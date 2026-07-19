// Realistic-styled table-tennis hall: blue tournament table with net and
// white lines, wood-plank floor, dark venue walls, soft shadows and warm key
// lighting. Sized to the game world: wall z=0, paddle plane z=1.8, player z=2.

import * as THREE from "three";

// table dimensions scaled to fit the game world (official 1.525×2.74 doesn't)
const TABLE_W = 1.5;
const TABLE_L = 1.7;
const TABLE_H = 0.76;
const TABLE_Z0 = 0.1; // near wall
const TABLE_ZC = TABLE_Z0 + TABLE_L / 2;

export function buildArena(scene: THREE.Scene): void {
  scene.background = new THREE.Color(0x0a0d13);
  scene.fog = new THREE.Fog(0x0a0d13, 5.0, 14.0);

  buildLights(scene);
  buildFloor(scene);
  buildTable(scene);
  buildVenue(scene);
}

// --------------------------------------------------------------------------- //
function buildLights(scene: THREE.Scene): void {
  scene.add(new THREE.HemisphereLight(0xbdd0e8, 0x2a2620, 0.75));

  // warm venue key light, casting soft shadows over the table
  const key = new THREE.DirectionalLight(0xfff1de, 2.2);
  key.position.set(2.2, 4.2, 3.0);
  key.castShadow = true;
  key.shadow.mapSize.set(2048, 2048);
  key.shadow.camera.left = -2.5;
  key.shadow.camera.right = 2.5;
  key.shadow.camera.top = 3.5;
  key.shadow.camera.bottom = -0.5;
  key.shadow.camera.near = 0.5;
  key.shadow.camera.far = 12;
  key.shadow.bias = -0.0004;
  key.shadow.radius = 4;
  scene.add(key);

  // cool fill from the opposite side so shadows aren't pitch black
  const fill = new THREE.DirectionalLight(0x9db8dd, 0.5);
  fill.position.set(-2.5, 2.5, 1.0);
  scene.add(fill);
}

// --------------------------------------------------------------------------- //
function buildFloor(scene: THREE.Scene): void {
  const floor = new THREE.Mesh(
    new THREE.PlaneGeometry(14, 14),
    new THREE.MeshStandardMaterial({ map: woodTexture(), roughness: 0.75, metalness: 0.0 }),
  );
  floor.rotation.x = -Math.PI / 2;
  floor.position.set(0, 0, 2);
  floor.receiveShadow = true;
  scene.add(floor);
}

/** Procedural sports-hall wood plank texture (no external assets — spec CSP). */
function woodTexture(): THREE.CanvasTexture {
  const c = document.createElement("canvas");
  c.width = c.height = 512;
  const ctx = c.getContext("2d")!;
  const base = ["#a5793f", "#ab7f45", "#9f7339", "#b0834a", "#a2763c"];
  const plank = 64;
  for (let y = 0; y < 512; y += plank) {
    ctx.fillStyle = base[(y / plank) % base.length];
    ctx.fillRect(0, y, 512, plank);
    // grain streaks
    for (let i = 0; i < 22; i++) {
      ctx.strokeStyle = `rgba(70,45,15,${0.05 + Math.random() * 0.08})`;
      ctx.lineWidth = 1 + Math.random() * 1.5;
      const gy = y + Math.random() * plank;
      ctx.beginPath();
      ctx.moveTo(0, gy);
      ctx.bezierCurveTo(170, gy + (Math.random() - 0.5) * 6, 340, gy + (Math.random() - 0.5) * 6, 512, gy);
      ctx.stroke();
    }
    // plank seam
    ctx.fillStyle = "rgba(40,25,8,0.55)";
    ctx.fillRect(0, y + plank - 2, 512, 2);
    // butt joints
    for (let n = 0; n < 3; n++) {
      const jx = ((y * 7919) % 512 + n * 171) % 512;
      ctx.fillRect(jx, y, 2, plank);
    }
  }
  const tex = new THREE.CanvasTexture(c);
  tex.wrapS = tex.wrapT = THREE.RepeatWrapping;
  tex.repeat.set(4, 4);
  tex.colorSpace = THREE.SRGBColorSpace;
  tex.anisotropy = 4;
  return tex;
}

// --------------------------------------------------------------------------- //
function buildTable(scene: THREE.Scene): void {
  const table = new THREE.Group();

  // top: tournament blue with subtle sheen
  const top = new THREE.Mesh(
    new THREE.BoxGeometry(TABLE_W, 0.045, TABLE_L),
    new THREE.MeshStandardMaterial({ color: 0x1857a8, roughness: 0.55, metalness: 0.05 }),
  );
  top.position.set(0, TABLE_H - 0.0225, TABLE_ZC);
  top.castShadow = true;
  top.receiveShadow = true;
  table.add(top);

  // white boundary + centre lines, floated a hair above the top
  const lineMat = new THREE.MeshStandardMaterial({ color: 0xf4f4f0, roughness: 0.5 });
  const lineY = TABLE_H + 0.001;
  const mkLine = (w: number, l: number, x: number, z: number) => {
    const m = new THREE.Mesh(new THREE.BoxGeometry(w, 0.002, l), lineMat);
    m.position.set(x, lineY, z);
    table.add(m);
  };
  const lw = 0.02;
  mkLine(TABLE_W, lw, 0, TABLE_Z0 + lw / 2);            // near-wall end line
  mkLine(TABLE_W, lw, 0, TABLE_Z0 + TABLE_L - lw / 2);  // player end line
  mkLine(lw, TABLE_L, -TABLE_W / 2 + lw / 2, TABLE_ZC); // left side
  mkLine(lw, TABLE_L, TABLE_W / 2 - lw / 2, TABLE_ZC);  // right side
  mkLine(lw / 2, TABLE_L, 0, TABLE_ZC);                 // centre line

  // legs + under-frame
  const legMat = new THREE.MeshStandardMaterial({ color: 0x2b2f36, roughness: 0.6, metalness: 0.4 });
  for (const [lx, lz] of [
    [-TABLE_W / 2 + 0.12, TABLE_Z0 + 0.18],
    [TABLE_W / 2 - 0.12, TABLE_Z0 + 0.18],
    [-TABLE_W / 2 + 0.12, TABLE_Z0 + TABLE_L - 0.18],
    [TABLE_W / 2 - 0.12, TABLE_Z0 + TABLE_L - 0.18],
  ]) {
    const leg = new THREE.Mesh(new THREE.BoxGeometry(0.05, TABLE_H - 0.045, 0.05), legMat);
    leg.position.set(lx, (TABLE_H - 0.045) / 2, lz);
    leg.castShadow = true;
    table.add(leg);
  }
  const skirt = new THREE.Mesh(new THREE.BoxGeometry(TABLE_W * 0.92, 0.06, TABLE_L * 0.92), legMat);
  skirt.position.set(0, TABLE_H - 0.08, TABLE_ZC);
  table.add(skirt);

  buildNet(table);
  scene.add(table);
}

function buildNet(table: THREE.Group): void {
  const netH = 0.1525;
  const postMat = new THREE.MeshStandardMaterial({ color: 0x22252b, roughness: 0.5, metalness: 0.5 });
  for (const x of [-TABLE_W / 2 - 0.02, TABLE_W / 2 + 0.02]) {
    const post = new THREE.Mesh(new THREE.CylinderGeometry(0.012, 0.012, netH + 0.02, 10), postMat);
    post.position.set(x, TABLE_H + (netH + 0.02) / 2, TABLE_ZC);
    post.castShadow = true;
    table.add(post);
  }
  // net mesh: procedural grid texture on a transparent plane
  const net = new THREE.Mesh(
    new THREE.PlaneGeometry(TABLE_W + 0.04, netH),
    new THREE.MeshStandardMaterial({
      map: netTexture(),
      transparent: true,
      side: THREE.DoubleSide,
      roughness: 0.9,
      alphaTest: 0.05,
    }),
  );
  net.position.set(0, TABLE_H + netH / 2, TABLE_ZC);
  table.add(net);
  // white tape along the top
  const tape = new THREE.Mesh(
    new THREE.BoxGeometry(TABLE_W + 0.04, 0.014, 0.004),
    new THREE.MeshStandardMaterial({ color: 0xf4f4f0, roughness: 0.6 }),
  );
  tape.position.set(0, TABLE_H + netH - 0.007, TABLE_ZC);
  table.add(tape);
}

function netTexture(): THREE.CanvasTexture {
  const c = document.createElement("canvas");
  c.width = 256;
  c.height = 64;
  const ctx = c.getContext("2d")!;
  ctx.clearRect(0, 0, 256, 64);
  ctx.strokeStyle = "rgba(30,32,38,0.95)";
  ctx.lineWidth = 1.2;
  for (let x = 0; x <= 256; x += 5) {
    ctx.beginPath(); ctx.moveTo(x, 0); ctx.lineTo(x, 64); ctx.stroke();
  }
  for (let y = 0; y <= 64; y += 5) {
    ctx.beginPath(); ctx.moveTo(0, y); ctx.lineTo(256, y); ctx.stroke();
  }
  const tex = new THREE.CanvasTexture(c);
  tex.colorSpace = THREE.SRGBColorSpace;
  return tex;
}

// --------------------------------------------------------------------------- //
function buildVenue(scene: THREE.Scene): void {
  // dark venue wall behind the table with a subtle target ring (balls spawn here)
  const wall = new THREE.Mesh(
    new THREE.PlaneGeometry(10, 5),
    new THREE.MeshStandardMaterial({ color: 0x131820, roughness: 0.9 }),
  );
  wall.position.set(0, 2.5, -0.6);
  wall.receiveShadow = true;
  scene.add(wall);

  // sponsor-style barrier boards at the sides, like a real venue
  const boardMat = new THREE.MeshStandardMaterial({ color: 0x10365e, roughness: 0.7 });
  for (const x of [-1.6, 1.6]) {
    const board = new THREE.Mesh(new THREE.BoxGeometry(0.04, 0.7, 3.2), boardMat);
    board.position.set(x, 0.35, 1.2);
    board.castShadow = true;
    board.receiveShadow = true;
    scene.add(board);
  }

  // subtle glowing target ring on the wall where balls come from
  const ring = new THREE.Mesh(
    new THREE.TorusGeometry(0.3, 0.012, 10, 48),
    new THREE.MeshBasicMaterial({ color: 0x2a9fd8, transparent: true, opacity: 0.6 }),
  );
  ring.position.set(0, 1.2, -0.55);
  scene.add(ring);
}
