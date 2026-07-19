// Ball pool with colour/glow per type, motion trails, and a spin-curved
// predicted-trajectory line (mirrors the backend gravity + Magnus model).

import * as THREE from "three";
import type { BallMsg, BallType } from "../types";

const TYPE_COLOR: Record<BallType, number> = {
  NORMAL: 0xffffff,
  SMASH: 0xff5522,
  BACKHAND: 0x22ff99,
  AVOID: 0xff2266,
};

// must match backend physics.py so the drawn arc matches the real flight
const GRAV = -6.0;
const MAGNUS_K = 0.045;
const TRAJ_STEP = 0.025; // s per integration step
const TRAJ_LEN = 28;     // ~0.7 s look-ahead
const TABLE_Y = 0.72;    // stop the arc roughly where it would bounce

class BallMesh {
  mesh: THREE.Mesh;
  trail: THREE.Points;
  traj: THREE.Points;
  private trailPositions: Float32Array;
  private trailHead = 0;
  private readonly trailLen = 14;
  private trajPositions = new Float32Array(TRAJ_LEN * 3);
  private trajColors = new Float32Array(TRAJ_LEN * 3);

  constructor() {
    // matte celluloid ball; a faint emissive tint keeps the type colour
    // readable in flight without looking like a neon orb
    this.mesh = new THREE.Mesh(
      new THREE.SphereGeometry(1, 24, 24),
      new THREE.MeshStandardMaterial({
        color: 0xffffff,
        emissive: 0xffffff,
        emissiveIntensity: 0.22,
        roughness: 0.4,
        metalness: 0.0,
      }),
    );
    this.mesh.castShadow = true;
    this.trailPositions = new Float32Array(this.trailLen * 3);
    const geo = new THREE.BufferGeometry();
    geo.setAttribute("position", new THREE.BufferAttribute(this.trailPositions, 3));
    this.trail = new THREE.Points(
      geo,
      new THREE.PointsMaterial({ color: 0xffffff, size: 0.022, transparent: true, opacity: 0.35 }),
    );

    // predicted flight path: a beam of glowing dots, bright at the ball and
    // fading out ahead. Points (not a line) so it has real, GPU-reliable width.
    const tgeo = new THREE.BufferGeometry();
    tgeo.setAttribute("position", new THREE.BufferAttribute(this.trajPositions, 3));
    tgeo.setAttribute("color", new THREE.BufferAttribute(this.trajColors, 3));
    tgeo.setDrawRange(0, 0);
    this.traj = new THREE.Points(
      tgeo,
      new THREE.PointsMaterial({
        vertexColors: true,
        size: 0.05,
        sizeAttenuation: true,
        transparent: true,
        opacity: 0.95,
        blending: THREE.AdditiveBlending,
        depthWrite: false,
      }),
    );
    this.traj.visible = false;
    this.traj.frustumCulled = false;
  }

  set(ball: BallMsg): void {
    const color = TYPE_COLOR[ball.type];
    const m = this.mesh.material as THREE.MeshStandardMaterial;
    m.color.setHex(color);
    m.emissive.setHex(color);
    (this.trail.material as THREE.PointsMaterial).color.setHex(color);
    this.mesh.scale.setScalar(ball.radius);
    this.mesh.position.set(ball.position[0], ball.position[1], ball.position[2]);
    // push into ring-buffer trail
    const i = this.trailHead * 3;
    this.trailPositions[i] = ball.position[0];
    this.trailPositions[i + 1] = ball.position[1];
    this.trailPositions[i + 2] = ball.position[2];
    this.trailHead = (this.trailHead + 1) % this.trailLen;
    this.trail.geometry.attributes.position.needsUpdate = true;

    this.updateTrajectory(ball, color);
  }

  /** Forward-integrate gravity + Magnus to draw where the spin will send it. */
  private updateTrajectory(ball: BallMsg, color: number): void {
    const spin = ball.spin;
    const flyingToOpponent = ball.state === "RETURNED";
    if (!flyingToOpponent || !spin) {
      this.traj.visible = false;
      return;
    }
    const c = new THREE.Color(color);
    const p: [number, number, number] = [ball.position[0], ball.position[1], ball.position[2]];
    const v: [number, number, number] = [ball.velocity[0], ball.velocity[1], ball.velocity[2]];
    let n = 0;
    for (let k = 0; k < TRAJ_LEN; k++) {
      const idx = k * 3;
      this.trajPositions[idx] = p[0];
      this.trajPositions[idx + 1] = p[1];
      this.trajPositions[idx + 2] = p[2];
      const fade = 1 - 0.72 * (k / TRAJ_LEN); // brightest at the ball, dims ahead
      this.trajColors[idx] = c.r * fade;
      this.trajColors[idx + 1] = c.g * fade;
      this.trajColors[idx + 2] = c.b * fade;
      n++;
      // Magnus acceleration = MAGNUS_K * (spin × velocity)
      const ax = MAGNUS_K * (spin[1] * v[2] - spin[2] * v[1]);
      const ay = GRAV + MAGNUS_K * (spin[2] * v[0] - spin[0] * v[2]);
      const az = MAGNUS_K * (spin[0] * v[1] - spin[1] * v[0]);
      v[0] += ax * TRAJ_STEP;
      v[1] += ay * TRAJ_STEP;
      v[2] += az * TRAJ_STEP;
      p[0] += v[0] * TRAJ_STEP;
      p[1] += v[1] * TRAJ_STEP;
      p[2] += v[2] * TRAJ_STEP;
      if (p[1] < TABLE_Y && v[1] < 0) break; // would bounce on the table
    }
    if (n < 2) {
      this.traj.visible = false;
      return;
    }
    this.traj.geometry.setDrawRange(0, n);
    this.traj.geometry.attributes.position.needsUpdate = true;
    this.traj.geometry.attributes.color.needsUpdate = true;
    this.traj.visible = true;
  }
}

export class BallField {
  private pool = new Map<number, BallMesh>();
  constructor(private scene: THREE.Scene) {}

  update(balls: BallMsg[]): void {
    const seen = new Set<number>();
    for (const b of balls) {
      seen.add(b.id);
      let bm = this.pool.get(b.id);
      if (!bm) {
        bm = new BallMesh();
        this.scene.add(bm.mesh);
        this.scene.add(bm.trail);
        this.scene.add(bm.traj);
        this.pool.set(b.id, bm);
      }
      bm.set(b);
    }
    for (const [id, bm] of this.pool) {
      if (!seen.has(id)) {
        this.scene.remove(bm.mesh);
        this.scene.remove(bm.trail);
        this.scene.remove(bm.traj);
        this.pool.delete(id);
      }
    }
  }
}
