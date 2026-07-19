// Exaggerated impact particle bursts (spec §19.1). Simple pooled point sprites.

import * as THREE from "three";

interface Burst {
  points: THREE.Points;
  velocities: Float32Array;
  life: number;
  ttl: number;
}

export class Effects {
  private bursts: Burst[] = [];
  constructor(private scene: THREE.Scene) {}

  spawn(pos: [number, number, number], color: number, strength: number): void {
    const count = Math.floor(20 + strength * 60);
    const positions = new Float32Array(count * 3);
    const velocities = new Float32Array(count * 3);
    for (let i = 0; i < count; i++) {
      positions[i * 3] = pos[0];
      positions[i * 3 + 1] = pos[1];
      positions[i * 3 + 2] = pos[2];
      const speed = 0.8 + Math.random() * (1.5 + strength * 2.5);
      const theta = Math.random() * Math.PI * 2;
      const phi = Math.acos(2 * Math.random() - 1);
      velocities[i * 3] = Math.sin(phi) * Math.cos(theta) * speed;
      velocities[i * 3 + 1] = Math.cos(phi) * speed;
      velocities[i * 3 + 2] = Math.sin(phi) * Math.sin(theta) * speed;
    }
    const geo = new THREE.BufferGeometry();
    geo.setAttribute("position", new THREE.BufferAttribute(positions, 3));
    const points = new THREE.Points(
      geo,
      new THREE.PointsMaterial({ color, size: 0.04, transparent: true, opacity: 1 }),
    );
    this.scene.add(points);
    this.bursts.push({ points, velocities, life: 0, ttl: 0.6 });
  }

  update(dt: number): void {
    for (let b = this.bursts.length - 1; b >= 0; b--) {
      const burst = this.bursts[b];
      burst.life += dt;
      const attr = burst.points.geometry.attributes.position as THREE.BufferAttribute;
      const arr = attr.array as Float32Array;
      for (let i = 0; i < arr.length; i += 3) {
        burst.velocities[i + 1] -= 3.5 * dt; // gravity
        arr[i] += burst.velocities[i] * dt;
        arr[i + 1] += burst.velocities[i + 1] * dt;
        arr[i + 2] += burst.velocities[i + 2] * dt;
      }
      attr.needsUpdate = true;
      (burst.points.material as THREE.PointsMaterial).opacity = Math.max(0, 1 - burst.life / burst.ttl);
      if (burst.life >= burst.ttl) {
        this.scene.remove(burst.points);
        this.bursts.splice(b, 1);
      }
    }
  }
}
