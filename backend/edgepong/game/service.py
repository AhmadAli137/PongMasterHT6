"""Authoritative game service: the fixed-step loop (spec §16.1, §7.2 game_service).

Owns pose fusion, spawning, physics, swept collision, scoring, the state machine
and the haptic dispatcher. Runs on its own thread with a monotonic accumulator so
simulation never depends on render frame rate. Produces a thread-safe world
snapshot and a queue of impact events for the web layer to stream.

The backend is authoritative for ball state, collision, score, combo, impact
generation and haptic commands (spec §30). The frontend only interpolates.
"""

from __future__ import annotations

import collections
import random
import threading
import time
from dataclasses import dataclass
from typing import Callable, Deque

import numpy as np

from ..clock import now_us
from ..config import Config
from ..fusion.filter import PoseFusion
from ..logging_setup import get_logger
from ..mathutil import clamp, vec3
from ..types import (
    Ball,
    BallLifecycle,
    BallType,
    ImpactEvent,
    PaddlePose,
    TrackingState,
)
from .balance import BalanceSim
from .collision import build_collider, reflect_velocity, sweep_ball
from .difficulty import collision_scale
from .physics import (
    aim_shot,
    check_net_block,
    classify_quality,
    integrate_ball,
    is_out_of_play,
    predict_plane_crossing,
    step_table_bounce,
    swing_speed,
)
from .scoring import Scorer
from .spawner import BALL_RADIUS, Spawner
from .state_machine import GameState, GameStateMachine, Health

log = get_logger("game")


@dataclass
class HealthProvider:
    """Callables the service uses to learn hardware health each tick."""
    paddle_connected: Callable[[], bool]
    camera_ok: Callable[[], bool]


class GameService:
    TOSS_COUNTDOWN_S = 1.5   # hover time between toss click and the drop
    TOSS_HEIGHT_M = 0.45     # how far above the paddle the toss hovers
    AI_TOSS_DELAY_S = 1.2    # autoplay tosses on its own after this wait

    def __init__(
        self,
        cfg: Config,
        fusion: PoseFusion,
        health: HealthProvider,
        on_impact: Callable[[ImpactEvent], None],
        camera_poll: Callable[[], object] | None = None,
        imu_latest: Callable[[], object] | None = None,
        sim_model=None,
        on_balance_edge: Callable[[float, float], None] | None = None,
        on_rally: Callable[[dict], None] | None = None,
    ):
        self._cfg = cfg
        self._fusion = fusion
        self._health = health
        self._on_impact = on_impact
        self._camera_poll = camera_poll
        self._imu_latest = imu_latest
        self._sim_model = sim_model
        self._on_balance_edge = on_balance_edge
        self._on_rally = on_rally
        self._rng = random.Random(11)      # opponent return decisions
        self._opponent_swings = 0          # increments on serve + opponent return
        self._server = "OPPONENT"          # alternates each point
        self._toss_wait_s = 0.0            # AI auto-toss timer on player serve

        self._sm = GameStateMachine(cfg.game.countdown_s, cfg.game.round_duration_s)
        self._spawner = Spawner(cfg.game)
        self._scorer = Scorer()
        self._balance = BalanceSim(cfg.game.paddle_width_m * 0.5, cfg.game.paddle_height_m * 0.5)
        self.balance_mode = False

        self._balls: dict[int, Ball] = {}
        self._impacts: Deque[ImpactEvent] = collections.deque(maxlen=64)
        self._impact_id = 0

        self._pose = PaddlePose(timestamp_us=now_us())
        self._lock = threading.Lock()
        self._running = threading.Event()
        self._thread: threading.Thread | None = None

        self._last_imu_seq = -1
        self._metrics = {
            "physicsHz": 0.0,
            "lastImpactToCommandMs": 0.0,
        }

    # ------------------------------------------------------------------ #
    def start(self) -> None:
        self._running.set()
        self._thread = threading.Thread(target=self._loop, name="game", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._running.clear()
        if self._thread:
            self._thread.join(timeout=1.0)

    def command(self, command: str, value: str | None = None) -> None:
        cmd = command.upper()
        if cmd == "SET_DIFFICULTY" and value:
            self._cfg.game.difficulty = value.upper()
        elif cmd == "BALANCE_MODE":
            self.balance_mode = not self.balance_mode
            self._balance.reset()
            if self._sim_model is not None:
                self._sim_model.balance_mode = self.balance_mode
            log.info("balance mode %s", "ON" if self.balance_mode else "OFF")
        else:
            self._sm.request(cmd)
        # RESET always wipes; START_SESSION only when it was actually accepted —
        # a stray start press mid-round/countdown must never clear live state
        if cmd == "RESET":
            self._reset_round()
        elif cmd == "START_SESSION" and self._sm.state not in (
            GameState.PLAYING,
            GameState.COUNTDOWN,
            GameState.PAUSED,
        ):
            self._reset_round()

    def external_input(
        self,
        x: float | None = None,
        y: float | None = None,
        tilt_delta: float = 0.0,
        yaw_delta: float = 0.0,
        vx: float | None = None,
        vy: float | None = None,
        strike: bool = False,
        power: float | None = None,
        flip: bool = False,
    ) -> None:
        """Mouse input from the renderer, in normalized [-1, 1] screen coords.

        vx/vy are pointer velocities in normalized units/s; they map through
        the same screen→world scale as position so a real flick becomes real
        paddle velocity. Ignored in hardware mode — the paddle is the input.
        """
        if self._sim_model is None:
            return
        # on your serve, the strike click means "toss the ball", not "swing"
        if strike and self._player_serve_phase() == "AIM":
            self.request_toss()
            strike = False
        world_x = None if x is None else x * 0.75
        world_y = None if y is None else 1.15 + y * 0.55
        world_vx = None if vx is None else vx * 0.75
        world_vy = None if vy is None else vy * 0.55
        self._sim_model.set_external_input(
            x=world_x, y=world_y, tilt_delta=tilt_delta, yaw_delta=yaw_delta,
            vx=world_vx, vy=world_vy,
            strike=strike, strike_power=power, flip=flip,
        )

    def _player_serve_phase(self) -> str | None:
        """"AIM" (waiting for toss), "COUNTDOWN" (hovering), or None."""
        if self._server != "PLAYER" or not self._sm.is_playing:
            return None
        with self._lock:
            if not self._balls:
                return "AIM"
            if any(b.is_toss and b.freeze_s > 0.0 for b in self._balls.values()):
                return "COUNTDOWN"
        return None

    def request_toss(self) -> None:
        """Player serve: hover a ball above the paddle, drop it after a countdown."""
        with self._lock:
            if self._server != "PLAYER" or not self._sm.is_playing or self._balls:
                return
            self._spawn_toss_locked(self._pose)
            self._toss_wait_s = 0.0

    def _reset_round(self) -> None:
        with self._lock:
            self._balls.clear()
            self._spawner.reset()
            self._scorer.reset()
            self._server = "OPPONENT"
            self._toss_wait_s = 0.0

    # ------------------------------------------------------------------ #
    def _loop(self) -> None:
        rate = self._cfg.game.physics_rate_hz
        step = 1.0 / rate
        accumulator = 0.0
        prev = time.monotonic()
        hz_window = collections.deque(maxlen=rate)

        while self._running.is_set():
            now = time.monotonic()
            frame_dt = now - prev
            prev = now
            accumulator += min(frame_dt, 0.1)  # clamp to avoid spiral of death

            steps = 0
            while accumulator >= step and steps < 8:
                self._fixed_step(step)
                accumulator -= step
                steps += 1
                hz_window.append(step)

            if hz_window:
                self._metrics["physicsHz"] = round(len(hz_window) / sum(hz_window), 1)

            # sleep to roughly the physics period
            sleep_for = step - (time.monotonic() - now)
            if sleep_for > 0:
                time.sleep(sleep_for)

    def _fixed_step(self, dt: float) -> None:
        # 1) pump sensors into fusion
        if self._camera_poll is not None:
            obs = self._camera_poll()
            if obs is not None:
                self._fusion.on_camera(obs)  # type: ignore[arg-type]
        if self._imu_latest is not None:
            tel = self._imu_latest()
            if tel is not None and getattr(tel, "sequence", -1) != self._last_imu_seq:
                self._last_imu_seq = getattr(tel, "sequence", -1)
                self._fusion.on_imu(tel)  # type: ignore[arg-type]

        # 2) fused pose
        pose = self._fusion.step()

        # 3) health + state machine
        health = Health(
            paddle_connected=self._health.paddle_connected(),
            tag_visible=pose.tracking_state is not TrackingState.LOST,
            camera_ok=self._health.camera_ok(),
        )
        self._sm.tick(dt, health)

        # 4) gameplay — or the balance-mode demo when not in a round (spec §18)
        if self._sm.is_playing:
            self._step_gameplay(dt, pose)
        elif self._sim_model is not None and self._sim_model.serve_mode:
            self._sim_model.serve_mode = False  # round over: drop the stance
        if not self._sm.is_playing and self.balance_mode:
            event = self._balance.step(dt, pose.orientation)
            if event == "edge" and self._on_balance_edge is not None:
                self._on_balance_edge(
                    clamp(self._balance.lx / self._balance.half_w, -1.0, 1.0),
                    clamp(self._balance.ly / self._balance.half_h, -1.0, 1.0),
                )

        # 5) drive the sim ground-truth paddle (autoplay intercepts balls)
        if self._sim_model is not None:
            with self._lock:
                balls = list(self._balls.values())
            self._sim_model.update(dt, balls)

        with self._lock:
            self._pose = pose

    def _step_gameplay(self, dt: float, pose: PaddlePose) -> None:
        cfg = self._cfg.game
        scale = collision_scale(cfg)
        collider = build_collider(pose, cfg.paddle_width_m * 0.5, cfg.paddle_height_m * 0.5, scale)

        with self._lock:
            # serve: opponent's turn uses the spawner; player's turn waits for a
            # toss (the autoplay AI tosses on its own after a short wait)
            if self._server == "OPPONENT":
                for ball in self._spawner.update(dt, len(self._balls)):
                    ball.spawn_time_us = now_us()
                    self._balls[ball.id] = ball
                    self._opponent_swings += 1  # the serve stroke
            elif not self._balls:
                self._toss_wait_s += dt
                ai_driving = self._sim_model is not None and not self._sim_model.external_active
                if self._toss_wait_s >= self.AI_TOSS_DELAY_S and ai_driving:
                    self._toss_wait_s = 0.0
                    self._spawn_toss_locked(pose)

            # serve stance: face opens + strike brushes upward while our serve
            # is pending (aiming, hovering, or the toss still falling unhit)
            if self._sim_model is not None:
                self._sim_model.serve_mode = self._server == "PLAYER" and (
                    not self._balls
                    or any(b.is_toss for b in self._balls.values())
                )

            to_remove: list[int] = []
            removed_balls: list[Ball] = []
            for ball in self._balls.values():
                # hovering toss: frozen in the air until the countdown expires
                # (sim-dt driven so replays/tests behave identically)
                if ball.freeze_s > 0.0:
                    ball.freeze_s -= dt
                    ball.prev_position = ball.position.copy()
                    continue

                prev_pos = ball.position.copy()
                integrate_ball(ball, dt)

                # dead balls dribble realistically for a moment, then cull
                if ball.state in (BallLifecycle.MISSED, BallLifecycle.EXPIRED, BallLifecycle.HIT):
                    step_table_bounce(ball, cfg)
                    ball.linger_s -= dt
                    if is_out_of_play(ball, cfg) or ball.linger_s <= 0.0:
                        to_remove.append(ball.id)
                        removed_balls.append(ball)
                    continue

                # net first: it can kill either direction of travel
                if check_net_block(ball, cfg):
                    if ball.state is BallLifecycle.RETURNED:
                        self._scorer.register_fault()
                        self._emit_rally("NET", 0)
                    # (a served ball into the net is the opponent's fault — no penalty)
                    self._kill_ball(ball)
                    continue

                side = step_table_bounce(ball, cfg)

                if ball.state is BallLifecycle.APPROACHING:
                    self._step_approaching(ball, side, prev_pos, pose, collider, cfg, to_remove, dt)
                    if ball.id in to_remove and ball not in removed_balls:
                        removed_balls.append(ball)
                elif ball.state is BallLifecycle.RETURNED:
                    self._step_returned(ball, side, cfg, to_remove)
                    if ball.id in to_remove and ball not in removed_balls:
                        removed_balls.append(ball)

            for bid in to_remove:
                self._balls.pop(bid, None)
            if to_remove and not self._balls:
                # a whiffed toss (never hit — is_toss survives) is a re-serve
                whiff = any(b.is_toss for b in removed_balls)
                if not whiff:
                    self._server = "PLAYER" if self._server == "OPPONENT" else "OPPONENT"
                    self._spawner.delay_next(1.1)  # breather before the next serve
                self._toss_wait_s = 0.0

    def _spawn_toss_locked(self, pose: PaddlePose) -> None:
        """Create the hovering toss ball (caller holds the lock)."""
        pos = pose.position_m + vec3(0.0, self.TOSS_HEIGHT_M, 0.0)
        ball = Ball(
            id=self._spawner.take_id(),
            spawn_time_us=now_us(),
            position=pos.copy(),
            velocity=vec3(),
            radius=BALL_RADIUS,
            type=BallType.NORMAL,
            target_zone=vec3(
                float(pose.position_m[0]),
                float(pose.position_m[1]),
                self._cfg.game.paddle_plane_z_m,
            ),
            state=BallLifecycle.APPROACHING,
            prev_position=pos.copy(),
            last_hit_by="OPPONENT",
            is_toss=True,
            freeze_s=self.TOSS_COUNTDOWN_S,
        )
        self._balls[ball.id] = ball

    def _step_approaching(self, ball, side, prev_pos, pose, collider, cfg, to_remove, dt) -> None:
        # is_toss is cleared on the player's strike, so it means "still falling,
        # never hit" here — even if the rally later comes back this way
        untouched_toss = ball.is_toss

        # a toss that touches the table was fumbled: silent re-serve, no penalty
        if untouched_toss and side is not None:
            to_remove.append(ball.id)
            return
        # double bounce on your side = you never got to it
        if side == "player" and ball.bounces_player >= 2:
            self._scorer.register_miss()
            self._emit_rally("MISS", 0)
            self._kill_ball(ball)
            return

        # autoplay serves itself: swing when its own toss falls to paddle height
        if (
            untouched_toss
            and self._sim_model is not None
            and not self._sim_model.external_active
            and not self._sim_model.striking
            and ball.velocity[1] < -0.5
            and abs(float(ball.position[1]) - float(pose.position_m[1])) < 0.10
        ):
            self._sim_model.ai_strike(0.75)

        # sweep in paddle-relative motion: shifting the segment start by the
        # paddle's own displacement lets a forward lunge hit a ball that is
        # falling parallel to the face (e.g. the serve toss)
        contact = sweep_ball(
            prev_pos + collider.velocity * dt, ball.position, ball.radius, collider
        )
        if contact.hit:
            # a toss only connects with an actual swing — a passive paddle
            # letting the ball drop past it is a whiff, not a serve
            if untouched_toss and float(np.linalg.norm(collider.velocity)) < 1.0:
                return
            self._resolve_hit(ball, pose, collider, contact)
            return

        if is_out_of_play(ball, cfg):
            if untouched_toss:
                to_remove.append(ball.id)  # fumbled toss: re-serve quietly
                return
            self._scorer.register_miss()
            self._emit_rally("MISS", 0)
            ball.state = BallLifecycle.MISSED
            to_remove.append(ball.id)

    def _step_returned(self, ball, side, cfg, to_remove) -> None:
        if side == "opponent":
            if ball.bounces_opponent == 1:
                # your return landed in — points now, then the opponent decides
                delta = self._scorer.register_landed()
                self._emit_rally("IN", delta)
                ball.opponent_will_return = self._rng.random() < self._return_probability()
            else:
                # second bounce on their side: they never got to it
                delta = self._scorer.register_winner()
                self._emit_rally("WINNER", delta)
                self._kill_ball(ball)
            return
        if side == "player":
            # your return came down on your own half — didn't clear
            self._scorer.register_fault()
            self._emit_rally("OWN_SIDE", 0)
            self._kill_ball(ball)
            return

        # opponent executes their return when the ball reaches their zone
        if (
            ball.opponent_will_return
            and ball.velocity[2] < 0.0
            and ball.position[2] < 0.30
        ):
            self._opponent_return(ball)
            return

        if is_out_of_play(ball, cfg):
            if ball.bounces_opponent >= 1:
                # landed in, opponent let it fly past: clean winner
                delta = self._scorer.register_winner()
                self._emit_rally("WINNER", delta)
            else:
                # never touched their side: out
                self._scorer.register_fault()
                self._emit_rally("OUT", 0)
            ball.state = BallLifecycle.EXPIRED
            to_remove.append(ball.id)

    def _return_probability(self) -> float:
        d = self._cfg.game.difficulty.upper()
        return {"EASY": 0.5, "NORMAL": 0.7, "HARD": 0.85}.get(d, 0.7)

    def _aim_assist(self, ball, contact, outgoing):
        """Mild outgoing aim correction (spec §16.4).

        Blend the raw reflection toward a legal arc onto the opponent half so
        rallies flow; the paddle angle still steers placement through both the
        blend remainder and the target's dependence on the raw direction.
        """
        cfg = self._cfg.game
        assist = {"EASY": 0.65, "NORMAL": 0.45, "HARD": 0.25}.get(
            cfg.difficulty.upper(), 0.45
        )
        h = cfg.table_height_m + ball.radius
        # target follows where the raw reflection was heading laterally
        tx = clamp(float(ball.position[0] + outgoing[0] * 0.35), -0.55, 0.55)
        target = vec3(tx, h, 0.55)
        speed_h = max(1.5, float(np.hypot(outgoing[0], outgoing[2])))
        t_flight = clamp(float(ball.position[2] - 0.55) / speed_h, 0.5, 0.85)
        ideal = None
        for _ in range(3):
            ideal = aim_shot(ball.position, target, t_flight, cfg, min_net_clearance=0.06)
            if ideal is not None:
                break
            t_flight += 0.1
        if ideal is None:
            return outgoing
        return outgoing * (1.0 - assist) + ideal * assist

    def _opponent_return(self, ball) -> None:
        """The opponent plays the ball back: a fresh shot aimed at your half."""
        cfg = self._cfg.game
        h = cfg.table_height_m + ball.radius
        target = vec3(
            self._rng.uniform(-0.4, 0.4),
            h,
            self._rng.uniform(1.30, 1.55),
        )
        t_flight = self._rng.uniform(0.55, 0.75)
        vel = None
        for _ in range(5):
            vel = aim_shot(ball.position, target, t_flight, cfg)
            if vel is not None:
                break
            t_flight += 0.08
        if vel is None:  # pathological position: lob it high, net check waived
            t_flight = 0.95
            vel = aim_shot(ball.position, target, t_flight, cfg, min_net_clearance=-9.9)
        ball.velocity = vel
        ball.state = BallLifecycle.APPROACHING
        ball.last_hit_by = "OPPONENT"
        ball.bounces_player = 0
        ball.bounces_opponent = 0
        ball.opponent_will_return = None
        ball.spawn_time_us = now_us()  # restart the reaction timer
        ball.target_zone = predict_plane_crossing(
            ball.position, ball.velocity, ball.radius, cfg, cfg.paddle_plane_z_m
        )
        self._opponent_swings += 1

    def _kill_ball(self, ball) -> None:
        ball.state = BallLifecycle.EXPIRED
        ball.linger_s = 1.5

    def _emit_rally(self, outcome: str, score_delta: int) -> None:
        if self._on_rally is not None:
            self._on_rally({"type": "rally", "outcome": outcome, "scoreDelta": score_delta})

    def _resolve_hit(self, ball: Ball, pose: PaddlePose, collider, contact) -> None:
        cfg = self._cfg.game
        # the ball lives on: it returns toward the opponent by the face angle
        ball.state = BallLifecycle.RETURNED
        ball.last_hit_by = "PLAYER"
        ball.is_toss = False  # a struck toss is just a live rally ball now
        ball.bounces_player = 0
        ball.bounces_opponent = 0
        ball.opponent_will_return = None

        plane_dist = float(np.dot(contact.point - collider.center, collider.normal))
        quality = classify_quality(ball, cfg, plane_dist)

        rel_speed = float(np.linalg.norm(ball.velocity - contact.contact_velocity))
        strength = swing_speed(rel_speed, cfg)

        outgoing = reflect_velocity(ball.velocity, contact.contact_velocity, collider.normal)
        outgoing = self._aim_assist(ball, contact, outgoing)
        # nudge the contact point to just outside the paddle face so the very
        # next sweep can't immediately re-collide with the same surface
        ball.position = contact.point + collider.normal * (
            ball.radius + 0.002
        ) * (1.0 if float(np.dot(outgoing, collider.normal)) >= 0.0 else -1.0)
        ball.prev_position = ball.position.copy()
        ball.velocity = outgoing

        # stance-based when the player is driving with the mouse; otherwise the
        # crude contact-side heuristic (hardware mode has no stance signal yet)
        if self._sim_model is not None and self._sim_model.external_active:
            is_backhand = self._sim_model.backhand
        else:
            is_backhand = contact.local_x < -0.25
        reaction_ms = (now_us() - ball.spawn_time_us) / 1000.0

        score_delta = self._scorer.score_hit(
            quality, ball.type, strength, reaction_ms, is_backhand
        )

        self._impact_id += 1
        impact = ImpactEvent(
            id=self._impact_id,
            ball_id=ball.id,
            timestamp_us=now_us(),
            position_m=contact.point.copy(),
            paddle_local_x=contact.local_x,
            paddle_local_y=contact.local_y,
            strength=strength,
            quality=quality,
            outgoing_velocity_mps=outgoing.copy(),
            score_delta=score_delta,
            ball_type=ball.type,
        )
        self._impacts.append(impact)
        t0 = now_us()
        self._on_impact(impact)  # sends haptic command (spec: authoritative path)
        self._metrics["lastImpactToCommandMs"] = round((now_us() - t0) / 1000.0, 3)

    # ------------------------------------------------------------------ #
    def snapshot(self) -> dict:
        with self._lock:
            pose = self._pose
            balls = [
                {
                    "id": b.id,
                    "position": [float(b.position[0]), float(b.position[1]), float(b.position[2])],
                    "velocity": [float(b.velocity[0]), float(b.velocity[1]), float(b.velocity[2])],
                    "radius": b.radius,
                    "type": b.type.value,
                    "state": b.state.value,
                }
                for b in self._balls.values()
            ]
            # balance-mode ball rides the paddle whenever a round is not running
            if self.balance_mode and self._balance.on_paddle and not self._sm.is_playing:
                wp = self._balance.world_position(pose)
                balls.append(
                    {
                        "id": 0,
                        "position": [float(wp[0]), float(wp[1]), float(wp[2])],
                        "velocity": [0.0, 0.0, 0.0],
                        "radius": self._balance.radius,
                        "type": "NORMAL",
                        "state": "BALANCE",
                    }
                )
            stats = self._scorer.stats
            return {
                "type": "state",
                "serverTimeUs": now_us(),
                "gameState": self._sm.state.value,
                "countdown": round(self._sm.countdown_remaining_s, 2),
                "remainingMs": self._sm.round_remaining_ms,
                "balanceMode": self.balance_mode,
                "calibrationProgress": round(self._sm.calibration_progress, 3),
                "serve": (
                    self._spawner.serve_preview()
                    if self._sm.is_playing and self._server == "OPPONENT"
                    else None
                ),
                "server": self._server,
                "playerServe": self._player_serve_snapshot_locked(),
                "opponentSwing": self._opponent_swings,
                "controlMode": (
                    "MOUSE"
                    if self._sim_model is not None and self._sim_model.external_active
                    else "AUTO" if self._sim_model is not None else "HARDWARE"
                ),
                "stance": (
                    "BACKHAND"
                    if self._sim_model is not None and self._sim_model.backhand
                    else "FOREHAND"
                ),
                "paddle": {
                    "position": [
                        float(pose.position_m[0]),
                        float(pose.position_m[1]),
                        float(pose.position_m[2]),
                    ],
                    "quaternion": [
                        float(pose.orientation[0]),
                        float(pose.orientation[1]),
                        float(pose.orientation[2]),
                        float(pose.orientation[3]),
                    ],
                    "confidence": round(pose.confidence, 3),
                    "trackingState": pose.tracking_state.value,
                },
                "balls": balls,
                "score": stats.score,
                "combo": stats.combo,
                "bestCombo": stats.best_combo,
                "accuracy": round(stats.accuracy(), 3),
            }

    def _player_serve_snapshot_locked(self) -> dict | None:
        """Player-serve phase for the HUD (caller holds the lock)."""
        if self._server != "PLAYER" or not self._sm.is_playing:
            return None
        for b in self._balls.values():
            if b.is_toss and b.freeze_s > 0.0:
                return {"phase": "COUNTDOWN", "dropInMs": int(b.freeze_s * 1000)}
        if not self._balls:
            return {"phase": "AIM", "dropInMs": 0}
        return None

    def results(self) -> dict:
        with self._lock:
            s = self._scorer.stats
            return {
                "score": s.score,
                "accuracy": round(s.accuracy(), 3),
                "bestCombo": s.best_combo,
                "avgReactionMs": round(s.avg_reaction_ms(), 1),
                "strongestSwing": round(s.strongest_swing, 3),
                "forehandHits": s.forehand_hits,
                "backhandHits": s.backhand_hits,
                "perfects": s.perfects,
            }

    def metrics(self) -> dict:
        return dict(self._metrics)

    @property
    def state(self) -> GameState:
        return self._sm.state
