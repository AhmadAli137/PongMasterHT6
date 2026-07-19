// AI opponent paddle on the far side of the table. It telegraphs each serve:
// glides to the upcoming serve origin (from the backend's serve preview),
// swings when the ball actually launches, then drifts back to a home hover.
// Blue rubber (tournament-legal since 2021) so it reads instantly as "not you".

import * as THREE from "three";
import { buildBlade } from "./blade";
import type { BallMsg, ServeMsg } from "../types";

const RUBBER_BLUE = 0x2b66cc;
const RUBBER_BLACK = 0x17171b;

export class OpponentView {
  readonly group = new THREE.Group();
  private blade = new THREE.Group();
  private home = new THREE.Vector3(0, 1.15, -0.12);
  private target = new THREE.Vector3().copy(this.home);
  private swingT = -1; // <0 = idle
  private lastSwingCount = -1;
  private phase = 0;

  constructor() {
    // blue rubber faces the player (+Z), black toward the far wall
    const { group } = buildBlade(RUBBER_BLUE, RUBBER_BLACK, 0.09);
    this.blade.add(group);
    this.group.add(this.blade);
    this.group.position.copy(this.home);
  }

  update(
    dt: number,
    serve: ServeMsg | null | undefined,
    balls: BallMsg[],
    swingCount: number,
  ): void {
    this.phase += dt;

    // an incoming return takes priority: shadow its x like a real receiver
    const incoming = balls.find((b) => b.state === "RETURNED");
    if (incoming) {
      const x = Math.max(-0.8, Math.min(0.8, incoming.position[0]));
      this.target.set(x, 1.05, -0.08);
    } else if (serve) {
      // telegraph: glide toward where the next serve will come from
      this.target.set(serve.position[0], serve.position[1], serve.position[2] - 0.06);
    } else {
      this.target.copy(this.home);
    }

    // backend counts every opponent stroke (serve + rally return)
    if (swingCount !== this.lastSwingCount) {
      if (this.lastSwingCount >= 0) this.swingT = 0;
      this.lastSwingCount = swingCount;
    }

    // damped glide + a little lively hover bob
    const k = 1 - Math.exp(-5.5 * dt);
    this.group.position.lerp(this.target, k);
    this.group.position.y += Math.sin(this.phase * 1.7) * 0.0035;

    // serve swing: sharp forward flick that eases back
    if (this.swingT >= 0) {
      this.swingT += dt;
      const T = 0.28;
      if (this.swingT >= T) {
        this.swingT = -1;
        this.blade.rotation.x = 0;
      } else {
        this.blade.rotation.x = -Math.sin((Math.PI * this.swingT) / T) * 0.9;
      }
    } else {
      // idle: slight forehand-ready tilt
      this.blade.rotation.x = 0.12 + Math.sin(this.phase * 1.1) * 0.04;
    }
  }
}
