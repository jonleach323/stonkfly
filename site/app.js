// Sat Rush Fly watch page. Reads /api/state and /api/board, writes nothing.
// Every render is wrapped so a missing or odd field never takes the page down.

const POLL_MS = 2000;
const POLL_HIDDEN_MS = 10000;
const FETCH_TIMEOUT_MS = 8000;
const LIVE_BOARD_MAX_AGE_MS = 15000;
const HEARTBEAT_MAX_AGE_S = 180;
const NEURONS_DEFAULT = 166700;
const ROUND_SLOTS = 200;

const $ = (id) => document.getElementById(id);
const intFmt = new Intl.NumberFormat("en-US");
const moneyFmt = new Intl.NumberFormat("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
const moneyFmt4 = new Intl.NumberFormat("en-US", { minimumFractionDigits: 4, maximumFractionDigits: 4 });
const reducedMotion = window.matchMedia("(prefers-reduced-motion: reduce)");

// Static demo: a captured snapshot embedded by site/tools/demo.py. The clock freezes at capture time,
// polling stops after the first render, and the footer says so. Nothing here fetches or updates.
const DEMO = typeof window !== "undefined" && window.__STONKFLY_DEMO && typeof window.__STONKFLY_DEMO === "object" ? window.__STONKFLY_DEMO : null;
function nowMs() { return DEMO && num(DEMO.captured_at) !== null ? Number(DEMO.captured_at) * 1000 : Date.now(); }

const app = {
  base: "", baseFallback: false, baseFails: 0, // "" = this deployment's /api; else the worker's own origin
  state: null, stateAt: 0, stateFails: 0, everFetched: false, lastError: "", stateSkew: 0,
  board: null, boardAt: 0, boardSkew: null, boardFails: 0,
  scene: null, sceneLoading: false, sceneFailed: false,
  paused: false, view: "watch", tab: "rounds",
  frameSha: null, roundsKey: "", decisionsKey: "", chartKey: "",
  timers: {},
};

/* ---------------- helpers ---------------- */

function num(x) {
  if (x === null || x === undefined || x === "") return null;
  const v = Number(x);
  return Number.isFinite(v) ? v : null;
}
function pad(n) { return String(n).padStart(2, "0"); }
function fmtInt(x) { const v = num(x); return v === null ? "—" : intFmt.format(Math.round(v)); }
function fmtRush(x) { const v = num(x); return v === null ? "0" : v >= 100 ? intFmt.format(Math.round(v)) : v >= 1 ? v.toFixed(2) : v.toFixed(4); }
function roundTo(v, digits) { const f = 10 ** digits; return Math.round(v * f) / f; }
// Sign after rounding, so a sub-cent value never prints as "+$0.00" or "-$0.00"; per-round P&L uses 4 decimals.
function fmtMoney(x, { sign = false, digits = 2 } = {}) {
  const v = num(x);
  if (v === null) return "—";
  const c = roundTo(v, digits);
  const s = c < 0 ? "-" : sign && c > 0 ? "+" : "";
  return `${s}$${(digits === 4 ? moneyFmt4 : moneyFmt).format(Math.abs(c))}`;
}
function fmtPct(x, { sign = true } = {}) {
  const v = num(x);
  if (v === null) return "—";
  const c = roundTo(v, 2);
  const s = c < 0 ? "-" : sign && c > 0 ? "+" : "";
  return `${s}${Math.abs(c).toFixed(2)}%`;
}
function fmtSats(n, usd) {
  const v = num(n);
  if (v === null) return "—";
  const u = num(usd);
  return `${intFmt.format(v)} SATS${u === null ? "" : ` (${fmtMoney(u)})`}`;
}
/** Wall time in Unix seconds on the server's clock: `publication.served_at` corrects this browser's clock. */
function nowS() { return nowMs() / 1000 - app.stateSkew; }
// UTC throughout (the daily deploy limit is a UTC day); a date is added once a stamp is older than a day.
function fmtTime(t) {
  const v = num(t);
  if (v === null) return "—";
  const d = new Date(v * 1000);
  const hm = `${pad(d.getUTCHours())}:${pad(d.getUTCMinutes())}`;
  return nowS() - v > 86400 ? `${pad(d.getUTCMonth() + 1)}-${pad(d.getUTCDate())} ${hm}` : hm;
}
function fmtAge(t) {
  const v = num(t);
  if (v === null) return "—";
  const s = Math.max(0, nowS() - v);
  if (s < 60) return `${Math.floor(s)}s`;
  if (s < 3600) return `${Math.floor(s / 60)}m`;
  if (s < 86400) return `${Math.floor(s / 3600)}h`;
  return `${Math.floor(s / 86400)}d`;
}
function fmtClock(seconds) {
  const s = Math.max(0, Math.ceil(seconds));
  return `${Math.floor(s / 60)}:${pad(s % 60)}`;
}
function signClass(x, digits = 2) {
  const v = num(x);
  if (v === null) return "";
  const c = roundTo(v, digits);
  return c > 0 ? "pos" : c < 0 ? "neg" : "";
}
function setText(id, text) {
  const el = $(id);
  if (el && el.textContent !== text) el.textContent = text;
}
function setChip(id, text, tone) {
  const el = $(id);
  if (!el) return;
  if (el.textContent !== text) el.textContent = text;
  el.className = `chip${tone ? ` ${tone}` : ""}`;
}
function el(tag, attrs = {}, children = []) {
  const node = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (v === null || v === undefined) continue;
    if (k === "class") node.className = v;
    else if (k === "text") node.textContent = v;
    else node.setAttribute(k, v);
  }
  for (const c of [].concat(children)) {
    if (c === null || c === undefined) continue;
    node.append(typeof c === "string" ? document.createTextNode(c) : c);
  }
  return node;
}
function replaceChildren(node, children) {
  if (!node) return;
  node.replaceChildren(...children);
}
function safe(fn) {
  try { return fn(); } catch (e) { console.error(e); return undefined; }
}
function explorerTx(signature, network) {
  return `https://solscan.io/tx/${encodeURIComponent(signature)}${network === "devnet" ? "?cluster=devnet" : ""}`;
}
function explorerAccount(address, network) {
  return `https://solscan.io/account/${encodeURIComponent(address)}${network === "devnet" ? "?cluster=devnet" : ""}`;
}
/** Chance of a hit for the recent settled rounds: picking k of 21 tiles hits k/21 of the time. */
function chanceHitRate(s) {
  const settled = (Array.isArray(s.rounds) ? s.rounds : []).filter((r) => r.won === true || r.won === false);
  if (!settled.length) return null;
  const k = settled.reduce((a, r) => a + (num(r.tile_count) ?? (Array.isArray(r.tiles) ? r.tiles.length : 0)), 0) / settled.length;
  return (k / 21) * 100;
}
/** Flag tables wider than their box so the CSS can fade the cut edge (overlay scrollbars show nothing). */
function markOverflow() {
  document.querySelectorAll(".scroll").forEach((box) => {
    box.classList.toggle("overflowing", box.scrollWidth > box.clientWidth + 1);
    box.classList.toggle("at-end", box.scrollLeft + box.clientWidth >= box.scrollWidth - 1);
  });
}
function tileList(tiles) {
  return Array.isArray(tiles) ? tiles.map((t) => String(t)).join(" ") : "—";
}

/* ---------------- fetching ---------------- */

function apiUrl(path) {
  return app.base ? app.base + path : path;
}

/**
 * Where the data comes from. On Vercel, /api/config names the worker's own origin (its `stonkfly serve`
 * behind your domain) so the browser reads it directly and the functions stay idle; without it, or if
 * the origin stops answering, the page falls back to this deployment's /api functions.
 */
async function loadConfig() {
  if (DEMO) return;
  try {
    const r = await getJSON("/api/config");
    const origin = r.ok && r.body && typeof r.body.origin === "string" ? r.body.origin.trim().replace(/\/+$/, "") : "";
    if (/^https?:\/\/\S+$/.test(origin)) app.base = origin;
  } catch (_) { /* no config endpoint: same-origin API */ }
}

async function getJSON(url) {
  if (DEMO) {
    if (url.startsWith("/api/state")) return { ok: true, status: 200, body: DEMO.state || null };
    if (url.startsWith("/api/board")) return { ok: !!DEMO.board, status: DEMO.board ? 200 : 503, body: DEMO.board || null };
    return { ok: false, status: 404, body: null };
  }
  const ctl = new AbortController();
  const timer = setTimeout(() => ctl.abort(), FETCH_TIMEOUT_MS);
  try {
    const r = await fetch(url.startsWith("/api/") && url !== "/api/config" ? apiUrl(url) : url, { cache: "no-store", signal: ctl.signal });
    let body = null;
    try { body = await r.json(); } catch (_) { body = null; }
    if (app.base && r.ok) app.baseFails = 0;
    return { ok: r.ok, status: r.status, body };
  } catch (_) {
    // A direct origin that stops answering three times in a row hands over to this deployment's functions.
    if (app.base && ++app.baseFails >= 3) { app.base = ""; app.baseFallback = true; }
    return { ok: false, status: 0, body: null };
  } finally {
    clearTimeout(timer);
  }
}

function schedule(name, fn, ms) {
  clearTimeout(app.timers[name]);
  if (DEMO) return; // one render of the captured snapshot, then nothing moves
  app.timers[name] = setTimeout(fn, ms);
}
function pollDelay() { return document.hidden ? POLL_HIDDEN_MS : POLL_MS; }

async function pollState() {
  const r = await getJSON("/api/state");
  app.everFetched = true;
  const b = r.body && typeof r.body === "object" && "ready" in r.body ? r.body : null;
  // A not-ready document carrying an error is the hosting layer failing to read the snapshot, not the
  // worker starting over: keep the last good snapshot and count a failure (three in a row reads DISCONNECTED).
  const hostingError = !!(b && b.ready === false && b.error && app.state && app.state.ready);
  if (r.ok && b && !hostingError) {
    const wasReady = !!(app.state && app.state.ready);
    app.state = b;
    app.stateAt = nowMs();
    app.stateFails = 0;
    app.lastError = "";
    const served = num(b.publication && b.publication.served_at);
    if (served !== null) app.stateSkew = nowMs() / 1000 - served;
    if (wasReady && !b.ready) safe(resetReady);
  } else {
    app.stateFails += 1;
    app.lastError = String((b && b.error) || (r.status ? `HTTP ${r.status}` : ""));
  }
  safe(renderAll);
  sceneUpdate();
  schedule("state", pollState, pollDelay());
}

function sceneUpdate() {
  if (!app.scene || !app.state) return;
  safe(() => app.scene.update(app.state, (currentBoard() || {}).board || null, app.boardSkew ?? 0, { stale: app.stateFails >= 3 }));
}

async function pollBoard() {
  const r = await getJSON("/api/board");
  const b = r.body;
  if (r.ok && b && b.live === true && Array.isArray(b.tile_stakes)) {
    app.board = b;
    app.boardAt = nowMs();
    app.boardFails = 0;
    const fetched = num(b.fetched_at);
    // Countdown runs on the server's clock. `now - fetched_at` overstates the skew by the response's age
    // (proxy memo, CDN, latency), so keep the smallest lag seen; a jump of 30 s means a clock changed.
    if (fetched !== null) {
      const lag = nowMs() / 1000 - fetched;
      if (app.boardSkew === null || lag < app.boardSkew || lag - app.boardSkew > 30) app.boardSkew = lag;
    }
  } else {
    app.boardFails += 1;
  }
  safe(renderBoard);
  safe(tick);
  sceneUpdate();
  schedule("board", pollBoard, pollDelay());
}

/** Which board to draw: the live proxy when fresh, else the retina's snapshot, else a stale live board. */
function currentBoard() {
  const s = app.state;
  const liveFresh = app.board && nowMs() - app.boardAt < LIVE_BOARD_MAX_AGE_MS;
  if (liveFresh) return { board: app.board, source: "live" };
  if (s && s.ready && s.board && Array.isArray(s.board.tile_stakes)) return { board: s.board, source: "snapshot" };
  if (app.board) return { board: app.board, source: "stale" };
  return null;
}

/* ---------------- rendering ---------------- */

function renderAll() {
  const s = app.state;
  const ready = !!(s && s.ready);
  safe(renderFooter);
  if (!ready) { safe(renderWaiting); safe(renderBoard); return; }
  safe(() => renderBrain(s));
  safe(() => renderPick(s));
  safe(() => renderStack(s));
  safe(() => renderHoldings(s));
  safe(() => renderRounds(s));
  safe(() => renderDecisions(s));
  safe(() => renderPerf(s));
  safe(() => renderSensory(s));
  safe(renderBoard);
}

function renderWaiting() {
  const text = app.everFetched && !app.state ? "DISCONNECTED" : "WAITING FOR WORKER";
  setChip("fresh-chip", text, app.state ? "" : "bad");
  setChip("pick-status", "WAITING", "");
  setText("pick-tiles", text);
  setText("stimulus", "—");
  setChip("pick-meta", "OBSERVATION —", "");
  setText("sensory-meta", text);
  if (!app.state) return;
  const e = $("equity");
  if (e) { e.firstElementChild.textContent = "$—"; e.lastElementChild.textContent = ""; }
}

/** The worker started over (a not-ready snapshot after a ready one): drop every number from the old one. */
function resetReady() {
  app.roundsKey = ""; app.decisionsKey = ""; app.chartKey = ""; app.frameSha = null;
  for (const id of ["pnl", "in-play", "h-cash", "h-inplay", "h-sats", "h-rounds", "h-hit", "h-strikes", "h-tickets", "h-hashrate", "h-rush", "s-cash", "s-fees", "s-refunds", "s-sats", "s-best", "perf-chip", "b-spikes", "b-edges", "b-time"]) setText(id, "—");
  for (const id of ["pnl", "s-best"]) { const n = $(id); if (n) n.className = "num"; }
  setChip("perf-chip", "—", "");
  setText("value-unit", "USDC");
  setText("pick-round", "");
  setText("readout-cells", "");
  const note = $("stage-note");
  if (note) { note.textContent = ""; note.classList.remove("bad"); }
  if ($("minigrid")) replaceChildren($("minigrid"), []);
  replaceChildren($("rounds-body"), [el("tr", {}, el("td", { colspan: "8", class: "dim", text: "WAITING FOR WORKER" }))]);
  replaceChildren($("decisions-body"), [el("tr", {}, el("td", { colspan: "6", class: "dim", text: "WAITING FOR WORKER" }))]);
  const chart = $("equity-chart");
  if (chart) chart.replaceChildren();
  const img = $("sensory");
  if (img) { img.hidden = true; const empty = $("sensory-empty"); if (empty) empty.hidden = false; }
  markOverflow();
}

function renderBrain(s) {
  const n = s.neural || {};
  const model = s.model || {};
  setText("b-neurons", fmtInt(model.neurons ?? NEURONS_DEFAULT));
  setText("b-spikes", fmtInt(n.total_spikes));
  const edges = $("b-edges");
  if (edges) {
    const learning = !(s.settings && s.settings.learning === false);
    replaceChildren(edges, [fmtInt(n.changed_edges), el("small", { text: learning ? "KC-MBON EDGES" : "FROZEN MEMORY" })]);
  }
  const time = $("b-time");
  if (time) {
    const ms = num(n.brain_ms);
    const text = ms === null ? "—" : ms < 1000 ? `${Math.round(ms)} ms` : `${(ms / 1000).toFixed(1)} s`;
    replaceChildren(time, [text, el("small", { text: "NEURAL TIME" })]);
  }
  const note = $("stage-note");
  if (note) {
    const stub = !!(s.status && s.status.stub_brain);
    note.textContent = stub ? "STUB BRAIN · SEEDED NOISE" : "";
    note.classList.toggle("bad", stub);
  }
}

function renderPick(s) {
  const n = s.neural || {};
  const picks = new Set(Array.isArray(n.tiles) ? n.tiles : []);
  // Older readouts publish `excess_rel: []` and only `excess_hz`; an empty array is not a ranking.
  const excess = [n.excess_rel, n.excess_hz].find((a) => Array.isArray(a) && a.length) || [];
  const rel = excess === n.excess_rel;
  const hz = Array.isArray(n.group_hz) ? n.group_hz : [];
  let top = -1;
  let best = -Infinity;
  excess.forEach((v, i) => { const x = num(v); if (x !== null && x > best) { best = x; top = i; } });
  const grid = $("minigrid");
  if (grid) {
    const items = [];
    for (let i = 0; i < 21; i++) {
      const tile = i + 1;
      const lit = picks.has(tile);
      const bits = [];
      if (num(hz[i]) !== null) bits.push(`${num(hz[i]).toFixed(1)} Hz`);
      if (num(excess[i]) !== null) bits.push(`excess ${num(excess[i]).toFixed(2)}${rel ? "" : " Hz"}`);
      items.push(el("li", {
        class: `${lit ? "lit" : ""}${i === top && lit ? " top" : ""}`.trim() || null,
        title: `Tile ${tile}${bits.length ? ` · ${bits.join(" · ")}` : ""}`,
        "aria-label": `Tile ${tile}${lit ? ", selected" : ""}`,
        text: String(tile),
      }));
    }
    replaceChildren(grid, items);
  }
  const d0 = Array.isArray(s.decisions) && s.decisions.length ? s.decisions[0] : null;
  const roundId = d0 && d0.round_id != null ? d0.round_id : s.board && s.board.round_id;
  const count = picks.size;
  const tiles = Array.isArray(n.tiles) ? n.tiles : [];
  const line = $("pick-tiles");
  if (line) {
    replaceChildren(line, [
      `${roundId != null ? `R#${roundId} · ` : ""}${count} TILE${count === 1 ? "" : "S"} `,
      el("span", { class: "tilelist", text: tiles.length ? `· ${tileList(tiles)}` : "" }),
    ]);
  }
  const status = d0 ? String(d0.status || "").toUpperCase() : "";
  let statusText = status || "—";
  let tone = "";
  if (status === "VETO") { statusText = `VETO${d0.reason ? ` · ${d0.reason}` : ""}`; tone = "bad"; }
  else if (status === "CONFIRMED") tone = "ok";
  else if (status === "FAILED") tone = "bad";
  else if (status === "PAPER") { statusText = "PAPER · SIMULATED"; tone = "info"; }
  setChip("pick-status", statusText.toUpperCase(), tone);
  setChip("pick-meta", `OBSERVATION #${fmtInt(s.tick)} · ${fmtTime(s.observed_at)} UTC`, "");

  const stim = $("stimulus");
  if (stim) {
    const kind = String(n.stimulus || "none");
    const ms = num(n.stimulus_ms);
    const dur = ms && ms > 0 ? `${Math.round(ms)} ms` : "200 ms";
    let text = "NO ADDED REINFORCEMENT";
    if (kind === "reward") text = `REWARD INPUT · ${dur} INTO 15 PAM11 CELLS · ${fmtInt(n.reward_spikes)} SPIKES`;
    else if (kind === "aversive") text = `AVERSIVE INPUT · ${dur} INTO 2 PPL101 CELLS · ${fmtInt(n.aversive_spikes)} SPIKES`;
    const kc = num(n.KC_spikes);
    if (kc !== null) text += ` · KC ${fmtInt(kc)} SPIKES`;
    stim.textContent = text;
    stim.className = `stimulus ${kind === "reward" ? "reward" : kind === "aversive" ? "aversive" : ""}`.trim();
  }
  const cells = $("readout-cells");
  if (cells) {
    const c = s.readout && num(s.readout.cells);
    cells.textContent = c ? ` (${fmtInt(c)} cells in this graph)` : "";
  }
}

function renderStack(s) {
  const p = s.portfolio || {};
  const equity = $("equity");
  if (equity) {
    const v = num(p.equity);
    const dollars = equity.firstElementChild;
    const cents = equity.lastElementChild;
    if (v === null) { dollars.textContent = "$—"; cents.textContent = ""; }
    else {
      const [whole, frac] = moneyFmt.format(Math.abs(v)).split(".");
      dollars.textContent = `${v < 0 ? "-" : ""}$${whole}`;
      cents.textContent = `.${frac}`;
    }
  }
  setText("value-unit", s.mode === "live" ? "USDC · LIVE WALLET" : "USDC · SIMULATED");
  setText("how-execution", s.mode === "live" ? "LIVE · DEPLOYS GO TO SOLANA FROM A DEDICATED WALLET" : "PAPER · SETTLED FROM THE REAL WINNING TILE, NOTHING SENT");
  const pnl = $("pnl");
  if (pnl) {
    pnl.textContent = `${fmtMoney(p.pnl, { sign: true })} · ${fmtPct(p.pnl_percent)}`;
    pnl.className = `num ${signClass(p.pnl)}`.trim();
  }
  const inPlay = $("in-play");
  if (inPlay) {
    const open = Array.isArray(p.open_rounds) ? p.open_rounds.length : 0;
    replaceChildren(inPlay, [fmtMoney(p.in_play), el("small", { text: `${open} OPEN ROUND${open === 1 ? "" : "S"}` })]);
  }
  renderFreshChip(s);
}

/** One verdict for the header chip and the footer: what the page can honestly say about the worker. */
function workerHealth(s) {
  const st = s.status || {};
  if (app.stateFails >= 3) return "disconnected";
  if (st.halted) return "halted";
  // `status.running` is frozen at publish time; a worker killed before its final publish keeps it true.
  if (!st.running || nowS() - (num(st.heartbeat) ?? 0) > HEARTBEAT_MAX_AGE_S) return "stale";
  return "ok";
}

function renderFreshChip(s) {
  const health = workerHealth(s);
  const age = `OBSERVATION ${fmtAge(s.observed_at)} AGO`;
  if (health === "disconnected") setChip("fresh-chip", `DISCONNECTED · LAST ${age}`, "bad");
  else if (health === "halted") setChip("fresh-chip", `HALTED · ${String(s.status.halted).toUpperCase()}`, "bad");
  else if (health === "stale") setChip("fresh-chip", `STALE · ${age}`, "");
  else setChip("fresh-chip", age, nowS() - (num(s.observed_at) ?? 0) < 120 ? "ok" : "");
}

function renderHoldings(s) {
  const p = s.portfolio || {};
  setText("h-cash", fmtMoney(p.cash));
  setText("h-inplay", fmtMoney(p.in_play));
  const sats = $("h-sats");
  if (sats) {
    const v = num(p.sats_won);
    replaceChildren(sats, v === null ? ["—"] : [`${intFmt.format(v)} SATS`, el("small", { text: fmtMoney(p.sats_won_usd) })]);
  }
  setText("h-rounds", `${fmtInt(p.rounds_won)} / ${fmtInt(p.rounds_played)}`);
  const hitEl = $("h-hit");
  if (hitEl) {
    const hit = num(p.hit_rate_percent);
    const chance = num(p.expected_hit_rate_percent) ?? chanceHitRate(s);
    replaceChildren(hitEl, hit === null ? ["—"] : [`${hit.toFixed(1)}%`, el("small", { text: chance === null ? "" : `CHANCE ${chance.toFixed(1)}%` })]);
  }
  // Sat Strikes: rounds the fly played that carried the strike vault's bonus, and how many it hit.
  const strikes = $("h-strikes");
  if (strikes) {
    const played = num(p.strikes_played);
    const won = num(p.strike_won_usd);
    replaceChildren(strikes, played === null ? ["—"] : [`${fmtInt(p.strikes_hit)} / ${fmtInt(played)}`, el("small", { text: won ? `WON ${fmtMoney(won)}` : "NONE HIT" })]);
  }
  // Vault tickets are only known for a real wallet; the API does not publish the formula.
  const tickets = $("h-tickets");
  if (tickets) {
    const v = p.vaults;
    if (s.mode !== "live") replaceChildren(tickets, ["—", el("small", { text: "NO WALLET" })]);
    else if (!v) replaceChildren(tickets, ["—", el("small", { text: "NOT READ YET" })]);
    else {
      const e = v.epoch, o = v.one_btc;
      const line = `EPOCH ${e ? fmtInt(e.tickets) : 0} · 1 BTC ${o ? fmtInt(o.tickets) : 0}`;
      const wins = [];
      if (e && e.rank != null) wins.push(`EPOCH RANK ${e.rank}`);
      if (num(v.epoch_wins)) wins.push(`${v.epoch_wins} EPOCH WIN${v.epoch_wins === 1 ? "" : "S"}`);
      if (num(v.one_btc_wins)) wins.push(`WON THE 1 BTC VAULT`);
      replaceChildren(tickets, [line, el("small", { text: wins.join(" · ") || "NO VAULT WINS" })]);
    }
  }
  // Hashrate: what the fly's settled deploys mined, as the API reports it per round. Paper deploys mine nothing.
  const hashrate = $("h-hashrate");
  if (hashrate) {
    const hr = num(p.hashrate);
    if (s.mode !== "live") replaceChildren(hashrate, ["—", el("small", { text: "NO WALLET" })]);
    else replaceChildren(hashrate, [`${fmtInt(hr ?? 0)} HR`, el("small", { text: `${fmtInt(p.rounds_played ?? 0)} ROUNDS` })]);
  }
  // RUSH: the game's token, minted to the deployer each round; the API prices it at settlement.
  const rush = $("h-rush");
  if (rush) {
    if (s.mode !== "live") replaceChildren(rush, ["—", el("small", { text: "NO WALLET" })]);
    else replaceChildren(rush, [`${fmtRush(p.rush_won)} RUSH`, el("small", { text: fmtMoney(p.rush_won_usd) })]);
  }
}

function renderRounds(s) {
  const rounds = Array.isArray(s.rounds) ? s.rounds : [];
  const key = JSON.stringify(rounds.map((r) => [r.round_id, r.status, r.won, r.pnl, r.signature, r.sats, r.token_usd]));
  if (key === app.roundsKey) return;
  app.roundsKey = key;
  const body = $("rounds-body");
  if (!body) return;
  if (!rounds.length) { replaceChildren(body, [el("tr", {}, el("td", { colspan: "8", class: "dim", text: "NO ROUNDS YET" }))]); markOverflow(); return; }
  const rows = rounds.map((r) => {
    const status = String(r.status || "").toUpperCase();
    let result = "OPEN", tone = "dim";
    if (r.won === true) { result = "HIT"; tone = "pos"; }
    else if (r.won === false) { result = "MISS"; tone = "neg"; }
    else if (status === "FAILED") { result = "FAILED"; tone = "neg"; }
    else if (status === "UNKNOWN") { result = "UNKNOWN"; tone = "neg"; }
    else if (status === "PREPARED" || status === "SENT") { result = status; }
    const resultCell = el("td", { class: tone }, [result]);
    if (r.simulated === true) resultCell.append(el("span", { class: "tag", text: "SIM" }));
    else if (status === "PAPER") resultCell.append(el("span", { class: "tag", text: "PAPER" }));
    if (r.strike) resultCell.append(el("span", { class: "tag strike", text: "STRIKE" }));
    if (r.winning_tile != null) resultCell.append(el("small", { text: `winner ${r.winning_tile}` }));
    const sats = num(r.sats);
    const tx = r.signature
      ? el("a", { href: explorerTx(r.signature, s.network), target: "_blank", rel: "noopener noreferrer", "aria-label": `Transaction for round ${r.round_id} on Solscan` }, ["TX ↗"])
      : el("span", { class: "dim", text: "—" });
    // Live settlements add the RUSH token value the API reports to the round's P&L; say so where it happens.
    const pnlCell = el("td", { class: signClass(r.pnl, 4) }, [fmtMoney(r.pnl, { sign: true, digits: 4 })]);
    const token = num(r.token_usd);
    if (token) pnlCell.append(el("small", { text: `incl. ${fmtRush(r.token)} RUSH (${fmtMoney(token, { digits: 4 })})` }));
    const strikeUsd = num(r.strike_usd);
    if (strikeUsd) pnlCell.append(el("small", { text: `incl. ${fmtMoney(strikeUsd)} strike bonus` }));
    const hr = num(r.hashrate);
    if (hr) pnlCell.append(el("small", { text: `+${fmtInt(hr)} HR` }));
    return el("tr", {}, [
      el("td", {}, [`#${r.round_id ?? "—"}`, el("small", { text: fmtTime(r.time) })]),
      resultCell,
      pnlCell,
      el("td", { text: fmtMoney(r.stake) }),
      el("td", { text: fmtMoney(r.refund) }),
      el("td", {}, sats === null ? ["—"] : [intFmt.format(sats), el("small", { text: fmtMoney(r.sats_usd, { digits: 4 }) })]),
      el("td", {}, [String(r.tile_count ?? (Array.isArray(r.tiles) ? r.tiles.length : "—")), el("small", { text: tileList(r.tiles) })]),
      el("td", {}, [tx]),
    ]);
  });
  replaceChildren(body, rows);
  markOverflow();
}

function renderDecisions(s) {
  const decisions = Array.isArray(s.decisions) ? s.decisions : [];
  const key = JSON.stringify(decisions.map((d) => [d.tick, d.status, d.reason, d.kc_spikes]));
  if (key === app.decisionsKey) return;
  app.decisionsKey = key;
  const body = $("decisions-body");
  if (!body) return;
  if (!decisions.length) { replaceChildren(body, [el("tr", {}, el("td", { colspan: "6", class: "dim", text: "NO OBSERVATIONS YET" }))]); markOverflow(); return; }
  const rows = decisions.map((d) => {
    const status = String(d.status || "").toUpperCase();
    const tone = status === "VETO" || status === "FAILED" ? "neg" : status === "CONFIRMED" ? "pos" : "";
    const statusCell = el("td", { class: tone }, [status || "—"]);
    if (d.reason) statusCell.append(el("small", { text: String(d.reason) }));
    const stim = String(d.stimulus || "none").toUpperCase();
    return el("tr", {}, [
      el("td", {}, [`#${d.tick ?? "—"}`, el("small", { text: fmtTime(d.time) })]),
      el("td", { text: d.round_id != null ? `#${d.round_id}` : "—" }),
      statusCell,
      el("td", { class: stim === "REWARD" ? "pos" : stim === "AVERSIVE" ? "neg" : "dim", text: stim }),
      el("td", { text: fmtInt(d.kc_spikes) }),
      el("td", {}, [String(d.tile_count ?? (Array.isArray(d.tiles) ? d.tiles.length : "—")), el("small", { text: tileList(d.tiles) })]),
    ]);
  });
  replaceChildren(body, rows);
  markOverflow();
}

function renderPerf(s) {
  const p = s.portfolio || {};
  const settings = s.settings || {};
  const costs = s.costs || {};
  setText("s-cash", fmtMoney(p.cash));
  setText("s-fees", fmtMoney(p.fees));
  setText("s-refunds", fmtMoney(p.refunds));
  setText("s-sats", fmtSats(p.sats_won, p.sats_won_usd));
  const best = $("s-best");
  if (best) { best.textContent = fmtMoney(p.best_round_pnl, { sign: true, digits: 4 }); best.className = `num ${signClass(p.best_round_pnl, 4)}`.trim(); }
  const chip = $("perf-chip");
  if (chip) {
    chip.textContent = `${fmtInt(p.rounds_played)} ROUNDS · ${fmtMoney(p.pnl, { sign: true })}${s.mode === "live" ? "" : " · SIMULATED"}`;
    chip.className = `chip ${signClass(p.pnl)}`.trim();
  }
  const stake = settings.stake != null ? fmtMoney(settings.stake) : "$1";
  const daily = settings.daily_deploys != null ? fmtInt(settings.daily_deploys) : "300";
  const stop = settings.loss_stop != null ? fmtMoney(settings.loss_stop) : "$20";
  const today = num(costs.deploys_today);
  setText("limits", `${stake} / ROUND · ${daily} DEPLOYS / DAY · 1 DEPLOY PER ROUND · LOSS STOP ${stop}${today === null ? "" : ` · ${fmtInt(today)} DEPLOYS TODAY`}`);
  renderChart(s);
}

function renderChart(s) {
  const svg = $("equity-chart");
  if (!svg) return;
  const hist = (Array.isArray(s.history) ? s.history : []).map((h) => ({ t: num(h.time), v: num(h.equity) })).filter((h) => h.t !== null && h.v !== null);
  const initial = num(s.portfolio && s.portfolio.initial);
  // Draw at the real width so one unit is one CSS pixel: axis text and strokes keep their size at every width.
  const avail = svg.clientWidth || (svg.parentElement && svg.parentElement.clientWidth) || 0;
  const W = Math.max(280, Math.round(avail || 640));
  const key = `${W}:${hist.length}:${hist.length ? hist[hist.length - 1].t : 0}:${hist.length ? hist[hist.length - 1].v : 0}:${initial}`;
  if (key === app.chartKey) return;
  app.chartKey = key;
  svg.setAttribute("viewBox", `0 0 ${W} 220`);
  const NS = "http://www.w3.org/2000/svg";
  const mk = (tag, attrs, text) => {
    const n = document.createElementNS(NS, tag);
    for (const [k, v] of Object.entries(attrs)) n.setAttribute(k, String(v));
    if (text !== undefined) n.textContent = text;
    return n;
  };
  const H = 220, R = 12, T = 14, B = 26;
  const nodes = [];
  if (hist.length < 2) {
    nodes.push(mk("text", { x: W / 2, y: H / 2, "text-anchor": "middle", class: "empty" }, hist.length ? "ONE OBSERVATION · NO CURVE YET" : "NO HISTORY YET"));
    svg.replaceChildren(...nodes);
    return;
  }
  const t0 = hist[0].t, t1 = hist[hist.length - 1].t;
  const values = hist.map((h) => h.v).concat(initial === null ? [] : [initial]);
  let lo = Math.min(...values), hi = Math.max(...values);
  const padY = Math.max((hi - lo) * 0.15, 0.01);
  lo -= padY; hi += padY;
  // The left gutter fits the longest label (10px Space Mono is about 6.2 units per glyph), so $12,345.67 is not cut.
  const labels = [hi, lo].concat(initial === null ? [] : [initial]).map((v) => `$${moneyFmt.format(v)}`);
  const L = 14 + Math.ceil(6.2 * Math.max(...labels.map((t) => t.length)));
  const x = (t) => L + ((t - t0) / Math.max(t1 - t0, 1)) * (W - L - R);
  const y = (v) => T + (1 - (v - lo) / (hi - lo)) * (H - T - B);
  // Steps, not slopes: equity only changes at observations.
  let d = "";
  hist.forEach((h, i) => {
    const px = x(h.t).toFixed(1), py = y(h.v).toFixed(1);
    if (i === 0) d += `M${px} ${py}`;
    else d += `H${px}V${py}`;
  });
  const last = hist[hist.length - 1].v;
  const color = initial !== null && last < initial ? "#ff4b78" : "#bdff32";
  nodes.push(mk("rect", { x: L, y: T, width: W - L - R, height: H - T - B, fill: "none", stroke: "#32353e", "stroke-width": 2, "shape-rendering": "crispEdges" }));
  if (initial !== null) {
    nodes.push(mk("line", { x1: L, x2: W - R, y1: y(initial).toFixed(1), y2: y(initial).toFixed(1), stroke: "#989aaa", "stroke-width": 2, "stroke-dasharray": "6 6", "shape-rendering": "crispEdges" }));
    nodes.push(mk("text", { x: L - 6, y: (y(initial) + 4).toFixed(1), "text-anchor": "end", class: "axis" }, `$${moneyFmt.format(initial)}`));
  }
  // Skip an axis label that would touch the starting-balance label (each label is a 13 px box).
  const yInit = initial === null ? null : y(initial);
  if (yInit === null || yInit - T > 24) nodes.push(mk("text", { x: L - 6, y: T + 10, "text-anchor": "end", class: "axis" }, `$${moneyFmt.format(hi)}`));
  if (yInit === null || (H - B) - yInit > 24) nodes.push(mk("text", { x: L - 6, y: H - B, "text-anchor": "end", class: "axis" }, `$${moneyFmt.format(lo)}`));
  nodes.push(mk("text", { x: L, y: H - 8, class: "axis" }, fmtTime(t0)));
  nodes.push(mk("text", { x: W - R, y: H - 8, "text-anchor": "end", class: "axis" }, fmtTime(t1)));
  nodes.push(mk("path", { d, fill: "none", stroke: color, "stroke-width": 3, "stroke-linejoin": "miter", "shape-rendering": "crispEdges" }));
  nodes.push(mk("rect", { x: (x(t1) - 4).toFixed(1), y: (y(last) - 4).toFixed(1), width: 8, height: 8, fill: color }));
  svg.setAttribute("aria-label", `Equity over ${hist.length} observations, from $${moneyFmt.format(hist[0].v)} to $${moneyFmt.format(last)}`);
  svg.replaceChildren(...nodes);
}

function renderSensory(s) {
  const img = $("sensory");
  const pub = s.publication || {};
  const sha = pub.frame_sha256 || null;
  if (img && sha && sha !== app.frameSha) {
    app.frameSha = sha;
    img.src = DEMO && DEMO.sensory ? DEMO.sensory : apiUrl(`/api/sensory.png?v=${encodeURIComponent(sha.slice(0, 16))}`);
    img.hidden = false;
    const empty = $("sensory-empty");
    if (empty) empty.hidden = true;
  }
  setText("sensory-meta", `OBSERVATION #${fmtInt(s.tick)} · ${fmtTime(s.observed_at)} UTC${sha ? ` · SHA ${sha.slice(0, 8)}` : ""}`);
}

function renderBoardChip(cb) {
  const b = cb.board;
  if (cb.source === "live") setChip("board-chip", "LIVE BOARD", "ok");
  else if (cb.source === "snapshot") setChip("board-chip", `SNAPSHOT · ${fmtAge(b.fetched_at)} AGO`, "");
  else setChip("board-chip", `STALE · ${fmtAge(b.fetched_at)} AGO`, "bad");
}

/** At most five glyphs, so the label fits a 31 px tile on a 360 px phone. */
function stakeLabel(stake) {
  if (stake === null) return "—";
  if (stake >= 10000) return `$${Math.round(stake / 1000)}K`;
  if (stake >= 1000) return `$${(stake / 1000).toFixed(1)}K`;
  return `$${stake.toFixed(stake >= 100 ? 0 : 1)}`;
}

function renderBoard() {
  const s = app.state;
  const cb = currentBoard();
  if (!cb) {
    setChip("board-chip", app.everFetched ? "BOARD OFFLINE" : "CONNECTING", app.everFetched ? "bad" : "");
    const grid = $("tiles");
    if (grid && !grid.childElementCount) {
      const items = [];
      for (let i = 0; i < 21; i++) items.push(el("li", { class: "empty", "aria-label": `Tile ${i + 1}: no data` }, [el("span", { text: String(i + 1) }), el("b", { text: "—" })]));
      replaceChildren(grid, items);
    }
    return;
  }
  const b = cb.board;
  renderBoardChip(cb);
  setText("round-id", b.round_id != null ? `#${b.round_id}` : "#—");
  setText("pot", fmtMoney(b.pot_usd));
  setText("miners", fmtInt(b.miners));
  renderVaults(b.vaults);

  const stakes = Array.isArray(b.tile_stakes) ? b.tile_stakes : [];
  const top = Math.max(1e-9, ...stakes.map((t) => num(t && t.stake_usd) ?? 0));
  const neural = (s && s.ready && s.neural) || {};
  const picks = new Set(Array.isArray(neural.tiles) ? neural.tiles : []);
  const d0 = s && s.ready && Array.isArray(s.decisions) && s.decisions.length ? s.decisions[0] : null;
  const pickRound = d0 && d0.round_id != null ? d0.round_id : null;
  const winners = Array.isArray(b.previous_winners) ? b.previous_winners : [];
  const lastWin = winners.length && winners[0] ? num(winners[0].tile) : (b.previous_round ? num(b.previous_round.winning_tile) : null);

  const grid = $("tiles");
  if (grid) {
    const items = [];
    for (let i = 0; i < 21; i++) {
      const tile = i + 1;
      const st = stakes[i] || {};
      const stake = num(st.stake_usd);
      const share = stake === null ? 0 : stake / top;
      const classes = [];
      if (picks.has(tile)) classes.push("pick");
      if (lastWin === tile) classes.push("win");
      if (stake === null) classes.push("empty");
      const li = el("li", {
        class: classes.join(" ") || null,
        style: `--a:${(0.06 + 0.64 * share).toFixed(3)}`,
        "aria-label": `Tile ${tile}: ${stake === null ? "unknown stake" : fmtMoney(stake)}${st.miners != null ? `, ${st.miners} miners` : ""}${picks.has(tile) ? ", fly pick" : ""}${lastWin === tile ? ", last winner" : ""}`,
        title: `Tile ${tile} · ${stake === null ? "—" : fmtMoney(stake)}${st.miners != null ? ` · ${st.miners} miners` : ""}`,
      }, [el("span", { text: String(tile) }), el("b", { text: stakeLabel(stake) })]);
      items.push(li);
    }
    replaceChildren(grid, items);
  }
  const pr = $("pick-round");
  if (pr) {
    if (pickRound === null) pr.textContent = "";
    else pr.textContent = b.round_id != null && pickRound !== b.round_id ? `(FROM R#${pickRound})` : `(R#${pickRound})`;
  }
  const lw = $("last-winners");
  if (lw) {
    replaceChildren(lw, winners.slice(0, 5).map((w) => el("li", { "aria-label": `Round ${w.round_id}: tile ${w.tile}` }, [String(w.tile ?? "—"), el("small", { text: w.round_id != null ? `#${w.round_id}` : "" })])));
    if (!winners.length) lw.append(el("li", { class: "dim", text: "—" }));
  }
}

/** The three prize vaults above the real board: strike, epoch (with its countdown) and 1 BTC. */
function renderVaults(v) {
  v = v || {};
  setText("v-strike", fmtMoney(v.strike_usd));
  const epoch = $("v-epoch");
  if (epoch) {
    const ends = num(v.epoch_ends_at);
    const left = ends === null ? null : ends - nowMs() / 1000;
    replaceChildren(epoch, [fmtMoney(v.epoch_usd), el("small", { text: left === null ? "" : (left > 0 ? `ENDS IN ${fmtAge(nowMs() / 1000 - left)}` : "ENDING") })]);
  }
  const one = $("v-onebtc");
  if (one) {
    const btc = num(v.one_btc_btc);
    replaceChildren(one, [btc === null ? "—" : `${btc.toFixed(3)} BTC`, el("small", { text: btc === null ? "" : "A TICKET LOTTERY" })]);
  }
}

/** One-second ticker: countdown, ages, freshness. */
function tick() {
  const s = app.state;
  const cb = currentBoard();
  const out = $("countdown");
  const bar = $("countdown-bar");
  const fill = $("countdown-fill");
  if (!out || !bar || !fill) return;
  let text = "—", frac = 0, cls = "";
  if (cb) {
    const b = cb.board;
    if (b.pending_activation) { text = "ROTATING"; frac = 1; cls = "rotating"; }
    else {
      const now = nowMs() / 1000 - (cb.source === "snapshot" ? app.stateSkew : (app.boardSkew ?? app.stateSkew));
      const endsAt = num(b.ends_at), startedAt = num(b.started_at), slotMs = num(b.slot_ms) || 316, slotsLeft = num(b.slots_remaining), fetchedAt = num(b.fetched_at);
      let remaining = null;
      if (endsAt !== null) remaining = endsAt - now;
      else if (slotsLeft !== null) remaining = (slotsLeft * slotMs) / 1000 - (fetchedAt === null ? 0 : now - fetchedAt);
      const total = endsAt !== null && startedAt !== null && endsAt > startedAt ? endsAt - startedAt : (ROUND_SLOTS * slotMs) / 1000;
      if (remaining === null) text = "—";
      else if (remaining <= 0) { text = cb.source === "live" ? "DRAWING" : "ENDED"; frac = 0; }
      else {
        text = fmtClock(remaining);
        frac = Math.min(1, Math.max(0, remaining / total));
        const minSlots = num(s && s.settings && s.settings.min_slots_remaining) ?? 40;
        if (remaining < (minSlots * slotMs) / 1000) cls = "late";
      }
    }
  }
  out.textContent = text;
  out.className = `num ${cls}`.trim();
  bar.className = `countdown ${cls}`.trim();
  bar.setAttribute("aria-valuenow", String(Math.round(frac * 100)));
  bar.setAttribute("aria-valuetext", text);
  fill.style.width = `${(frac * 100).toFixed(1)}%`;
  if (s && s.ready) { safe(() => renderFreshChip(s)); safe(renderFooter); }
  // The live board goes stale between polls when the tab is hidden; keep the chip honest.
  if (cb) safe(() => renderBoardChip(cb));
}

function renderFooter() {
  const s = app.state;
  let chip = "CONNECTING", tone = "";
  let fresh = "—", exec = "—";
  if (app.everFetched && !s) { chip = "DISCONNECTED"; tone = "bad"; fresh = "NO RESPONSE FROM /API/STATE"; }
  else if (s && !s.ready) {
    chip = "WAITING FOR WORKER"; tone = "";
    fresh = s.error ? `SNAPSHOT UNAVAILABLE · ${String(s.error).toUpperCase()}` : `NO LEDGER YET · PUBLISHED ${fmtTime(s.published_at)} UTC`;
  } else if (s) {
    const st = s.status || {};
    const health = workerHealth(s);
    if (health === "disconnected") { chip = "DISCONNECTED"; tone = "bad"; }
    else if (health === "halted") { chip = "WORKER HALTED"; tone = "bad"; }
    else if (health === "stale") { chip = "STALE"; tone = ""; }
    else if (s.mode === "live") { chip = "LIVE WALLET"; tone = "ok"; }
    else if (app.board && nowMs() - app.boardAt < LIVE_BOARD_MAX_AGE_MS && st.feed !== "fixture") { chip = "PAPER · LIVE BOARD"; tone = "info"; }
    else { chip = "PAPER SIMULATION"; tone = "info"; }
    const phase = String(st.phase || "stopped").toUpperCase();
    fresh = health === "disconnected"
      ? `NO RESPONSE FROM /API/STATE${app.lastError ? ` · ${app.lastError.toUpperCase()}` : ""} · LAST OBSERVATION ${fmtAge(s.observed_at)} AGO`
      : `${phase} · OBSERVATION ${fmtAge(s.observed_at)} AGO`;
    if (st.halted) fresh += ` · ${String(st.halted).toUpperCase()}`;
    if (s.mode === "live") exec = "LIVE WALLET · REAL DEPLOYS";
    else if (st.feed === "fixture") exec = "SYNTHETIC ROUNDS";
    else exec = "PAPER SIMULATION · NO WALLET";
    if (st.stub_brain) exec += " · STUB BRAIN";
    if (s.network === "devnet") exec += " · DEVNET";
  }
  if (DEMO) {
    chip = "STATIC DEMO"; tone = "info";
    fresh = `SNAPSHOT CAPTURED ${fmtTime(DEMO.captured_at)} UTC · NOTHING ON THIS PAGE UPDATES`;
  }
  setChip("conn-chip", chip, tone);
  setText("foot-fresh", fresh);
  setText("foot-exec", exec);
  const w = $("wallet-link");
  if (w) {
    const a = s && s.ready && s.wallet && s.wallet.address ? String(s.wallet.address) : "";
    w.hidden = !a;
    const href = a ? explorerAccount(a, s.network) : "";
    if (a && w.getAttribute("href") !== href) {
      w.href = href;
      w.textContent = `WALLET ${a.slice(0, 4)}...${a.slice(-4)}`;
      w.setAttribute("aria-label", `Wallet ${a} on Solscan`);
    }
  }
}

/* ---------------- scene ---------------- */

function sceneFallback(title) {
  app.sceneFailed = true;
  const canvas = $("output");
  const fb = $("stage-fallback");
  if (canvas) canvas.hidden = true;
  if (fb) fb.hidden = false;
  setText("stage-fallback-title", title);
  setText("stage-caption", "STATIC VIEW");
}

async function loadScene() {
  if (app.scene || app.sceneLoading || app.sceneFailed) return;
  const canvas = $("output");
  if (!canvas) return;
  app.sceneLoading = true;
  // three r170 needs WebGL2, so a WebGL1-only browser gets the fallback without downloading the module;
  // a software renderer (major performance caveat) gets the scene with the low-power hint.
  let gl = null, strong = false;
  try {
    gl = document.createElement("canvas").getContext("webgl2", { failIfMajorPerformanceCaveat: true });
    strong = !!gl;
    if (!gl) gl = document.createElement("canvas").getContext("webgl2");
    const lose = gl && gl.getExtension("WEBGL_lose_context");
    if (lose) lose.loseContext();
  } catch (_) { gl = null; }
  if (!gl) { app.sceneLoading = false; sceneFallback("WEBGL OFF"); return; }
  try {
    const mod = await import("/scene.js");
    const scene = mod && typeof mod.startScene === "function" ? mod.startScene(canvas, { lowPower: !strong }) : null;
    if (!scene || typeof scene.update !== "function") throw new Error("scene did not start");
    app.scene = scene;
    if (app.paused) safe(() => scene.setPaused(true));
    safe(() => scene.setVisible(app.view === "watch" && !document.hidden));
    sceneUpdate();
  } catch (e) {
    console.warn("scene unavailable:", e && e.message ? e.message : e);
    sceneFallback("3D VIEW OFF");
  } finally {
    app.sceneLoading = false;
  }
}

function setPaused(paused) {
  app.paused = paused;
  document.body.classList.toggle("motion-paused", paused);
  const btn = $("pause-btn");
  if (btn) { btn.classList.toggle("on", paused); btn.textContent = paused ? "RESUME MOTION" : "PAUSE MOTION"; }
  if (app.scene) safe(() => app.scene.setPaused(paused));
}

/* ---------------- routing and tabs ---------------- */

function route() {
  const hash = (location.hash || "#watch").replace(/^#/, "");
  const view = hash === "how-it-works" ? "how" : "watch";
  const changed = view !== app.view;
  app.view = view;
  const watch = $("watch"), how = $("how-it-works");
  if (watch) watch.hidden = view !== "watch";
  if (how) how.hidden = view !== "how";
  document.querySelectorAll("[data-view-link]").forEach((a) => {
    if (a.dataset.viewLink === view) a.setAttribute("aria-current", "page");
    else a.removeAttribute("aria-current");
  });
  document.title = view === "how" ? "HOW IT WORKS · SAT RUSH FLY" : "SAT RUSH FLY · a fly connectome plays SatRush";
  if (changed && hash !== "main") window.scrollTo(0, 0);
  if (app.scene) safe(() => app.scene.setVisible(view === "watch" && !document.hidden));
  if (view === "watch") loadScene();
}

function selectTab(name, focus) {
  app.tab = name;
  document.querySelectorAll("[role=tab]").forEach((t) => {
    const on = t.dataset.tab === name;
    t.setAttribute("aria-selected", String(on));
    t.tabIndex = on ? 0 : -1;
    const panel = $(t.getAttribute("aria-controls"));
    if (panel) panel.hidden = !on;
    if (on && focus) t.focus();
  });
  markOverflow();
}

function initTabs() {
  const tabs = Array.from(document.querySelectorAll("[role=tab]"));
  tabs.forEach((t, i) => {
    t.addEventListener("click", () => selectTab(t.dataset.tab, false));
    t.addEventListener("keydown", (e) => {
      let j = null;
      if (e.key === "ArrowRight" || e.key === "ArrowDown") j = (i + 1) % tabs.length;
      else if (e.key === "ArrowLeft" || e.key === "ArrowUp") j = (i - 1 + tabs.length) % tabs.length;
      else if (e.key === "Home") j = 0;
      else if (e.key === "End") j = tabs.length - 1;
      if (j !== null) { e.preventDefault(); selectTab(tabs[j].dataset.tab, true); }
    });
  });
}

/* ---------------- boot ---------------- */

function init() {
  initTabs();
  const btn = $("pause-btn");
  if (btn) btn.addEventListener("click", () => setPaused(!app.paused));
  if (reducedMotion.matches) setPaused(true);
  const img = $("sensory");
  if (img) {
    img.addEventListener("error", () => { img.hidden = true; const e = $("sensory-empty"); if (e) e.hidden = false; });
  }
  window.addEventListener("hashchange", route);
  const rechart = () => { app.chartKey = ""; if (app.state && app.state.ready) safe(() => renderChart(app.state)); };
  const perf = $("perf");
  if (perf) perf.addEventListener("toggle", rechart);
  window.addEventListener("resize", () => schedule("rechart", () => { rechart(); markOverflow(); }, 200));
  document.querySelectorAll(".scroll").forEach((box) => box.addEventListener("scroll", () => schedule("overflow", markOverflow, 100), { passive: true }));
  document.addEventListener("visibilitychange", () => {
    if (app.scene) safe(() => app.scene.setVisible(app.view === "watch" && !document.hidden));
    if (!document.hidden) { schedule("state", pollState, 0); schedule("board", pollBoard, 0); }
  });
  route();
  loadConfig().finally(() => {
    pollState();
    pollBoard();
  });
  setInterval(() => safe(tick), 1000);
  if (app.view === "watch") {
    if ("requestIdleCallback" in window) requestIdleCallback(() => loadScene(), { timeout: 1500 });
    else setTimeout(loadScene, 300);
  }
}

if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", init);
else init();
