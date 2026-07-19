// Shared table-tennis paddle geometry: a proper rounded blade (elliptical,
// beveled edge — not a flat disc) that necks into a contoured, slightly
// flattened handle. Used by both the player and opponent paddles.

import * as THREE from "three";

const WOOD = 0xb9895a;

export interface BladeParts {
  group: THREE.Group;
  rx: number; // blade half-width
  ry: number; // blade half-height
}

/** Build the blade + handle. Front face (+Z) and back face (-Z) rubbers can
 *  differ so the paddle reads correctly from either side. */
export function buildBlade(frontColor: number, backColor: number, r = 0.095): BladeParts {
  const group = new THREE.Group();
  const rx = r;
  const ry = r * 1.07; // real blades are a touch taller than wide

  // ---- blade outline: an ellipse, extruded with a rounded bevel so the rim
  // reads as a real paddle edge instead of a flat cut. ----
  const shape = new THREE.Shape();
  shape.absellipse(0, 0, rx, ry, 0, Math.PI * 2, false, 0);

  const layer = (thick: number, zCenter: number, mat: THREE.Material, shrink = 1): THREE.Mesh => {
    const g = new THREE.ExtrudeGeometry(shape, {
      depth: thick,
      bevelEnabled: true,
      bevelThickness: 0.0035,
      bevelSize: 0.0035,
      bevelSegments: 3,
      curveSegments: 48,
    });
    g.translate(0, 0, -thick / 2); // centre the slab on its own z
    const m = new THREE.Mesh(g, mat);
    m.position.z = zCenter;
    m.scale.set(shrink, shrink, 1);
    m.castShadow = true;
    m.receiveShadow = true;
    return m;
  };

  const woodMat = new THREE.MeshStandardMaterial({ color: WOOD, roughness: 0.6, metalness: 0.05 });
  const frontMat = new THREE.MeshStandardMaterial({ color: frontColor, roughness: 0.85 });
  const backMat = new THREE.MeshStandardMaterial({ color: backColor, roughness: 0.85 });

  const CORE = 0.006, RUB = 0.004;
  group.add(layer(CORE, 0, woodMat));                       // wood core
  group.add(layer(RUB, CORE / 2 + RUB / 2, frontMat, 0.98)); // rubber, +Z face
  group.add(layer(RUB, -CORE / 2 - RUB / 2, backMat, 0.98)); // rubber, -Z face

  // ---- contoured handle: a lathe profile, flattened front-to-back into an
  // oval grip, necking out of the blade bottom. ----
  const profile: THREE.Vector2[] = [
    new THREE.Vector2(0.0005, -ry - 0.115),
    new THREE.Vector2(0.014, -ry - 0.112),
    new THREE.Vector2(0.019, -ry - 0.085),
    new THREE.Vector2(0.017, -ry - 0.05),
    new THREE.Vector2(0.0135, -ry - 0.02),
    new THREE.Vector2(0.013, -ry + 0.028), // neck, tucked under the blade
  ];
  const handleGeo = new THREE.LatheGeometry(profile, 28);
  const handle = new THREE.Mesh(handleGeo, woodMat);
  handle.scale.set(1, 1, 0.62); // flatten into an oval grip
  handle.castShadow = true;
  group.add(handle);

  // white edge tape following the elliptical rim
  const rim = new THREE.Mesh(
    new THREE.TorusGeometry(1, 0.004, 8, 56),
    new THREE.MeshStandardMaterial({ color: 0xf0ede6, roughness: 0.7 }),
  );
  rim.scale.set(rx + 0.004, ry + 0.004, 1); // stretch the unit torus to the ellipse
  group.add(rim);

  return { group, rx, ry };
}
