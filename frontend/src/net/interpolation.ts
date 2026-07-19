// Smooth rendering interpolation between the two latest backend states (spec §13.4).
// The backend is authoritative; the frontend only interpolates/limited-extrapolates.

import * as THREE from "three";
import type { StateMsg } from "../types";

export class PaddleInterpolator {
  private prev: StateMsg | null = null;
  private curr: StateMsg | null = null;
  private prevArrivalMs = 0;
  private currArrivalMs = 0;

  push(state: StateMsg, nowMs: number): void {
    this.prev = this.curr;
    this.prevArrivalMs = this.currArrivalMs;
    this.curr = state;
    this.currArrivalMs = nowMs;
  }

  /** Fill target position/quaternion for the render frame at nowMs. */
  sample(nowMs: number, outPos: THREE.Vector3, outQuat: THREE.Quaternion): boolean {
    if (!this.curr) return false;
    if (!this.prev) {
      applyPaddle(this.curr, outPos, outQuat);
      return true;
    }
    const span = Math.max(1, this.currArrivalMs - this.prevArrivalMs);
    // small render delay for smoothness; clamp extrapolation to avoid overshoot
    let t = (nowMs - this.currArrivalMs) / span + 1.0;
    t = Math.min(1.25, Math.max(0, t));

    const p = this.prev.paddle;
    const c = this.curr.paddle;
    outPos.set(
      lerp(p.position[0], c.position[0], t),
      lerp(p.position[1], c.position[1], t),
      lerp(p.position[2], c.position[2], t),
    );
    const qp = new THREE.Quaternion(p.quaternion[1], p.quaternion[2], p.quaternion[3], p.quaternion[0]);
    const qc = new THREE.Quaternion(c.quaternion[1], c.quaternion[2], c.quaternion[3], c.quaternion[0]);
    outQuat.copy(qp).slerp(qc, Math.min(1, t));
    return true;
  }
}

function applyPaddle(s: StateMsg, outPos: THREE.Vector3, outQuat: THREE.Quaternion): void {
  const q = s.paddle.quaternion;
  outPos.set(s.paddle.position[0], s.paddle.position[1], s.paddle.position[2]);
  outQuat.set(q[1], q[2], q[3], q[0]);
}

function lerp(a: number, b: number, t: number): number {
  return a + (b - a) * t;
}
