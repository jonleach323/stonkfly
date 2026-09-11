// scene.js — the decorative 3D avatar: a low-poly fruit fly at a CRT terminal.
//
// startScene(canvas) builds everything from primitives (no model files) and
// returns { update, setPaused, isPaused, setVisible, dispose }. The monitor's
// screen is a CanvasTexture painted by monitor.js from the latest snapshot.
// The avatar is decorative: nothing here is a neural or muscle reconstruction,
// and it never touches game state.

import * as THREE from 'https://cdn.jsdelivr.net/npm/three@0.170.0/build/three.module.js';
import { drawMonitor, MONITOR_WIDTH, MONITOR_HEIGHT } from './monitor.js';

const INTERNAL_WIDTH = 800;        // render width; the canvas is upscaled with image-rendering: pixelated
const MAX_INTERNAL_HEIGHT = 1024;
const DEFAULT_ASPECT = 16 / 11;
const DRAG_RADIANS_PER_PIXEL = 0.007;
const ORBIT_MIN = -1.35;              // drag limits (radians from home): the screen stays within ~75 degrees of facing the camera
const ORBIT_MAX = 1.05;
const MONITOR_TICK_MS = 1000;      // countdown refresh while the loop runs

const PALETTE = {
  bg: 0x060709,
  acid: 0xbdff32,
  red: 0xff4b78,
  blue: 0x7376ff,
  magenta: 0xff3ce0,
  floor: 0x0b0d13,
  wall: 0x0d1018,
  desk: 0x2a3040,
  deskLeg: 0x1a1e27,
  crt: 0x9d9784,
  crtDark: 0x5a564c,
  keyboard: 0x8f8a78,
  keys: 0x4d4b45,
  stool: 0x3a2f45,
  metal: 0x5b6070,
  body: 0x7f8ba3,
  bodyDark: 0x5d6779,
  eye: 0x8c1030,
  wing: 0xcfe4ff,
  leg: 0x4a5163,
  shape: 0x1a1f2b,
  shapeAlt: 0x222836,
};

function standard(color, extra = {}) {
  return new THREE.MeshStandardMaterial({ color, roughness: 0.85, metalness: 0.05, ...extra });
}

function flat(color, extra = {}) {
  return standard(color, { flatShading: true, ...extra });
}

function box(w, h, d, material, x = 0, y = 0, z = 0) {
  const mesh = new THREE.Mesh(new THREE.BoxGeometry(w, h, d), material);
  mesh.position.set(x, y, z);
  return mesh;
}

function emissiveBox(w, h, d, color, intensity, x, y, z) {
  const material = new THREE.MeshStandardMaterial({
    color: 0x111111, emissive: color, emissiveIntensity: intensity, roughness: 1, metalness: 0,
  });
  return box(w, h, d, material, x, y, z);
}

// Cylinder between two points; used for legs and antennae.
const UP = new THREE.Vector3(0, 1, 0);
const tmpDir = new THREE.Vector3();
function placeSegment(mesh, a, b) {
  tmpDir.subVectors(b, a);
  const length = tmpDir.length() || 1e-6;
  mesh.position.copy(a).addScaledVector(tmpDir, 0.5);
  mesh.quaternion.setFromUnitVectors(UP, tmpDir.multiplyScalar(1 / length));
  mesh.scale.set(1, length, 1);
}
function segment(a, b, radius, material) {
  const mesh = new THREE.Mesh(new THREE.CylinderGeometry(radius, radius * 0.8, 1, 6), material);
  placeSegment(mesh, a, b);
  return mesh;
}

// ---------------------------------------------------------------------------
// Procedural textures (small canvases; no assets).

function floorTexture() {
  const size = 64;
  const c = document.createElement('canvas');
  c.width = size;
  c.height = size;
  const ctx = c.getContext('2d');
  ctx.fillStyle = '#0b0d13';
  ctx.fillRect(0, 0, size, size);
  ctx.strokeStyle = '#161a24';
  ctx.lineWidth = 1;
  ctx.strokeRect(0.5, 0.5, size, size);
  ctx.fillStyle = '#2a2f3c';
  for (let i = 0; i < 4; i += 1) {
    for (let j = 0; j < 4; j += 1) ctx.fillRect(i * 16 + 7, j * 16 + 7, 2, 2);
  }
  const tex = new THREE.CanvasTexture(c);
  tex.colorSpace = THREE.SRGBColorSpace;
  tex.wrapS = THREE.RepeatWrapping;
  tex.wrapT = THREE.RepeatWrapping;
  tex.repeat.set(14, 14);
  tex.magFilter = THREE.NearestFilter;
  tex.minFilter = THREE.LinearMipmapLinearFilter;
  return tex;
}

function stripeTexture() {
  const c = document.createElement('canvas');
  c.width = 4;
  c.height = 32;
  const ctx = c.getContext('2d');
  for (let y = 0; y < 32; y += 1) {
    const band = y >= 8 && y < 28 && Math.floor((y - 8) / 4) % 2 === 1;
    ctx.fillStyle = band ? '#59627a' : '#8a95ad';
    ctx.fillRect(0, y, 4, 1);
  }
  const tex = new THREE.CanvasTexture(c);
  tex.colorSpace = THREE.SRGBColorSpace;
  tex.magFilter = THREE.NearestFilter;
  tex.minFilter = THREE.NearestFilter;
  return tex;
}

// A pixel candlestick poster for the back wall. Decorative; not data.
function posterTexture() {
  const c = document.createElement('canvas');
  c.width = 64;
  c.height = 88;
  const ctx = c.getContext('2d');
  ctx.fillStyle = '#0e1015';
  ctx.fillRect(0, 0, 64, 88);
  ctx.fillStyle = '#32353e';
  ctx.fillRect(0, 0, 64, 2);
  ctx.fillRect(0, 86, 64, 2);
  ctx.fillRect(0, 0, 2, 88);
  ctx.fillRect(62, 0, 2, 88);
  const candles = [[40, 52, 1], [50, 44, 0], [46, 60, 1], [58, 48, 0], [50, 66, 1], [64, 56, 0], [56, 74, 1], [70, 60, 0], [62, 70, 1], [68, 58, 0]];
  candles.forEach(([open, close, up], i) => {
    const x = 8 + i * 5;
    ctx.fillStyle = up ? '#bdff32' : '#ff4b78';
    const top = Math.min(open, close);
    const bottom = Math.max(open, close);
    ctx.fillRect(x + 1, 88 - bottom - 6, 1, bottom - top + 12);
    ctx.fillRect(x, 88 - bottom, 3, Math.max(2, bottom - top));
  });
  ctx.fillStyle = '#bdff32';
  ctx.fillRect(8, 10, 10, 3);
  ctx.fillRect(20, 10, 4, 3);
  ctx.fillRect(26, 10, 14, 3);
  const tex = new THREE.CanvasTexture(c);
  tex.colorSpace = THREE.SRGBColorSpace;
  tex.magFilter = THREE.NearestFilter;
  tex.minFilter = THREE.NearestFilter;
  return tex;
}

// ---------------------------------------------------------------------------
// Set dressing.

function buildRoom(scene) {
  const floor = new THREE.Mesh(new THREE.PlaneGeometry(14, 14), standard(0xffffff, { map: floorTexture(), roughness: 0.95 }));
  floor.rotation.x = -Math.PI / 2;
  scene.add(floor);

  const wallMaterial = standard(PALETTE.wall, { roughness: 1 });
  const back = new THREE.Mesh(new THREE.PlaneGeometry(14, 5), wallMaterial);
  back.position.set(0, 2.5, -1.9);
  scene.add(back);
  const left = new THREE.Mesh(new THREE.PlaneGeometry(8, 5), wallMaterial);
  left.rotation.y = Math.PI / 2;
  left.position.set(-3.2, 2.5, 1);
  scene.add(left);

  // Blocky background shapes.
  scene.add(box(0.7, 1.7, 0.5, standard(PALETTE.shape), -3.1, 0.85, -1.6));
  scene.add(box(0.5, 0.5, 0.5, standard(PALETTE.shapeAlt), 1.25, 0.25, -1.5));
  scene.add(box(0.44, 0.44, 0.44, standard(PALETTE.shape), 1.28, 0.72, -1.52));
  const tower = box(0.46, 1.1, 0.5, standard(PALETTE.shapeAlt), 1.95, 0.55, -1.55);
  scene.add(tower);
  for (let i = 0; i < 4; i += 1) {
    scene.add(emissiveBox(0.04, 0.02, 0.01, i % 2 ? PALETTE.blue : PALETTE.acid, 2.5, 1.8, 0.9 - i * 0.14, -1.295));
  }

  const poster = new THREE.Mesh(new THREE.PlaneGeometry(0.5, 0.69), standard(0xffffff, { map: posterTexture(), roughness: 1 }));
  poster.position.set(-2.35, 1.45, -1.89);
  scene.add(poster);

  // Neon tubes: the magenta one is the rim light, the blue one a cool accent.
  scene.add(emissiveBox(3.2, 0.035, 0.035, PALETTE.magenta, 2.2, 0.5, 2.3, -1.87));
  scene.add(emissiveBox(0.035, 1.7, 0.035, PALETTE.blue, 2.0, -2.75, 1.15, -1.87));

  // Contact shadow under the stool.
  const shadow = new THREE.Mesh(new THREE.CircleGeometry(0.34, 20), new THREE.MeshBasicMaterial({ color: 0x000000, transparent: true, opacity: 0.55, depthWrite: false }));
  shadow.rotation.x = -Math.PI / 2;
  shadow.position.set(-0.16, 0.003, 0.56);
  scene.add(shadow);
}

function buildDesk(scene) {
  const group = new THREE.Group();
  group.add(box(1.8, 0.05, 0.8, standard(PALETTE.desk, { roughness: 0.7 }), 0, 0.72, -0.1));
  const legMaterial = standard(PALETTE.deskLeg);
  [[-0.85, -0.45], [0.85, -0.45], [-0.85, 0.25], [0.85, 0.25]].forEach(([x, z]) => {
    group.add(box(0.05, 0.7, 0.05, legMaterial, x, 0.35, z));
  });
  group.add(emissiveBox(1.8, 0.012, 0.012, PALETTE.blue, 1.6, 0, 0.693, 0.3));

  // Mug: an acid accent on the desk.
  const mug = new THREE.Mesh(new THREE.CylinderGeometry(0.05, 0.045, 0.1, 10), standard(PALETTE.acid, { roughness: 0.6 }));
  mug.position.set(-0.62, 0.795, -0.05);
  group.add(mug);
  const handle = new THREE.Mesh(new THREE.TorusGeometry(0.03, 0.008, 6, 10), mug.material);
  handle.position.set(-0.56, 0.8, -0.05);
  group.add(handle);

  scene.add(group);
  return group;
}

function buildStool(scene, x, z) {
  const group = new THREE.Group();
  group.add(new THREE.Mesh(new THREE.CylinderGeometry(0.2, 0.22, 0.03, 16), standard(PALETTE.metal, { metalness: 0.4, roughness: 0.5 })));
  group.children[0].position.y = 0.02;
  const post = new THREE.Mesh(new THREE.CylinderGeometry(0.028, 0.028, 0.42, 8), standard(PALETTE.metal, { metalness: 0.4, roughness: 0.5 }));
  post.position.y = 0.24;
  group.add(post);
  const seat = new THREE.Mesh(new THREE.CylinderGeometry(0.3, 0.27, 0.06, 16), standard(PALETTE.stool, { roughness: 0.9 }));
  seat.position.y = 0.475;
  group.add(seat);
  group.position.set(x, 0, z);
  scene.add(group);
  return group;
}

function buildMonitor(scene, screenTexture, yaw) {
  const group = new THREE.Group();
  const plastic = standard(PALETTE.crt, { roughness: 0.8 });
  const dark = standard(PALETTE.crtDark, { roughness: 0.9 });

  group.add(box(0.42, 0.07, 0.36, dark, 0, 0.035, 0));                // stand
  group.add(box(1.02, 0.74, 0.38, plastic, 0, 0.46, -0.05));          // front body with bezel
  group.add(box(0.8, 0.6, 0.3, plastic, 0, 0.46, -0.38));             // tube bulge
  group.add(box(0.06, 0.06, 0.02, dark, -0.4, 0.13, 0.145));          // knobs
  group.add(box(0.06, 0.06, 0.02, dark, -0.32, 0.13, 0.145));

  const bezelInner = box(0.88, 0.52, 0.02, standard(0x0a0b0e, { roughness: 0.4 }), 0, 0.49, 0.135);
  group.add(bezelInner);
  const screen = new THREE.Mesh(
    new THREE.PlaneGeometry(0.82, 0.461),
    new THREE.MeshBasicMaterial({ map: screenTexture, toneMapped: false }),
  );
  screen.position.set(0, 0.49, 0.147);
  group.add(screen);
  // Glass: a faint blue sheen so the screen reads as a tube, not a sticker.
  const glass = new THREE.Mesh(
    new THREE.PlaneGeometry(0.84, 0.48),
    new THREE.MeshStandardMaterial({ color: 0x9db4ff, transparent: true, opacity: 0.07, roughness: 0.15, metalness: 0.6, depthWrite: false }),
  );
  glass.position.set(0, 0.49, 0.149);
  group.add(glass);

  const led = emissiveBox(0.025, 0.025, 0.01, PALETTE.acid, 2.5, 0.44, 0.13, 0.145);
  group.add(led);

  const glow = new THREE.PointLight(0xa8ffb8, 2.2, 2.8, 2);
  glow.position.set(0, 0.49, 0.5);
  group.add(glow);

  group.rotation.y = yaw;
  group.rotation.x = -0.06; // tilted back slightly toward the camera
  scene.add(group);
  return { group, led, glow, screen };
}

function buildKeyboard(scene, x, z, yaw) {
  const group = new THREE.Group();
  group.add(box(0.56, 0.035, 0.2, standard(PALETTE.keyboard, { roughness: 0.85 }), 0, 0.0175, 0));
  const keys = standard(PALETTE.keys, { roughness: 0.9 });
  for (let row = 0; row < 3; row += 1) {
    group.add(box(0.5 - row * 0.04, 0.012, 0.04, keys, 0, 0.04, -0.06 + row * 0.055));
  }
  group.position.set(x, 0.745, z);
  group.rotation.y = yaw;
  scene.add(group);
  return group;
}

// ---------------------------------------------------------------------------
// The fly. Local frame: forward is -z, up is +y, the base sits at y = 0.

function buildFly() {
  const group = new THREE.Group();
  const body = flat(PALETTE.body, { roughness: 0.75 });
  const bodyDark = flat(PALETTE.bodyDark, { roughness: 0.8 });

  const thorax = new THREE.Mesh(new THREE.SphereGeometry(1, 12, 9), body);
  thorax.scale.set(0.16, 0.145, 0.18);
  thorax.position.set(0, 0.29, 0);
  group.add(thorax);

  const abdomenGeometry = new THREE.SphereGeometry(1, 12, 10);
  abdomenGeometry.rotateX(Math.PI / 2); // poles along the body axis so the stripes ring it
  const abdomen = new THREE.Mesh(abdomenGeometry, flat(0xffffff, { map: stripeTexture(), roughness: 0.8 }));
  abdomen.scale.set(0.15, 0.13, 0.27);
  abdomen.position.set(0, 0.22, 0.25);
  abdomen.rotation.x = -0.25;
  group.add(abdomen);

  const head = new THREE.Group();
  head.position.set(0, 0.36, -0.19);
  const skull = new THREE.Mesh(new THREE.SphereGeometry(1, 12, 9), body);
  skull.scale.set(0.12, 0.115, 0.1);
  head.add(skull);

  const eyeMaterial = flat(PALETTE.eye, { roughness: 0.3, metalness: 0.15, emissive: 0x5a0a1c, emissiveIntensity: 0.9 });
  const glint = new THREE.MeshBasicMaterial({ color: 0xffd6e0 });
  [-1, 1].forEach((s) => {
    const eye = new THREE.Mesh(new THREE.IcosahedronGeometry(0.088, 1), eyeMaterial);
    eye.position.set(s * 0.085, 0.015, -0.035);
    head.add(eye);
    const spark = new THREE.Mesh(new THREE.SphereGeometry(0.018, 6, 5), glint);
    spark.position.set(s * 0.12, 0.05, -0.085);
    head.add(spark);
    const antenna = segment(new THREE.Vector3(s * 0.03, 0.05, -0.1), new THREE.Vector3(s * 0.07, 0.12, -0.19), 0.008, bodyDark);
    head.add(antenna);
  });
  const proboscis = segment(new THREE.Vector3(0, -0.05, -0.07), new THREE.Vector3(0, -0.13, -0.11), 0.018, bodyDark);
  head.add(proboscis);
  group.add(head);

  // Wings: an ellipse rooted at the thorax; outer group = resting yaw, inner = flap.
  const wingShape = new THREE.Shape();
  wingShape.absellipse(0.19, 0, 0.19, 0.07, 0, Math.PI * 2, false, 0);
  const wingGeometry = new THREE.ShapeGeometry(wingShape, 12);
  wingGeometry.rotateX(-Math.PI / 2);
  const wingMaterial = new THREE.MeshStandardMaterial({
    color: PALETTE.wing, emissive: 0x1a2a4a, emissiveIntensity: 0.6, transparent: true, opacity: 0.45, side: THREE.DoubleSide, roughness: 0.25, metalness: 0.2, depthWrite: false,
  });
  const wings = [];
  [-1, 1].forEach((s) => {
    const root = new THREE.Group();
    root.position.set(s * 0.06, 0.4, 0.03);
    root.rotation.y = s > 0 ? -1.3 : -(Math.PI - 1.3);
    const flap = new THREE.Group();
    flap.add(new THREE.Mesh(wingGeometry, wingMaterial));
    root.add(flap);
    group.add(root);
    wings.push(flap);
  });

  // Legs: hip -> knee -> foot, two segments each. Front legs rest on the keyboard.
  const legMaterial = standard(PALETTE.leg, { roughness: 0.7 });
  const jointGeometry = new THREE.SphereGeometry(0.016, 6, 5);
  const legs = [];
  const layout = [
    { hip: [0.13, 0.24, -0.09], knee: [0.16, 0.36, -0.24], foot: [0.19, 0.29, -0.52] }, // front, on the keys
    { hip: [0.14, 0.22, 0.0], knee: [0.27, 0.32, 0.0], foot: [0.2, 0.0, 0.04] },        // middle, on the seat
    { hip: [0.13, 0.2, 0.09], knee: [0.24, 0.3, 0.2], foot: [0.18, 0.0, 0.28] },          // hind, trailing
  ];
  layout.forEach((l, index) => {
    [-1, 1].forEach((s) => {
      const hip = new THREE.Vector3(s * l.hip[0], l.hip[1], l.hip[2]);
      const knee = new THREE.Vector3(s * l.knee[0], l.knee[1], l.knee[2]);
      const foot = new THREE.Vector3(s * l.foot[0], l.foot[1], l.foot[2]);
      const femur = segment(hip, knee, 0.013, legMaterial);
      const tibia = segment(knee, foot, 0.011, legMaterial);
      const joint = new THREE.Mesh(jointGeometry, legMaterial);
      joint.position.copy(knee);
      group.add(femur, tibia, joint);
      legs.push({ index, side: s, hip, knee, foot, femur, tibia, joint, restKnee: knee.clone(), restFoot: foot.clone() });
    });
  });

  return { group, head, abdomen, thorax, wings, legs };
}

// ---------------------------------------------------------------------------
// Post pass: ordered dither + faint scanlines + vignette.

const POST_VERTEX = /* glsl */ `
  varying vec2 vUv;
  void main() { vUv = uv; gl_Position = vec4(position.xy, 0.0, 1.0); }
`;

const POST_FRAGMENT = /* glsl */ `
  uniform sampler2D tDiffuse;
  uniform float uLevels;
  uniform float uDither;
  uniform float uScan;
  varying vec2 vUv;
  float b2(vec2 p) { return mod(2.0 * p.x + 3.0 * p.y, 4.0); }
  float bayer4(vec2 p) { vec2 q = floor(mod(p, 4.0)); return (4.0 * b2(mod(q, 2.0)) + b2(floor(q * 0.5))) / 16.0; }
  vec3 toSRGB(vec3 c) {
    vec3 lo = c * 12.92;
    vec3 hi = 1.055 * pow(max(c, vec3(0.0)), vec3(1.0 / 2.4)) - 0.055;
    return mix(lo, hi, step(vec3(0.0031308), c));
  }
  void main() {
    vec3 c = toSRGB(texture2D(tDiffuse, vUv).rgb);
    float d = (bayer4(gl_FragCoord.xy) - 0.5) * uDither;
    c = floor(c * uLevels + 0.5 + d) / uLevels;
    float scan = 1.0 - uScan * mod(floor(gl_FragCoord.y), 2.0);
    vec2 v = vUv * 2.0 - 1.0;
    float vig = 1.0 - 0.28 * smoothstep(0.35, 1.9, dot(v, v));
    gl_FragColor = vec4(c * scan * vig, 1.0);
  }
`;

// ---------------------------------------------------------------------------

/**
 * Start the avatar on a canvas. Throws when WebGL is unavailable so the page
 * can show its fallback.
 */
export function startScene(canvas) {
  if (!canvas || typeof canvas.getContext !== 'function') throw new Error('startScene needs a canvas element');

  let renderer;
  try {
    renderer = new THREE.WebGLRenderer({
      canvas, antialias: false, alpha: false, stencil: false, powerPreference: 'low-power', failIfMajorPerformanceCaveat: false,
    });
  } catch (err) {
    throw new Error(`WebGL unavailable: ${err && err.message ? err.message : err}`);
  }
  if (!renderer.getContext()) throw new Error('WebGL unavailable');

  renderer.setPixelRatio(1);
  renderer.setClearColor(PALETTE.bg, 1);
  renderer.outputColorSpace = THREE.SRGBColorSpace;
  canvas.style.imageRendering = 'pixelated';
  canvas.style.touchAction = 'pan-y';
  if (!canvas.hasAttribute('tabindex')) canvas.tabIndex = 0;

  // --- monitor texture ------------------------------------------------------
  const screenCanvas = document.createElement('canvas');
  screenCanvas.width = MONITOR_WIDTH;
  screenCanvas.height = MONITOR_HEIGHT;
  const screenCtx = screenCanvas.getContext('2d');
  const screenTexture = new THREE.CanvasTexture(screenCanvas);
  screenTexture.colorSpace = THREE.SRGBColorSpace;
  screenTexture.magFilter = THREE.LinearFilter;
  screenTexture.minFilter = THREE.LinearMipmapLinearFilter;
  screenTexture.generateMipmaps = true;
  screenTexture.anisotropy = Math.min(8, renderer.capabilities.getMaxAnisotropy());

  let latestState = null;
  let latestBoard = null;
  function paintMonitor() {
    try {
      drawMonitor(screenCtx, latestState, latestBoard);
    } catch (err) {
      // A drawing bug must not stop the avatar; leave the last frame on screen.
      if (typeof console !== 'undefined') console.warn('drawMonitor failed', err);
    }
    screenTexture.needsUpdate = true;
  }

  // --- scene ----------------------------------------------------------------
  const scene = new THREE.Scene();
  scene.background = new THREE.Color(PALETTE.bg);
  scene.fog = new THREE.Fog(PALETTE.bg, 4.5, 11);

  const camera = new THREE.PerspectiveCamera(32, DEFAULT_ASPECT, 0.1, 40);

  scene.add(new THREE.HemisphereLight(0x3d55a8, 0x07070c, 0.55));
  const key = new THREE.DirectionalLight(0x8ab0ff, 1.9);
  key.position.set(-2.4, 3.6, 2.4);
  scene.add(key, key.target);
  const rim = new THREE.PointLight(PALETTE.magenta, 6, 9, 2);
  rim.position.set(0.9, 2.3, -1.7);
  scene.add(rim);
  const fill = new THREE.PointLight(PALETTE.blue, 1.4, 6, 2);
  fill.position.set(2.4, 1.3, 1.8);
  scene.add(fill);

  buildRoom(scene);
  buildDesk(scene);

  const monitorYaw = 0.5;
  const monitorPos = new THREE.Vector3(0.16, 0.745, -0.32);
  const monitor = buildMonitor(scene, screenTexture, monitorYaw);
  monitor.group.position.copy(monitorPos);
  const keyboardPos = new THREE.Vector3(-0.02, 0, 0.1);
  buildKeyboard(scene, keyboardPos.x, keyboardPos.z, monitorYaw);

  const stoolPos = new THREE.Vector3(-0.16, 0, 0.56);
  buildStool(scene, stoolPos.x, stoolPos.z);
  const fly = buildFly();
  fly.group.position.set(stoolPos.x, 0.505, stoolPos.z);
  fly.group.scale.setScalar(0.85);
  // Face the monitor: local forward is -z.
  const toScreen = new THREE.Vector3().subVectors(monitorPos, fly.group.position);
  fly.group.rotation.y = Math.atan2(-toScreen.x, -toScreen.z);
  scene.add(fly.group);

  // --- post pass (three r170 is WebGL2-only, so sRGB targets are available) --
  let post = null;
  {
    try {
      const target = new THREE.WebGLRenderTarget(4, 4, {
        minFilter: THREE.NearestFilter, magFilter: THREE.NearestFilter, depthBuffer: true, stencilBuffer: false,
        format: THREE.RGBAFormat, type: THREE.UnsignedByteType, colorSpace: THREE.SRGBColorSpace,
      });
      target.texture.colorSpace = THREE.SRGBColorSpace;
      const material = new THREE.ShaderMaterial({
        uniforms: { tDiffuse: { value: target.texture }, uLevels: { value: 40 }, uDither: { value: 0.9 }, uScan: { value: 0.07 } },
        vertexShader: POST_VERTEX,
        fragmentShader: POST_FRAGMENT,
        depthTest: false,
        depthWrite: false,
      });
      const quadScene = new THREE.Scene();
      quadScene.add(new THREE.Mesh(new THREE.PlaneGeometry(2, 2), material));
      post = { target, material, quadScene, camera: new THREE.OrthographicCamera(-1, 1, 1, -1, 0, 1) };
    } catch (err) {
      post = null; // plain render is still fine
    }
  }

  // --- orbit ----------------------------------------------------------------
  const target = new THREE.Vector3(-0.3, 0.82, 0.12);
  const orbit = { home: 0.68, drag: 0, sway: 0, velocity: 0, radius: 3.05, elevation: 0.25 };
  function clampDrag() {
    if (orbit.drag < ORBIT_MIN) { orbit.drag = ORBIT_MIN; orbit.velocity = 0; }
    if (orbit.drag > ORBIT_MAX) { orbit.drag = ORBIT_MAX; orbit.velocity = 0; }
  }
  function placeCamera() {
    clampDrag();
    const az = orbit.home + orbit.drag + orbit.sway;
    const r = orbit.radius;
    camera.position.set(
      target.x + r * Math.cos(orbit.elevation) * Math.sin(az),
      target.y + r * Math.sin(orbit.elevation),
      target.z + r * Math.cos(orbit.elevation) * Math.cos(az),
    );
    camera.lookAt(target);
  }

  // --- sizing ---------------------------------------------------------------
  let width = 0;
  let height = 0;
  function resize() {
    const cssW = canvas.clientWidth || 0;
    const cssH = canvas.clientHeight || 0;
    if (cssW < 2 || cssH < 2) {
      width = 0;
      height = 0;
      return false;
    }
    const w = Math.min(INTERNAL_WIDTH, Math.max(160, cssW));
    const h = Math.min(MAX_INTERNAL_HEIGHT, Math.max(96, Math.round(w * cssH / cssW)));
    if (w !== width || h !== height) {
      width = w;
      height = h;
      renderer.setSize(w, h, false);
      camera.aspect = w / h;
      camera.updateProjectionMatrix();
      if (post) post.target.setSize(w, h);
    }
    return true;
  }

  // --- render state ---------------------------------------------------------
  const reducedMotionQuery = typeof matchMedia === 'function' ? matchMedia('(prefers-reduced-motion: reduce)') : null;
  let reducedMotion = !!(reducedMotionQuery && reducedMotionQuery.matches);
  let paused = false;
  let visible = true;
  let disposed = false;
  let contextLost = false;
  let rafId = 0;
  let lastT = 0;
  let monitorTickAt = 0;
  let dragging = false;

  const anim = {
    t: 0,
    burstUntil: 0,       // wing flutter
    shudderUntil: 0,     // aversive twitch
    nextBurst: 3,
    flash: 0,            // screen light flash strength
    flashColor: new THREE.Color(0xa8ffb8),
    baseGlow: new THREE.Color(0xa8ffb8),
    lastTick: null,
    running: false,
  };

  function render() {
    if (disposed || contextLost || width === 0) return;
    placeCamera();
    if (post) {
      renderer.setRenderTarget(post.target);
      renderer.render(scene, camera);
      renderer.setRenderTarget(null);
      renderer.render(post.quadScene, post.camera);
    } else {
      renderer.render(scene, camera);
    }
  }

  function animate(t, dt) {
    anim.t = t;
    // Camera sway around home; the user's drag offset is added on top.
    orbit.sway = 0.2 * Math.sin(t * 0.14);
    if (!dragging && Math.abs(orbit.velocity) > 1e-4) {
      orbit.drag += orbit.velocity * dt;
      orbit.velocity *= Math.exp(-dt * 4);
    }

    // Wings: a resting shiver plus occasional bursts (reward triggers one too).
    if (t > anim.nextBurst) {
      anim.burstUntil = t + 0.5 + Math.random() * 0.5;
      anim.nextBurst = anim.burstUntil + 4 + Math.random() * 6;
    }
    const bursting = t < anim.burstUntil;
    const flapAngle = bursting ? Math.sin(t * Math.PI * 2 * 22) * 0.55 + 0.15 : Math.sin(t * 7) * 0.02;
    fly.wings.forEach((w) => { w.rotation.z = flapAngle; });

    // Breathing, head turns and a shudder after an aversive stimulus.
    const breathe = 1 + 0.025 * Math.sin(t * 2.1);
    fly.abdomen.scale.set(0.15 * breathe, 0.13 * breathe, 0.27);
    const shudder = t < anim.shudderUntil ? Math.sin(t * 60) * 0.03 : 0;
    fly.head.rotation.y = -0.25 + 0.3 * Math.sin(t * 0.45) + shudder;
    fly.head.rotation.x = 0.06 * Math.sin(t * 0.9);
    fly.group.rotation.z = shudder * 0.6;

    // Front legs type on the keyboard.
    fly.legs.forEach((leg) => {
      if (leg.index !== 0) return;
      const phase = leg.side > 0 ? 0 : Math.PI;
      const lift = Math.max(0, Math.sin(t * 6 + phase)) * 0.025;
      leg.foot.copy(leg.restFoot).setY(leg.restFoot.y + lift);
      leg.knee.copy(leg.restKnee).setY(leg.restKnee.y + lift * 0.5);
      placeSegment(leg.femur, leg.hip, leg.knee);
      placeSegment(leg.tibia, leg.knee, leg.foot);
      leg.joint.position.copy(leg.knee);
    });

    // Screen glow: gentle flicker; brief colour flash after a stimulus.
    anim.flash = Math.max(0, anim.flash - dt * 0.8);
    monitor.glow.color.copy(anim.baseGlow).lerp(anim.flashColor, anim.flash);
    monitor.glow.intensity = 2.1 + 0.15 * Math.sin(t * 17) + anim.flash * 2.5;
  }

  function shouldRun() {
    return !disposed && !contextLost && visible && !paused && !reducedMotion && !(typeof document !== 'undefined' && document.hidden);
  }

  function frame(nowMs) {
    rafId = 0;
    if (!shouldRun()) {
      anim.running = false;
      return;
    }
    if (width === 0 && !resize()) {
      anim.running = false;
      return;
    }
    const t = nowMs / 1000;
    const dt = lastT ? Math.min(0.05, Math.max(0, t - lastT)) : 0.016;
    lastT = t;
    if (nowMs - monitorTickAt > MONITOR_TICK_MS) {
      monitorTickAt = nowMs;
      paintMonitor();
    }
    animate(t, dt);
    render();
    rafId = requestAnimationFrame(frame);
  }

  // Ensure the loop runs when it should; otherwise draw one still frame.
  function kick() {
    if (disposed) return;
    if (shouldRun()) {
      if (!rafId) {
        anim.running = true;
        lastT = 0;
        rafId = requestAnimationFrame(frame);
      }
      return;
    }
    if (visible && !contextLost && resize()) {
      animate(anim.t, 0);
      render();
    }
  }

  function stopLoop() {
    if (rafId) cancelAnimationFrame(rafId);
    rafId = 0;
    anim.running = false;
  }

  // --- input ----------------------------------------------------------------
  let pointerId = null;
  let lastX = 0;
  let lastMoveT = 0;
  function onPointerDown(e) {
    if (e.button !== undefined && e.button !== 0) return;
    pointerId = e.pointerId;
    lastX = e.clientX;
    lastMoveT = performance.now();
    dragging = true;
    orbit.velocity = 0;
    try { canvas.setPointerCapture(e.pointerId); } catch (err) { /* capture is optional */ }
    canvas.style.cursor = 'grabbing';
  }
  function onPointerMove(e) {
    if (!dragging || e.pointerId !== pointerId) return;
    const now = performance.now();
    const dx = e.clientX - lastX;
    const dt = Math.max(1, now - lastMoveT) / 1000;
    lastX = e.clientX;
    lastMoveT = now;
    const delta = dx * DRAG_RADIANS_PER_PIXEL;
    orbit.drag += delta;
    orbit.velocity = delta / dt;
    if (!anim.running) {
      if (resize()) render();
    }
  }
  function onPointerUp(e) {
    if (e.pointerId !== pointerId) return;
    dragging = false;
    pointerId = null;
    canvas.style.cursor = 'grab';
    if (performance.now() - lastMoveT > 80) orbit.velocity = 0;
    try { canvas.releasePointerCapture(e.pointerId); } catch (err) { /* already released */ }
  }
  function onKeyDown(e) {
    if (e.key === 'ArrowLeft' || e.key === 'ArrowRight') {
      orbit.drag += (e.key === 'ArrowLeft' ? -1 : 1) * 0.15;
      if (!anim.running && resize()) render();
      e.preventDefault();
    }
  }
  canvas.style.cursor = 'grab';
  canvas.addEventListener('pointerdown', onPointerDown);
  canvas.addEventListener('pointermove', onPointerMove);
  canvas.addEventListener('pointerup', onPointerUp);
  canvas.addEventListener('pointercancel', onPointerUp);
  canvas.addEventListener('keydown', onKeyDown);

  function onContextLost(e) {
    e.preventDefault();
    contextLost = true;
    stopLoop();
  }
  function onContextRestored() {
    contextLost = false;
    kick();
  }
  canvas.addEventListener('webglcontextlost', onContextLost);
  canvas.addEventListener('webglcontextrestored', onContextRestored);

  function onVisibilityChange() {
    if (document.hidden) stopLoop();
    else kick();
  }
  document.addEventListener('visibilitychange', onVisibilityChange);

  function onMotionPreference(e) {
    reducedMotion = !!e.matches;
    if (reducedMotion) stopLoop();
    kick();
  }
  if (reducedMotionQuery) {
    if (typeof reducedMotionQuery.addEventListener === 'function') reducedMotionQuery.addEventListener('change', onMotionPreference);
    else if (typeof reducedMotionQuery.addListener === 'function') reducedMotionQuery.addListener(onMotionPreference);
  }

  let observer = null;
  function onResize() {
    if (resize()) kick();
  }
  if (typeof ResizeObserver === 'function') {
    observer = new ResizeObserver(onResize);
    observer.observe(canvas);
  } else {
    window.addEventListener('resize', onResize);
  }

  // Repaint the screen once the pixel font is available, if the page declared it.
  if (typeof document !== 'undefined' && document.fonts && typeof document.fonts.load === 'function') {
    document.fonts.load('16px "Press Start 2P"').then(() => { paintMonitor(); if (!anim.running) kick(); }).catch(() => {});
  }

  // --- public API -----------------------------------------------------------
  function update(state, board) {
    latestState = state || null;
    latestBoard = board || null;
    monitorTickAt = performance.now();
    paintMonitor();

    // React to a new observation; decorative only.
    const tick = latestState && latestState.tick != null ? latestState.tick : null;
    const neural = (latestState && latestState.neural) || {};
    if (tick !== null && tick !== anim.lastTick) {
      const first = anim.lastTick === null;
      anim.lastTick = tick;
      if (!first) {
        if (neural.stimulus === 'reward') {
          anim.burstUntil = anim.t + 1.2;
          anim.flash = 1;
          anim.flashColor.set(PALETTE.acid);
        } else if (neural.stimulus === 'aversive') {
          anim.shudderUntil = anim.t + 0.6;
          anim.flash = 1;
          anim.flashColor.set(PALETTE.red);
        } else {
          anim.flash = 0.4;
          anim.flashColor.set(0xffffff);
        }
      }
    }
    const running = !!(latestState && latestState.status && latestState.status.running);
    monitor.led.material.emissive.set(running ? PALETTE.acid : PALETTE.red);
    if (!anim.running) kick();
  }

  function setPaused(value) {
    paused = !!value;
    if (paused) stopLoop();
    kick();
  }

  function isPaused() {
    return paused;
  }

  function setVisible(value) {
    visible = !!value;
    if (!visible) stopLoop();
    else kick();
  }

  function dispose() {
    disposed = true;
    stopLoop();
    canvas.removeEventListener('pointerdown', onPointerDown);
    canvas.removeEventListener('pointermove', onPointerMove);
    canvas.removeEventListener('pointerup', onPointerUp);
    canvas.removeEventListener('pointercancel', onPointerUp);
    canvas.removeEventListener('keydown', onKeyDown);
    canvas.removeEventListener('webglcontextlost', onContextLost);
    canvas.removeEventListener('webglcontextrestored', onContextRestored);
    document.removeEventListener('visibilitychange', onVisibilityChange);
    if (observer) observer.disconnect();
    else window.removeEventListener('resize', onResize);
    if (reducedMotionQuery) {
      if (typeof reducedMotionQuery.removeEventListener === 'function') reducedMotionQuery.removeEventListener('change', onMotionPreference);
      else if (typeof reducedMotionQuery.removeListener === 'function') reducedMotionQuery.removeListener(onMotionPreference);
    }
    if (post) post.target.dispose();
    screenTexture.dispose();
    renderer.dispose();
  }

  // First frame: boot screen on the monitor, then start the loop (or a still).
  paintMonitor();
  resize();
  kick();

  return { update, setPaused, isPaused, setVisible, dispose };
}
