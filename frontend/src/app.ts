// Top-level app: renderer, scene graph, network wiring, render loop (spec §19.3).
// The backend is authoritative; here we only interpolate + render + play FX.

import * as THREE from "three";
import { AudioManager } from "./audio/audioManager";
import { PaddleInterpolator } from "./net/interpolation";
import { WebSocketClient } from "./net/websocketClient";
import { buildArena } from "./scene/arena";
import { BallField } from "./scene/ball";
import { Effects } from "./scene/effects";
import { OpponentView } from "./scene/opponent";
import { PaddleView } from "./scene/paddle";
import { Hud } from "./ui/hud";
import type { ImpactMsg, StateMsg } from "./types";

const TYPE_COLOR: Record<string, number> = {
  NORMAL: 0xffffff, SMASH: 0xff5522, BACKHAND: 0x22ff99, AVOID: 0xff2266,
};

export class App {
  private renderer: THREE.WebGLRenderer;
  private scene = new THREE.Scene();
  private camera: THREE.PerspectiveCamera;
  private clock = new THREE.Clock();

  private paddle = new PaddleView();
  private opponent = new OpponentView();
  private balls: BallField;
  private effects: Effects;
  private interp = new PaddleInterpolator();
  private hud: Hud;
  private audio = new AudioManager();
  private net = new WebSocketClient();

  private latest: StateMsg | null = null;
  private lastState = "";
  // damped-follow targets: interpolator output is low-passed before display
  private targetPos = new THREE.Vector3(0, 1.2, 1.8);
  private targetQuat = new THREE.Quaternion();
  private paddleSnapped = false;

  constructor(container: HTMLElement) {
    this.renderer = new THREE.WebGLRenderer({ antialias: true, powerPreference: "high-performance" });
    this.renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
    this.renderer.setSize(window.innerWidth, window.innerHeight);
    this.renderer.shadowMap.enabled = true;
    this.renderer.shadowMap.type = THREE.PCFSoftShadowMap;
    this.renderer.toneMapping = THREE.ACESFilmicToneMapping;
    this.renderer.toneMappingExposure = 1.05;
    container.appendChild(this.renderer.domElement);

    this.camera = new THREE.PerspectiveCamera(58, window.innerWidth / window.innerHeight, 0.05, 40);
    // third-person: behind and above your paddle, following it (see frame())
    this.camera.position.set(0, 1.5, 3.1);
    this.camera.lookAt(0, 0.92, 0.4);

    buildArena(this.scene);
    this.scene.add(this.paddle.group);
    this.scene.add(this.opponent.group);
    this.balls = new BallField(this.scene);
    this.effects = new Effects(this.scene);
    this.hud = new Hud(container);

    this.wireNetwork();
    this.wireInput();
    window.addEventListener("resize", () => this.onResize());
    this.pollMetrics();
  }

  private wireNetwork(): void {
    this.net.onStatus((c) => this.hud.setConnected(c));
    this.net.onState((s) => {
      this.latest = s;
      this.interp.push(s, performance.now());
      if (s.gameState === "COUNTDOWN" && s.gameState !== this.lastState) this.audio.countdown(Math.ceil(s.countdown));
      this.lastState = s.gameState;
    });
    this.net.onImpact((i) => this.onImpact(i));
    this.net.onRally((r) => {
      this.hud.onRally(r);
      if (r.outcome === "WINNER") this.audio.impact("PERFECT", 0.9, 0);
      else if (r.outcome !== "IN") this.audio.miss();
    });
    this.net.connect();
  }

  private wireInput(): void {
    const start = () => {
      this.audio.resume();
      this.net.send("START_SESSION");
    };
    window.addEventListener("keydown", (e) => {
      if (e.code === "Space") { e.preventDefault(); start(); }
      if (e.code === "Escape") this.net.send("PAUSE");
      if (e.key === "1") this.net.send("SET_DIFFICULTY", "EASY");
      if (e.key === "2") this.net.send("SET_DIFFICULTY", "NORMAL");
      if (e.key === "3") this.net.send("SET_DIFFICULTY", "HARD");
      if (e.key === "r" || e.key === "R") this.net.send("RESET");
      if (e.key === "m" || e.key === "M") this.audio.setMuted(!this.audio.muted);
      if (e.key === "b" || e.key === "B") this.net.send("BALANCE_MODE");
    });

    // -- mouse paddle control (sim mode) --------------------------------- //
    // move = arm position + swing velocity (a fast flick IS the power),
    // right-drag = wrist (vertical opens/closes the face, horizontal twists
    // to aim), quick right-click = forehand/backhand flip, wheel = coarse
    // face tilt, left click = strike lunge whose power comes from how fast
    // the hand is moving at that instant.
    let lastMove = 0;
    let lastX = 0, lastY = 0, lastT = 0;
    let velX = 0, velY = 0; // EMA of pointer velocity, normalized units/s
    let rightDragPx = 0;

    window.addEventListener("pointermove", (e) => {
      const now = performance.now();
      const nx = (e.clientX / window.innerWidth) * 2 - 1;
      const ny = -((e.clientY / window.innerHeight) * 2 - 1);
      if (lastT > 0) {
        const dt = Math.max(4, now - lastT) / 1000;
        // smooth the instantaneous velocity so single-event spikes don't ring
        velX = velX * 0.55 + ((nx - lastX) / dt) * 0.45;
        velY = velY * 0.55 + ((ny - lastY) / dt) * 0.45;
      }
      lastX = nx; lastY = ny; lastT = now;

      // right-button drag = wrist rotation, not arm movement
      if (e.buttons & 2) {
        rightDragPx += Math.abs(e.movementX) + Math.abs(e.movementY);
        this.net.sendInput({
          tilt: -e.movementY * 0.004,  // drag up = open face (like wheel up)
          yaw: -e.movementX * 0.004,   // drag right = aim right
        });
        return;
      }

      if (now - lastMove < 16) return; // ~60 Hz cap
      lastMove = now;
      this.net.sendInput({ x: nx, y: ny, vx: velX, vy: velY });
    });

    window.addEventListener("wheel", (e) => {
      // ~17° per notch; backend clamps total tilt to ±69°
      this.net.sendInput({ tilt: -e.deltaY * 0.003 });
    }, { passive: true });

    // right button: drag = wrist; a clean click (no drag) flips the stance
    window.addEventListener("pointerdown", (e) => {
      if (e.button === 2) rightDragPx = 0;
    });
    window.addEventListener("contextmenu", (e) => {
      e.preventDefault();
      if (rightDragPx < 8) this.net.sendInput({ flip: true });
      rightDragPx = 0;
    });

    // left click: strike — power is your swing speed at this moment
    window.addEventListener("pointerdown", (e) => {
      if (e.button !== 0) return;
      this.audio.resume();
      const g = this.latest?.gameState;
      if (g === "PLAYING" || g === "COUNTDOWN" || this.latest?.balanceMode) {
        this.net.sendInput({ strike: true, vx: velX, vy: velY });
      } else if (g !== "PAUSED") {
        // clicking while paused does nothing — resume is deliberate (ESC)
        this.net.send("START_SESSION");
      }
    });
  }

  private onImpact(i: ImpactMsg): void {
    const color = TYPE_COLOR[i.ballType] ?? 0xffffff;
    this.effects.spawn(i.position, color, i.strength);
    this.paddle.flashQuadrant(i.localX, i.localY, Math.max(0.4, i.strength));
    this.hud.onImpact(i);
    if (i.ballType === "AVOID") this.audio.miss();
    else this.audio.impact(i.quality, i.strength, i.localX * 0.6);
  }

  private async pollMetrics(): Promise<void> {
    setInterval(async () => {
      try {
        const r = await fetch("/api/metrics");
        if (r.ok) this.hud.updateDiagnostics(await r.json());
      } catch { /* offline; ignore */ }
    }, 500);
  }

  private onResize(): void {
    this.camera.aspect = window.innerWidth / window.innerHeight;
    this.camera.updateProjectionMatrix();
    this.renderer.setSize(window.innerWidth, window.innerHeight);
  }

  start(): void {
    this.renderer.setAnimationLoop(() => this.frame());
  }

  private frame(): void {
    const dt = Math.min(0.05, this.clock.getDelta());
    const now = performance.now();

    if (this.interp.sample(now, this.targetPos, this.targetQuat)) {
      if (!this.paddleSnapped) {
        // first sample: jump straight there instead of gliding in from origin
        this.paddle.group.position.copy(this.targetPos);
        this.paddle.group.quaternion.copy(this.targetQuat);
        this.paddleSnapped = true;
      } else {
        // critically-damped follow: kills residual sensor jitter for ~20 ms
        // of added visual lag (frame-rate independent)
        const k = 1 - Math.exp(-22 * dt);
        this.paddle.group.position.lerp(this.targetPos, k);
        this.paddle.group.quaternion.slerp(this.targetQuat, k);
      }
    }
    if (this.latest) {
      this.balls.update(this.latest.balls);
      this.paddle.setTracking(this.latest.paddle.trackingState, this.latest.paddle.confidence);
      this.opponent.update(dt, this.latest.serve, this.latest.balls, this.latest.opponentSwing ?? 0);
      this.hud.update(this.latest);
    }
    this.paddle.update(dt);
    this.effects.update(dt);

    // third-person follow: the camera trails your paddle laterally/vertically
    const pp = this.paddle.group.position;
    const kCam = 1 - Math.exp(-6 * dt);
    this.camera.position.lerp(
      new THREE.Vector3(pp.x * 0.55, 1.42 + (pp.y - 1.1) * 0.25, 3.1),
      kCam,
    );
    this.camera.lookAt(pp.x * 0.3, 0.92, 0.4);

    this.renderer.render(this.scene, this.camera);
  }
}
