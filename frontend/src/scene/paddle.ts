// Player paddle: a proper table-tennis blade (see blade.ts) with red rubber
// facing the player. Haptic quadrants flash as glowing quarter-segments on the
// face; tracking health shows as a thin coloured ring around the blade edge.

import * as THREE from "three";
import { buildBlade } from "./blade";

const RUBBER_RED = 0xb51e2b;
const RUBBER_BLACK = 0x17171b;

export class PaddleView {
  readonly group = new THREE.Group();
  private quadrants: THREE.Mesh[] = [];
  private quadFlash = [0, 0, 0, 0];
  private statusRing: THREE.Mesh;
  private statusMat: THREE.MeshBasicMaterial;

  constructor() {
    // Local +Z faces the wall in play, so the player sees the -Z side: red
    // there for readability against the dark venue, black toward the wall.
    const { group, rx, ry } = buildBlade(RUBBER_BLACK, RUBBER_RED);
    this.group.add(group);

    // haptic quadrant flashes: quarter-ellipse glow segments at the impact's
    // paddle-local X/Y, on BOTH faces (the player sees the back). CircleGeometry
    // theta starts at +X and runs CCW: Q0 TL, Q1 TR, Q2 BL, Q3 BR.
    const quadTheta: number[] = [Math.PI / 2, 0, Math.PI, (3 * Math.PI) / 2];
    for (const theta of quadTheta) {
      for (const z of [0.009, -0.009]) {
        const mat = new THREE.MeshBasicMaterial({
          color: 0x00e0ff,
          transparent: true,
          opacity: 0,
          blending: THREE.AdditiveBlending,
          depthWrite: false,
          side: THREE.DoubleSide,
        });
        const q = new THREE.Mesh(new THREE.CircleGeometry(1, 24, theta, Math.PI / 2), mat);
        q.scale.set(rx * 0.94, ry * 0.94, 1); // fit the ellipse
        q.position.z = z;
        this.quadrants.push(q); // index i belongs to quadrant floor(i/2)
        this.group.add(q);
      }
    }

    // tracking status ring hugging the elliptical blade edge
    this.statusMat = new THREE.MeshBasicMaterial({ color: 0x00e0ff, transparent: true, opacity: 0.55 });
    this.statusRing = new THREE.Mesh(new THREE.TorusGeometry(1, 0.0032, 8, 56), this.statusMat);
    this.statusRing.scale.set(rx + 0.008, ry + 0.008, 1);
    this.group.add(this.statusRing);
  }

  flashQuadrant(localX: number, localY: number, strength: number): void {
    // localX/Y in [-1,1]; map to quadrant weights (matches backend §17.1)
    const x = (localX + 1) * 0.5;
    const y = 1 - (localY + 1) * 0.5;
    this.quadFlash[0] = Math.max(this.quadFlash[0], (1 - x) * (1 - y) * strength);
    this.quadFlash[1] = Math.max(this.quadFlash[1], x * (1 - y) * strength);
    this.quadFlash[2] = Math.max(this.quadFlash[2], (1 - x) * y * strength);
    this.quadFlash[3] = Math.max(this.quadFlash[3], x * y * strength);
  }

  setTracking(state: string, confidence: number): void {
    if (state === "LOST") {
      this.statusMat.color.setHex(0xff3355);
      this.statusMat.opacity = Math.floor(performance.now() / 200) % 2 === 0 ? 0.9 : 0.15;
    } else if (state === "DEGRADED") {
      this.statusMat.color.setHex(0xffaa00);
      this.statusMat.opacity = 0.7;
    } else {
      this.statusMat.color.setHex(0x00e0ff);
      this.statusMat.opacity = 0.2 + confidence * 0.35;
    }
  }

  update(dt: number): void {
    for (let q = 0; q < 4; q++) {
      this.quadFlash[q] = Math.max(0, this.quadFlash[q] - dt * 4.0);
    }
    for (let i = 0; i < this.quadrants.length; i++) {
      const q = Math.floor(i / 2);
      (this.quadrants[i].material as THREE.MeshBasicMaterial).opacity = this.quadFlash[q] * 0.85;
    }
  }
}
