// Ball pool with colour/glow per type and motion trails.

import * as THREE from "three";
import type { BallMsg, BallType } from "../types";

const TYPE_COLOR: Record<BallType, number> = {
  NORMAL: 0xffffff,
  SMASH: 0xff5522,
  BACKHAND: 0x22ff99,
  AVOID: 0xff2266,
};

class BallMesh {
  mesh: THREE.Mesh;
  trail: THREE.Points;
  private trailPositions: Float32Array;
  private trailHead = 0;
  private readonly trailLen = 14;

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
        this.pool.set(b.id, bm);
      }
      bm.set(b);
    }
    for (const [id, bm] of this.pool) {
      if (!seen.has(id)) {
        this.scene.remove(bm.mesh);
        this.scene.remove(bm.trail);
        this.pool.delete(id);
      }
    }
  }
}
