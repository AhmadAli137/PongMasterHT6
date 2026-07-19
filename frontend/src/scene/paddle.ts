// Realistic table-tennis paddle: wood core, red/black rubber faces, flared
// handle. Haptic quadrants flash as glowing quarter-segments on the face, and
// tracking health shows as a thin status ring around the blade edge.

import * as THREE from "three";

const WOOD = 0xb9895a;
const WOOD_DARK = 0x8a6238;
const RUBBER_RED = 0xb51e2b;
const RUBBER_BLACK = 0x17171b;

export class PaddleView {
  readonly group = new THREE.Group();
  private quadrants: THREE.Mesh[] = [];
  private quadFlash = [0, 0, 0, 0];
  private statusRing: THREE.Mesh;
  private statusMat: THREE.MeshBasicMaterial;

  constructor(widthM = 0.19, heightM = 0.19) {
    const r = widthM / 2; // blade radius
    void heightM;

    const woodMat = new THREE.MeshStandardMaterial({ color: WOOD, roughness: 0.65, metalness: 0.0 });
    const woodDarkMat = new THREE.MeshStandardMaterial({ color: WOOD_DARK, roughness: 0.7, metalness: 0.0 });
    const redMat = new THREE.MeshStandardMaterial({ color: RUBBER_RED, roughness: 0.85, metalness: 0.0 });
    const blackMat = new THREE.MeshStandardMaterial({ color: RUBBER_BLACK, roughness: 0.9, metalness: 0.0 });

    // blade sandwich: wood core with rubber sheet on each face.
    // Cylinders are Y-axis aligned; rotate X by 90° so the flat faces point ±Z
    // (the paddle frame's face normal is +Z, matching the backend). Local +Z
    // faces the WALL in play, so the player sees the -Z side: red goes there
    // for readability against the dark venue.
    const core = new THREE.Mesh(new THREE.CylinderGeometry(r, r, 0.006, 48), woodMat);
    core.rotation.x = Math.PI / 2;
    const front = new THREE.Mesh(new THREE.CylinderGeometry(r * 0.985, r * 0.985, 0.0038, 48), blackMat);
    front.rotation.x = Math.PI / 2;
    front.position.z = 0.0049;
    const back = new THREE.Mesh(new THREE.CylinderGeometry(r * 0.985, r * 0.985, 0.0038, 48), redMat);
    back.rotation.x = Math.PI / 2;
    back.position.z = -0.0049;
    for (const m of [core, front, back]) {
      m.castShadow = true;
      this.group.add(m);
    }
    // white edge tape around the blade rim, like a real paddle
    const tape = new THREE.Mesh(
      new THREE.TorusGeometry(r, 0.0042, 8, 48),
      new THREE.MeshStandardMaterial({ color: 0xf0ede6, roughness: 0.7 }),
    );
    this.group.add(tape);

    // handle: tapered shaft with a flared butt cap, overlapping the blade edge
    const shaft = new THREE.Mesh(new THREE.BoxGeometry(0.026, 0.105, 0.021), woodMat);
    shaft.position.set(0, -r - 0.042, 0);
    shaft.castShadow = true;
    const flare = new THREE.Mesh(new THREE.BoxGeometry(0.034, 0.02, 0.024), woodDarkMat);
    flare.position.set(0, -r - 0.098, 0);
    flare.castShadow = true;
    const collar = new THREE.Mesh(new THREE.BoxGeometry(0.03, 0.018, 0.023), woodDarkMat);
    collar.position.set(0, -r + 0.006, 0);
    this.group.add(shaft, flare, collar);

    // haptic quadrant flashes: quarter-circle glow segments at the impact's
    // paddle-local X/Y — on BOTH faces, since the player sees the blade's back
    // (the face normal points at the wall). CircleGeometry theta starts at +X
    // and runs CCW: Q0 TL, Q1 TR, Q2 BL, Q3 BR.
    const quadTheta: number[] = [Math.PI / 2, 0, Math.PI, (3 * Math.PI) / 2];
    for (const theta of quadTheta) {
      for (const z of [0.0075, -0.0075]) {
        const mat = new THREE.MeshBasicMaterial({
          color: 0x00e0ff,
          transparent: true,
          opacity: 0,
          blending: THREE.AdditiveBlending,
          depthWrite: false,
          side: THREE.DoubleSide,
        });
        const q = new THREE.Mesh(new THREE.CircleGeometry(r * 0.96, 24, theta, Math.PI / 2), mat);
        q.position.z = z;
        this.quadrants.push(q); // index i belongs to quadrant floor(i/2)
        this.group.add(q);
      }
    }

    // tracking status ring hugging the blade edge (torus already lies in XY)
    this.statusMat = new THREE.MeshBasicMaterial({ color: 0x00e0ff, transparent: true, opacity: 0.55 });
    this.statusRing = new THREE.Mesh(new THREE.TorusGeometry(r + 0.005, 0.0035, 8, 48), this.statusMat);
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
      // blink the ring, not the paddle — the paddle should never vanish
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
