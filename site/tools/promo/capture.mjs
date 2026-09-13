// Render the fly scene frame by frame into PNGs with a virtual clock, then encode
// an MP4 for social posts. Needs the global playwright package and an ffmpeg
// with libx264 (FFMPEG env or `pip install imageio-ffmpeg`).
//
//   python site/tools/demo.py --run runs/paper --out /tmp/promo --no-live-board
//   node site/tools/promo/capture.mjs /tmp/promo /tmp/promo.mp4 [seconds]
import fs from 'node:fs';
import http from 'node:http';
import path from 'node:path';
import { execFileSync } from 'node:child_process';
const { chromium } = await import('playwright').catch(() => import(path.join(execFileSync('npm', ['root', '-g']).toString().trim(), 'playwright', 'index.mjs')));

const [dir, outFile, secondsArg] = process.argv.slice(2);
if (!dir || !outFile) throw new Error('usage: capture.mjs <demo dir> <out.mp4> [seconds]');
const FPS = 30;
const SECONDS = Number(secondsArg) || 26;
const W = 1280, H = 720;
const MIME = { '.html': 'text/html', '.js': 'text/javascript', '.css': 'text/css', '.svg': 'image/svg+xml', '.png': 'image/png' };

const server = http.createServer((req, res) => {
  const file = path.join(dir, decodeURIComponent(new URL(req.url, 'http://x').pathname));
  if (!file.startsWith(path.resolve(dir)) || !fs.existsSync(file) || fs.statSync(file).isDirectory()) { res.writeHead(404); res.end(); return; }
  res.writeHead(200, { 'content-type': MIME[path.extname(file)] || 'application/octet-stream' });
  fs.createReadStream(file).pipe(res);
});
await new Promise((r) => server.listen(0, '127.0.0.1', r));
const port = server.address().port;

// The headless browser has no network here: vendor three.js and the fonts next to the page.
const FONTS = 'https://fonts.googleapis.com/css2?family=Press+Start+2P&family=Space+Mono:wght@400;700&family=Work+Sans:wght@500;600;700&display=swap';
const UA = 'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120 Safari/537.36';
if (!fs.existsSync(path.join(dir, 'three.module.js'))) execFileSync('curl', ['-sS', '-o', path.join(dir, 'three.module.js'), 'https://cdn.jsdelivr.net/npm/three@0.170.0/build/three.module.js']);
if (!fs.existsSync(path.join(dir, 'fonts.css'))) {
  execFileSync('curl', ['-sS', '-A', UA, '-o', path.join(dir, 'fonts.css'), FONTS]);
  fs.mkdirSync(path.join(dir, 'fonts'), { recursive: true });
  let css = fs.readFileSync(path.join(dir, 'fonts.css'), 'utf8');
  [...new Set(css.match(/https:\/\/fonts\.gstatic\.com[^)]*/g) || [])].forEach((u, i) => {
    execFileSync('curl', ['-sS', '-o', path.join(dir, 'fonts', `f${i}.woff2`), u]);
    css = css.split(u).join(`fonts/f${i}.woff2`);
  });
  fs.writeFileSync(path.join(dir, 'fonts.css'), css);
}
const sceneSrc = path.join(dir, 'scene.js');
fs.writeFileSync(sceneSrc, fs.readFileSync(sceneSrc, 'utf8').replace("'https://cdn.jsdelivr.net/npm/three@0.170.0/build/three.module.js'", "'./three.module.js'"));
fs.copyFileSync(new URL('./promo.html', import.meta.url), path.join(dir, 'promo.html'));

const frames = path.join(path.dirname(outFile), 'promo-frames');
fs.rmSync(frames, { recursive: true, force: true });
fs.mkdirSync(frames, { recursive: true });

const browser = await chromium.launch({ args: ['--use-gl=angle', '--use-angle=swiftshader', '--enable-unsafe-swiftshader', '--ignore-gpu-blocklist'] });
const page = await browser.newPage({ viewport: { width: W, height: H }, deviceScaleFactor: 1 });
page.on('pageerror', (e) => console.error('page error:', e.message));
page.on('console', (m) => { if (m.type() === 'error') console.error('console:', m.text()); });
const start = JSON.parse(fs.readFileSync(path.join(dir, 'demo-data.js'), 'utf8').replace(/^window\.__STONKFLY_DEMO = /, '').replace(/;\s*$/, '')).captured_at * 1000;
await page.addInitScript((start) => {
  // A virtual clock: every timer the scene reads advances only when __step runs.
  let now = 0;
  const callbacks = new Map();
  let id = 0;
  performance.now = () => now;
  Date.now = () => start + now;
  window.requestAnimationFrame = (cb) => { callbacks.set(++id, cb); return id; };
  window.cancelAnimationFrame = (i) => { callbacks.delete(i); };
  window.__step = (ms) => {
    now += ms;
    const due = [...callbacks.entries()];
    callbacks.clear();
    for (const [, cb] of due) cb(now);
  };
  window.__now = () => now;
}, start);
await page.goto(`http://127.0.0.1:${port}/promo.html`, { waitUntil: 'load' });
await page.waitForFunction(() => window.__ready === true, null, { timeout: 60000 });
await page.evaluate(() => window.__step(16));

// Camera: a slow drag from the left of home to the right, never released, so the
// scene's own sway rides on top. Overlays fade on a schedule.
// Camera drag in canvas pixels, from and to, over the clip. Positive turns the camera
// toward the window wall and shows the monitor's side; negative goes behind the fly and faces the screen.
const DRAG_FROM = Number(process.env.PROMO_DRAG_FROM ?? 25);
const DRAG_TO = Number(process.env.PROMO_DRAG_TO ?? -110);
const total = FPS * SECONDS;
const canvas = await page.$('#output');
const box = await canvas.boundingBox();
const startX = box.x + box.width * 0.5;
await page.mouse.move(startX, box.y + box.height * 0.5);
await page.mouse.down();
await page.mouse.move(startX + DRAG_FROM, box.y + box.height * 0.5);
await page.evaluate(() => window.__step(16));
function ease(u) { return u < 0.5 ? 2 * u * u : 1 - Math.pow(-2 * u + 2, 2) / 2; }
function fade(t, from, to, out = Infinity, outLen = 0.6) {
  if (t < from) return 0;
  if (t < to) return (t - from) / (to - from);
  if (t < out) return 1;
  return Math.max(0, 1 - (t - out) / outLen);
}
for (let i = 0; i < total; i++) {
  const t = i / FPS;
  const u = Math.min(1, Math.max(0, (t - 1.0) / (SECONDS - 5)));
  await page.mouse.move(startX + DRAG_FROM + (DRAG_TO - DRAG_FROM) * ease(u), box.y + box.height * 0.5);
  await page.evaluate(({ t, S }) => {
    const set = (id, v) => { document.getElementById(id).style.opacity = String(v); };
    const f = (from, to, out, len) => (t < from ? 0 : t < to ? (t - from) / (to - from) : out !== undefined && t >= out ? Math.max(0, 1 - (t - out) / len) : 1);
    set('brand', f(0.6, 1.6, S - 3.2, 0.5));
    set('line', f(3.0, 4.0, S - 3.2, 0.5));
    set('url', f(1.2, 2.2, S - 3.2, 0.5));
    set('card', f(S - 3.0, S - 2.3));
    window.__step(1000 / 30);
  }, { t, S: SECONDS });
  await page.screenshot({ path: path.join(frames, `f${String(i).padStart(4, '0')}.png`), clip: { x: 0, y: 0, width: W, height: H }, animations: 'disabled', caret: 'hide' });
  if (i % 60 === 0) console.log(`frame ${i}/${total}`);
}
await page.mouse.up();
await browser.close();
server.close();

const ffmpeg = process.env.FFMPEG || execFileSync('python3', ['-c', 'import imageio_ffmpeg as f; print(f.get_ffmpeg_exe())']).toString().trim();
execFileSync(ffmpeg, ['-y', '-hide_banner', '-loglevel', 'error', '-framerate', String(FPS), '-i', path.join(frames, 'f%04d.png'),
  '-c:v', 'libx264', '-preset', 'slow', '-crf', '18', '-pix_fmt', 'yuv420p', '-movflags', '+faststart', outFile], { stdio: 'inherit' });
console.log(JSON.stringify({ out: outFile, frames: total, seconds: SECONDS, size: fs.statSync(outFile).size }));
