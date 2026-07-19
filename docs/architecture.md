# Architecture

## Ownership rule

Each resource has exactly one owner (spec §4.2). In this implementation:

| Resource | Owner |
|---|---|
| Camera / AprilTag | `edgepong.camera` (real) or `MockTagDetector` (sim) |
| Paddle IMU / haptics / RGB | ESP32 firmware (real) or `MockPaddle` (sim) |
| Pose fusion | `edgepong.fusion.filter.PoseFusion` |
| Ball state, collision, score, impacts, haptic commands | `edgepong.game.service.GameService` (**authoritative**) |
| Rendering | Three.js frontend (interpolates only) |
| Transport | UDP (paddle) + WebSocket (renderer) |

The frontend never owns game state and never issues haptic commands — those go
straight from the game service over UDP so the browser is never in the haptic
latency path (spec §6.1).

## Data flow (one physics step, `GameService._fixed_step`)

```
MockTagDetector.poll() ─▶ PoseFusion.on_camera()
PaddleGateway.latest()  ─▶ PoseFusion.on_imu()
                            │
                            ▼
                    PoseFusion.step()  → PaddlePose (predicted, confidence)
                            │
        ┌───────────────────┼───────────────────────────┐
        ▼                   ▼                           ▼
 GameStateMachine     swept collision            SimPaddleModel.update()
   .tick(health)      (build_collider,           (autoplay intercepts balls;
                       sweep_ball)                 feeds the mocks next tick)
                            │
                    ImpactEvent (exactly one per ball)
                            │
              ┌─────────────┴─────────────┐
              ▼                           ▼
    HapticDispatcher.on_impact()   WebServer.push_impact()
    (UDP → paddle, authoritative)  (WS → renderer: audio/particles)
```

The loop uses a monotonic accumulator at `game.physics_rate_hz` (240 Hz) and is
independent of the render frame rate (spec §16.1).

## Simulation closes the loop

`SimPaddleModel` holds a single ground-truth paddle pose. The mock camera and
mock IMU both derive their (noisy, delayed) observations from it, and fusion
recombines them — so the sim exercises the *real* fusion/collision/haptic code,
not a bypass. In autoplay the model steers the paddle to intercept the nearest
approaching ball, producing real hits headlessly.

## Process model

For the vertical slice the services run as threads in one process
(`Application` in `main.py`): the UDP RX thread, the mock paddle TX thread, the
game loop thread, and the asyncio web server. The interfaces (poll/latest/
snapshot callables) are already split so they can move to separate processes
later per spec §7.2 without touching the game logic.

## Tracking states

`PoseFusion` publishes `GOOD` / `DEGRADED` / `LOST` from camera age +
confidence (spec §11.2). On short tag loss it keeps predicting from gyro +
last linear velocity up to `paddle.max_prediction_ms`, then freezes — the
renderer blinks/fades the paddle accordingly.
