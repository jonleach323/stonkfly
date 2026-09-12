// brain.js — the neural replay: a point cloud of a fixed subsample of the
// simulated neurons, lit by their spikes over the neural time of the last
// observation, replayed in slow motion. Decorative and schematic: positions
// are annotated soma locations, activity is the model's, not a fly's.
//
// startBrainView(canvas, { raster, hud }) -> { setAtlas, setActivity, setPaused, setVisible, dispose }
//   setAtlas(ArrayBuffer, meta)  the atlas.bin / atlas.json pair (once per run)
//   setActivity(ArrayBuffer)     the activity.bin of the latest observation

import * as THREE from 'https://cdn.jsdelivr.net/npm/three@0.170.0/build/three.module.js';

const CLASS_COLORS = [
  [0.30, 0.34, 0.46], // other
  [0.36, 0.52, 0.86], // optic lobe
  [0.62, 0.48, 0.92], // central brain
  [0.34, 0.62, 0.62], // nerve cord
  [1.00, 0.82, 0.29], // retina
  [0.74, 1.00, 0.20], // KC
  [1.00, 0.54, 0.24], // MBON
  [1.00, 0.29, 0.47], // dopamine
  [1.00, 1.00, 1.00], // DN
];
// Dense classes (thousands of cells packed in a small volume) are drawn dimmer so they do not wash out.
const CLASS_WEIGHT = [0.5, 0.35, 0.6, 0.6, 0.45, 0.35, 1.0, 1.0, 0.8];
const BINS_PER_SECOND = 8;   // 500 ms of neural time replays in 1.25 s
const HOLD_SECONDS = 0.9;    // pause on the afterglow before the replay restarts

function readAtlas(buffer) {
  const view = new DataView(buffer);
  const magic = String.fromCharCode(view.getUint8(0), view.getUint8(1), view.getUint8(2), view.getUint8(3));
  if (magic !== 'SFAT') throw new Error('not an atlas');
  const n = view.getUint32(8, true);
  const xyz = new Int16Array(buffer, 12, n * 3);
  const cls = new Uint8Array(buffer, 12 + n * 6, n);
  const group = new Uint8Array(buffer, 12 + n * 7, n);
  return { n, xyz, cls, group };
}

function readActivity(buffer) {
  const view = new DataView(buffer);
  const magic = String.fromCharCode(view.getUint8(0), view.getUint8(1), view.getUint8(2), view.getUint8(3));
  if (magic !== 'SFAC') throw new Error('not an activity file');
  const tick = view.getUint32(8, true);
  const n = view.getUint32(12, true);
  const bins = view.getUint32(16, true);
  const binMs = view.getUint32(20, true);
  const counts = new Uint8Array(buffer, 24, n * bins);
  return { tick, n, bins, binMs, counts };
}

export function startBrainView(canvas, { raster = null, hud = null } = {}) {
  if (!canvas || typeof canvas.getContext !== 'function') throw new Error('startBrainView needs a canvas');
  let renderer;
  try {
    renderer = new THREE.WebGLRenderer({ canvas, antialias: false, alpha: false, powerPreference: 'low-power' });
  } catch (err) {
    throw new Error(`WebGL unavailable: ${err && err.message ? err.message : err}`);
  }
  renderer.setPixelRatio(Math.min(2, window.devicePixelRatio || 1));
  renderer.setClearColor(0x06070b, 1);

  const scene = new THREE.Scene();
  const camera = new THREE.PerspectiveCamera(38, 16 / 9, 1, 400);
  const pivot = new THREE.Group();
  scene.add(pivot);

  let atlas = null;
  let activity = null;
  let points = null;
  let base = null;      // per-point base colour (n*3)
  let energy = null;    // per-point afterglow
  let groupTotals = null; // 21 x bins
  let binMax = 1;
  let disposed = false;
  let paused = false;
  let visible = true;
  let rafId = 0;
  let lastT = 0;
  let replayT = 0;      // seconds into the replay cycle
  let lastBin = -1;
  let yaw = 0.6;
  let pitch = 0.05;
  let dragYaw = 0;
  let dragPitch = 0;
  let dragging = null;
  const reduced = typeof matchMedia === 'function' && matchMedia('(prefers-reduced-motion: reduce)').matches;

  function rebuild() {
    if (points) { pivot.remove(points); points.geometry.dispose(); points.material.dispose(); points = null; }
    if (!atlas) return;
    const n = atlas.n;
    const positions = new Float32Array(n * 3);
    base = new Float32Array(n * 3);
    energy = new Float32Array(n);
    for (let i = 0; i < n; i += 1) {
      // The atlas is scaled to a 60,000-unit cube; the view is 60 units across. Dataset y grows downward.
      positions[i * 3] = atlas.xyz[i * 3] / 1000;
      positions[i * 3 + 1] = -atlas.xyz[i * 3 + 1] / 1000;
      positions[i * 3 + 2] = atlas.xyz[i * 3 + 2] / 1000;
      const k = Math.min(atlas.cls[i], CLASS_COLORS.length - 1);
      const c = CLASS_COLORS[k];
      const w = CLASS_WEIGHT[k];
      base[i * 3] = c[0] * w; base[i * 3 + 1] = c[1] * w; base[i * 3 + 2] = c[2] * w;
    }
    const geometry = new THREE.BufferGeometry();
    geometry.setAttribute('position', new THREE.BufferAttribute(positions, 3));
    geometry.setAttribute('color', new THREE.BufferAttribute(new Float32Array(n * 3), 3));
    const material = new THREE.PointsMaterial({
      size: 1.8, sizeAttenuation: false, vertexColors: true, transparent: true, opacity: 0.7,
      blending: THREE.AdditiveBlending, depthWrite: false,
    });
    points = new THREE.Points(geometry, material);
    pivot.add(points);
    // Long axis (brain to nerve cord) runs along z in the dataset, brain at low z: stand it up, brain on top.
    pivot.rotation.set(0, 0, 0);
    points.rotation.x = Math.PI / 2;
    // Fit the camera to the cloud.
    geometry.computeBoundingSphere();
    const r = geometry.boundingSphere ? geometry.boundingSphere.radius : 30;
    camera.position.set(0, 0, r * 2.7);
    camera.near = Math.max(0.5, r * 0.05);
    camera.far = r * 8;
    camera.updateProjectionMatrix();
    paintBin(-1);
  }

  function totals() {
    groupTotals = null;
    if (!atlas || !activity || activity.n !== atlas.n) return;
    const { bins, counts, n } = activity;
    groupTotals = new Float32Array(21 * bins);
    binMax = 1;
    for (let b = 0; b < bins; b += 1) {
      let sum = 0;
      for (let i = 0; i < n; i += 1) {
        const v = counts[b * n + i];
        sum += v;
        const g = atlas.group[i];
        if (g) groupTotals[(g - 1) * bins + b] += v;
      }
      if (sum > binMax) binMax = sum;
    }
  }

  // Colour every point for one slice of neural time (bin < 0: resting, dim).
  function paintBin(bin) {
    if (!points) return;
    const n = atlas.n;
    const colors = points.geometry.attributes.color.array;
    const have = activity && activity.n === n && bin >= 0 && bin < activity.bins;
    for (let i = 0; i < n; i += 1) {
      const hit = have ? Math.min(1, activity.counts[bin * n + i] / 2) : 0;
      energy[i] = Math.max(energy[i] * 0.55, hit);
      const k = 0.16 + 1.6 * energy[i];
      colors[i * 3] = base[i * 3] * k;
      colors[i * 3 + 1] = base[i * 3 + 1] * k;
      colors[i * 3 + 2] = base[i * 3 + 2] * k;
    }
    points.geometry.attributes.color.needsUpdate = true;
    drawRaster(bin);
    if (hud) {
      let spikes = 0;
      if (have) for (let i = 0; i < n; i += 1) spikes += activity.counts[bin * n + i];
      const perSecond = have ? spikes * (1000 / activity.binMs) : 0;
      hud.textContent = have ? (perSecond >= 1e6 ? `${(perSecond / 1e6).toFixed(2)}M` : perSecond >= 1e3 ? `${(perSecond / 1e3).toFixed(1)}K` : String(Math.round(perSecond))) : '—';
    }
  }

  function drawRaster(bin) {
    if (!raster) return;
    const ctx = raster.getContext('2d');
    const w = raster.width;
    const h = raster.height;
    ctx.fillStyle = '#06070b';
    ctx.fillRect(0, 0, w, h);
    if (!groupTotals || !activity) return;
    const bins = activity.bins;
    const cw = w / bins;
    const rh = h / 21;
    let max = 1;
    for (let i = 0; i < groupTotals.length; i += 1) if (groupTotals[i] > max) max = groupTotals[i];
    for (let g = 0; g < 21; g += 1) {
      for (let b = 0; b < bins; b += 1) {
        const v = groupTotals[g * bins + b] / max;
        if (v <= 0) continue;
        const a = 0.15 + 0.85 * v;
        ctx.fillStyle = b === bin ? `rgba(255,255,255,${a})` : `rgba(189,255,50,${a})`;
        ctx.fillRect(b * cw + 1, g * rh + 1, Math.max(1, cw - 2), Math.max(1, rh - 1));
      }
    }
    if (bin >= 0) {
      ctx.fillStyle = 'rgba(255,255,255,0.35)';
      ctx.fillRect(bin * cw, 0, 1, h);
    }
  }

  function resize() {
    const w = canvas.clientWidth || 0;
    const h = canvas.clientHeight || 0;
    if (!w || !h) return false;
    if (canvas.width !== Math.round(w * renderer.getPixelRatio()) || canvas.height !== Math.round(h * renderer.getPixelRatio())) {
      renderer.setSize(w, h, false);
      camera.aspect = w / h;
      camera.updateProjectionMatrix();
    }
    return true;
  }

  function frame(nowMs) {
    rafId = 0;
    if (disposed || !visible || paused || document.hidden) return;
    if (!resize()) { rafId = requestAnimationFrame(frame); return; }
    const t = nowMs / 1000;
    const dt = lastT ? Math.min(0.05, t - lastT) : 0.016;
    lastT = t;
    if (!reduced) yaw += dt * 0.12;
    pivot.rotation.y = yaw + dragYaw;
    pivot.rotation.x = pitch + dragPitch;
    if (activity && atlas && activity.n === atlas.n) {
      replayT += dt;
      const cycle = activity.bins / BINS_PER_SECOND + HOLD_SECONDS;
      if (replayT >= cycle) { replayT -= cycle; }
      const bin = replayT < activity.bins / BINS_PER_SECOND ? Math.floor(replayT * BINS_PER_SECOND) : -1;
      if (bin !== lastBin) { lastBin = bin; paintBin(bin); }
    }
    renderer.render(scene, camera);
    rafId = requestAnimationFrame(frame);
  }

  function kick() {
    if (disposed) return;
    if (!rafId && visible && !paused && !document.hidden) { lastT = 0; rafId = requestAnimationFrame(frame); }
    else if (paused || !visible) { if (resize()) renderer.render(scene, camera); }
  }

  // Drag to orbit.
  canvas.addEventListener('pointerdown', (e) => { dragging = { x: e.clientX, y: e.clientY, yaw: dragYaw, pitch: dragPitch }; canvas.setPointerCapture(e.pointerId); });
  canvas.addEventListener('pointermove', (e) => {
    if (!dragging) return;
    dragYaw = dragging.yaw + (e.clientX - dragging.x) * 0.008;
    dragPitch = Math.max(-1.2, Math.min(1.2, dragging.pitch + (e.clientY - dragging.y) * 0.006));
    if (paused) renderer.render(scene, camera);
  });
  const stop = () => { dragging = null; };
  canvas.addEventListener('pointerup', stop);
  canvas.addEventListener('pointercancel', stop);

  return {
    setAtlas(buffer, meta) {
      atlas = readAtlas(buffer);
      atlas.meta = meta || null;
      energy = null;
      rebuild();
      totals();
      paintBin(-1);
      kick();
    },
    setActivity(buffer) {
      activity = readActivity(buffer);
      replayT = 0;
      lastBin = -1;
      totals();
      if (points) { energy.fill(0); paintBin(0); }
      kick();
    },
    setPaused(value) { paused = !!value; if (paused && rafId) { cancelAnimationFrame(rafId); rafId = 0; } kick(); },
    setVisible(value) { visible = !!value; if (!visible && rafId) { cancelAnimationFrame(rafId); rafId = 0; } kick(); },
    dispose() { disposed = true; if (rafId) cancelAnimationFrame(rafId); renderer.dispose(); },
  };
}
