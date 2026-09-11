// monitor.js — paints the SatRush-style board that the 3D fly is looking at.
//
// drawMonitor(ctx, state, board, now) draws onto a 960x540 2D canvas that
// scene.js uploads as a CanvasTexture. It only reads the snapshot documented in
// STATE.md; nothing here is a source of truth. The layout follows the real
// satrush.io board: three prize vaults across the top, a honeycomb of 21 hex
// tiles (number, stake, miners) in the middle, and a control rail on the right.
// Figures are drawn at 18px or larger: the texture covers about a third of the
// render width once projected onto the CRT, so anything smaller is a smear.

export const MONITOR_WIDTH = 960;
export const MONITOR_HEIGHT = 540;

// The satrush.io palette (their CSS custom properties, rounded to hex).
const COLORS = {
  bg: '#080707',
  raised: '#201818',
  panel: '#120e0e',
  tile: '#181313',
  tileRim: '#3a2f2f',
  line: '#2b2121',
  muted: '#9c8f8f',
  ink: '#eee8e8',
  accent: '#f25e30',   // orange: the fly's picks, START
  success: '#30f297',  // green: last winner, profit
  danger: '#ff3d67',   // pink-red: loss, halted
  warn: '#f2be30',     // amber: countdown, coins
  tileLow: [24, 19, 19],
  tileHigh: [96, 44, 26], // heaviest stake: a warm ember, still dark enough for white text
};

// Loads nothing itself: the page declares the faces, the canvas falls back to sans.
const SANS = '"Work Sans", "Helvetica Neue", Arial, sans-serif';
const font = (px, weight = 600) => `${weight} ${px}px ${SANS}`;

const TILES = 21;

// Layout (all in canvas pixels).
const PAD = 22;
const HEADER_H = 46;
const VAULT_Y = 56;
const VAULT_H = 64;
const BOARD_TOP = 130;
const RAIL_W = 236;
const RAIL_X = MONITOR_WIDTH - PAD - RAIL_W; // 702
const BOARD_W = RAIL_X - PAD - 18;           // 662
const FOOTER_Y = 494;

// Honeycomb: pointy-top hexagons in staggered rows of 5-6-5-5 (21), like the real board.
const HEX_R = 50;                         // circumradius
const HEX_W = Math.sqrt(3) * HEX_R;       // 86.6
const HEX_ROW = HEX_R * 1.5 + 2;          // 77: row pitch, with a little air
const HEX_ROWS = [5, 6, 5, 5];
const HEX_TOP = BOARD_TOP + HEX_R + 8;

function hexCenters() {
  const centers = [];
  const widest = Math.max(...HEX_ROWS);
  const left = PAD + (BOARD_W - widest * (HEX_W + 3)) / 2; // centre the widest row in the board area
  let tile = 0;
  HEX_ROWS.forEach((count, row) => {
    const offset = (widest - count) * (HEX_W + 3) / 2;
    for (let i = 0; i < count; i += 1) {
      tile += 1;
      centers.push({ tile, x: left + offset + (i + 0.5) * (HEX_W + 3), y: HEX_TOP + row * HEX_ROW });
    }
  });
  return centers;
}
const HEX_CENTERS = hexCenters();

// ---------------------------------------------------------------------------
// Formatting helpers. Money arrives as decimal strings; keep them as strings
// until display and never do arithmetic that could imply precision we lack.

function num(value) {
  if (value === null || value === undefined) return null;
  const n = typeof value === 'number' ? value : parseFloat(value);
  return Number.isFinite(n) ? n : null;
}

function usd(value, decimals = 2) {
  const n = num(value);
  if (n === null) return '—';
  const sign = n < 0 ? '-' : '';
  const abs = Math.abs(n);
  const body = abs >= 1000
    ? abs.toLocaleString('en-US', { minimumFractionDigits: decimals, maximumFractionDigits: decimals })
    : abs.toFixed(decimals);
  return `${sign}$${body}`;
}

// Whole dollars from $1,000 so the rail line still fits.
function signedUsd(value) {
  const n = num(value);
  if (n === null) return '—';
  return (n > 0 ? '+' : '') + usd(n, Math.abs(n) >= 1000 ? 0 : 2);
}

// Short stake label for a hex: "0", "2.2", "30", "1.2K" (the coin glyph carries the currency).
function tileStakeLabel(value) {
  const n = num(value);
  if (n === null) return '';
  if (n >= 10000) return `${Math.round(n / 1000)}K`;
  if (n >= 1000) return `${(n / 1000).toFixed(1)}K`;
  if (n >= 10) return String(Math.round(n));
  return n.toFixed(1);
}

// Whole dollars once the amount is large, so it still fits its slot.
function compactUsd(value) {
  const n = num(value);
  return usd(n, n !== null && Math.abs(n) >= 10000 ? 0 : 2);
}

function integer(value) {
  const n = num(value);
  return n === null ? '—' : Math.round(n).toLocaleString('en-US');
}

function clamp(x, lo, hi) {
  return Math.min(hi, Math.max(lo, x));
}

function lerpColor(a, b, t) {
  const k = clamp(t, 0, 1);
  const r = Math.round(a[0] + (b[0] - a[0]) * k);
  const g = Math.round(a[1] + (b[1] - a[1]) * k);
  const bl = Math.round(a[2] + (b[2] - a[2]) * k);
  return `rgb(${r},${g},${bl})`;
}

// Word-wrap by measured width.
function wrapWords(ctx, words, maxWidth, maxLines) {
  const lines = [];
  let current = '';
  for (const word of words) {
    const next = current ? `${current} ${word}` : word;
    if (ctx.measureText(next).width <= maxWidth) {
      current = next;
    } else {
      if (current) lines.push(current);
      current = word;
    }
  }
  if (current) lines.push(current);
  if (lines.length > maxLines) {
    const shown = lines.slice(0, maxLines);
    const hidden = words.length - shown.join(' ').split(' ').length;
    shown[maxLines - 1] = `${shown[maxLines - 1]} +${hidden}`;
    if (ctx.measureText(shown[maxLines - 1]).width > maxWidth) shown[maxLines - 1] = `+${hidden} more`;
    return shown;
  }
  return lines;
}

function durationLabel(seconds) {
  const s = Math.max(0, Math.round(seconds));
  if (s >= 86400) return `${Math.floor(s / 86400)}d ${Math.floor((s % 86400) / 3600)}h`;
  if (s >= 3600) return `${Math.floor(s / 3600)}h ${Math.floor((s % 3600) / 60)}m`;
  return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, '0')}`;
}

// ---------------------------------------------------------------------------
// Board selection: prefer the live proxy when it is usable, else the board the
// retina saw at the last observation.

function usableBoard(board) {
  return board && board.live !== false && Array.isArray(board.tile_stakes) ? board : null;
}

function pickBoard(state, board) {
  const live = usableBoard(board);
  if (live) return { board: live, live: true };
  const seen = state && state.board && Array.isArray(state.board.tile_stakes) ? state.board : null;
  return { board: seen, live: false };
}

function countdown(board, now) {
  if (!board) return null;
  const end = num(board.ends_at);
  const start = num(board.started_at);
  if (end === null) return null;
  const remaining = end - now;
  let fraction = null;
  if (start !== null && end > start) fraction = clamp(remaining / (end - start), 0, 1);
  const over = board.state === 'ended' || remaining <= 0;
  return { remaining: Math.max(0, remaining), fraction: over ? 0 : fraction, over, pending: !!board.pending_activation };
}

// ---------------------------------------------------------------------------
// Drawing primitives.

function rect(ctx, x, y, w, h, fill) {
  ctx.fillStyle = fill;
  ctx.fillRect(x, y, w, h);
}

function roundedPath(ctx, x, y, w, h, r) {
  ctx.beginPath();
  ctx.moveTo(x + r, y);
  ctx.lineTo(x + w - r, y);
  ctx.quadraticCurveTo(x + w, y, x + w, y + r);
  ctx.lineTo(x + w, y + h - r);
  ctx.quadraticCurveTo(x + w, y + h, x + w - r, y + h);
  ctx.lineTo(x + r, y + h);
  ctx.quadraticCurveTo(x, y + h, x, y + h - r);
  ctx.lineTo(x, y + r);
  ctx.quadraticCurveTo(x, y, x + r, y);
  ctx.closePath();
}

function card(ctx, x, y, w, h, fill, stroke, r = 8) {
  roundedPath(ctx, x, y, w, h, r);
  ctx.fillStyle = fill;
  ctx.fill();
  if (stroke) {
    ctx.lineWidth = 2;
    ctx.strokeStyle = stroke;
    ctx.stroke();
  }
}

function hexPath(ctx, cx, cy, r) {
  ctx.beginPath();
  for (let i = 0; i < 6; i += 1) {
    const a = Math.PI / 6 + (i * Math.PI) / 3; // pointy top
    const px = cx + r * Math.cos(a);
    const py = cy + r * Math.sin(a);
    if (i === 0) ctx.moveTo(px, py); else ctx.lineTo(px, py);
  }
  ctx.closePath();
}

function text(ctx, str, x, y, px, color, align = 'left', baseline = 'alphabetic', weight = 600) {
  ctx.font = font(px, weight);
  ctx.fillStyle = color;
  ctx.textAlign = align;
  ctx.textBaseline = baseline;
  ctx.fillText(str, x, y);
}

// A small USDC-style coin: blue disc with a light ring.
function coin(ctx, cx, cy, r) {
  ctx.beginPath();
  ctx.arc(cx, cy, r, 0, Math.PI * 2);
  ctx.fillStyle = '#2775ca';
  ctx.fill();
  ctx.beginPath();
  ctx.arc(cx, cy, r * 0.55, 0, Math.PI * 2);
  ctx.lineWidth = Math.max(1.5, r * 0.28);
  ctx.strokeStyle = '#ffffff';
  ctx.stroke();
}

// A tiny "people" glyph: head + shoulders.
function person(ctx, cx, cy, r, color) {
  ctx.fillStyle = color;
  ctx.beginPath();
  ctx.arc(cx, cy - r * 0.55, r * 0.45, 0, Math.PI * 2);
  ctx.fill();
  ctx.beginPath();
  ctx.arc(cx, cy + r * 0.75, r * 0.85, Math.PI, 0);
  ctx.closePath();
  ctx.fill();
}

// A faint honeycomb over the whole ground, like the site's background texture.
function drawBackdrop(ctx) {
  rect(ctx, 0, 0, MONITOR_WIDTH, MONITOR_HEIGHT, COLORS.bg);
  ctx.lineWidth = 1;
  ctx.strokeStyle = 'rgba(255,255,255,0.028)';
  const r = 26;
  const w = Math.sqrt(3) * r;
  for (let row = -1; row * r * 1.5 < MONITOR_HEIGHT + r; row += 1) {
    const shift = row % 2 ? w / 2 : 0;
    for (let x = -w; x < MONITOR_WIDTH + w; x += w) {
      hexPath(ctx, x + shift, row * r * 1.5, r - 1);
      ctx.stroke();
    }
  }
}

// ---------------------------------------------------------------------------
// Sections.

function drawHeader(ctx, state, board, now) {
  // Wordmark: an orange hex badge and the name, like the site's top bar.
  hexPath(ctx, PAD + 12, 23, 12);
  ctx.fillStyle = COLORS.accent;
  ctx.fill();
  text(ctx, 'sat', PAD + 32, 30, 22, COLORS.ink, 'left', 'alphabetic', 700);
  const satWidth = ctx.measureText('sat').width;
  text(ctx, 'rush', PAD + 32 + satWidth + 2, 30, 22, COLORS.accent, 'left', 'alphabetic', 700);

  const roundId = board && board.round_id != null ? `Round #${board.round_id}` : 'Round #—';
  text(ctx, roundId, PAD + 150, 30, 18, COLORS.muted, 'left', 'alphabetic', 500);

  // Right side, laid out from the countdown badge leftwards: pot, then miners.
  const badgeLeft = MONITOR_WIDTH - PAD - 62;
  if (board) {
    let cursor = badgeLeft - 16;
    text(ctx, 'pot', cursor, 30, 14, COLORS.muted, 'right', 'alphabetic', 500);
    cursor -= ctx.measureText('pot').width + 6;
    const pot = compactUsd(board.pot_usd);
    text(ctx, pot, cursor, 30, 18, COLORS.ink, 'right');
    cursor -= ctx.measureText(pot).width + 28;
    const miners = integer(board.miners);
    text(ctx, miners, cursor, 30, 18, COLORS.ink, 'right');
    cursor -= ctx.measureText(miners).width + 12;
    person(ctx, cursor, 22, 8, COLORS.muted);
  } else {
    text(ctx, 'no board', badgeLeft - 16, 30, 18, COLORS.muted, 'right', 'alphabetic', 500);
  }

  const cd = countdown(board, now);
  let label = '—';
  let color = COLORS.warn;
  if (cd) {
    // Same words and m:ss as the page's LIVE BOARD panel.
    if (cd.pending) { label = 'rotating'; color = COLORS.muted; }
    else if (cd.over) { label = board.live === true ? 'drawing' : 'ended'; color = COLORS.muted; }
    else {
      label = durationLabel(Math.ceil(cd.remaining));
      if (cd.remaining < 10) color = COLORS.danger;
    }
  }
  const bx = MONITOR_WIDTH - PAD - 40;
  hexPath(ctx, bx, 23, 22);
  ctx.fillStyle = COLORS.raised;
  ctx.fill();
  ctx.lineWidth = 2;
  ctx.strokeStyle = color;
  ctx.stroke();
  if (cd && cd.fraction !== null && cd.fraction > 0) {
    // Ring that empties as the round runs out.
    ctx.beginPath();
    ctx.arc(bx, 23, 17, -Math.PI / 2, -Math.PI / 2 + Math.PI * 2 * cd.fraction);
    ctx.lineWidth = 3;
    ctx.strokeStyle = color;
    ctx.stroke();
  }
  text(ctx, label, bx, 24, label.length > 5 ? 9 : 12, color, 'center', 'middle', 700);

  rect(ctx, 0, HEADER_H, MONITOR_WIDTH, 1, COLORS.line);
}

function drawVaults(ctx, board, now) {
  const vaults = (board && board.vaults) || {};
  const w = (MONITOR_WIDTH - PAD * 2 - 12 * 2) / 3;
  const epochLeft = num(vaults.epoch_ends_at);
  const cards = [
    { title: 'STRIKE', value: compactUsd(vaults.strike_usd), note: 'strike vault', color: COLORS.accent },
    {
      title: 'EPOCH', value: compactUsd(vaults.epoch_usd),
      note: epochLeft !== null ? `ends in ${durationLabel(epochLeft - now)}` : 'epoch vault', color: COLORS.warn,
    },
    {
      title: '1 BTC', value: vaults.one_btc_btc == null ? '—' : `${num(vaults.one_btc_btc).toFixed(3)} BTC`,
      note: 'one btc vault', color: COLORS.success,
    },
  ];
  cards.forEach((c, i) => {
    const x = PAD + i * (w + 12);
    card(ctx, x, VAULT_Y, w, VAULT_H, COLORS.panel, COLORS.line);
    rect(ctx, x + 2, VAULT_Y + 8, 3, VAULT_H - 16, c.color);
    text(ctx, c.title, x + 16, VAULT_Y + 24, 14, COLORS.muted, 'left', 'alphabetic', 700);
    text(ctx, c.value, x + 16, VAULT_Y + 50, 22, COLORS.ink, 'left', 'alphabetic', 700);
    text(ctx, c.note, x + w - 12, VAULT_Y + 50, 13, COLORS.muted, 'right', 'alphabetic', 500);
  });
}

function drawBoard(ctx, state, board) {
  const stakes = board ? board.tile_stakes.map((t) => num(t && t.stake_usd) || 0) : new Array(TILES).fill(0);
  const miners = board ? board.tile_stakes.map((t) => num(t && t.miners) || 0) : new Array(TILES).fill(0);
  const maxStake = Math.max(0, ...stakes);
  const chosen = new Set(state && state.neural && Array.isArray(state.neural.tiles) ? state.neural.tiles : []);

  // Last winning tile: the board's newest previous winner, else the state board's.
  const winners = (board && Array.isArray(board.previous_winners) && board.previous_winners.length ? board.previous_winners : null)
    || (board && board.previous_round && board.previous_round.winning_tile != null ? [{ tile: board.previous_round.winning_tile }] : null)
    || (state && state.board && Array.isArray(state.board.previous_winners) ? state.board.previous_winners : []);
  const recent = new Map();
  winners.slice(0, 5).forEach((w, rank) => {
    if (w && w.tile != null && !recent.has(w.tile)) recent.set(w.tile, rank);
  });

  HEX_CENTERS.forEach(({ tile, x, y }) => {
    const i = tile - 1;
    const share = maxStake > 0 ? stakes[i] / maxStake : 0;
    const rank = recent.get(tile);
    const isWinner = rank === 0;
    const isChosen = chosen.has(tile);

    // Face, then rim. Chosen tiles get the orange rim; the last winner the green one.
    hexPath(ctx, x, y, HEX_R - 2);
    ctx.fillStyle = lerpColor(COLORS.tileLow, COLORS.tileHigh, share);
    ctx.fill();
    if (isChosen) {
      hexPath(ctx, x, y, HEX_R - 2);
      ctx.fillStyle = 'rgba(242,94,48,0.16)';
      ctx.fill();
    }
    if (isWinner) {
      hexPath(ctx, x, y, HEX_R - 2);
      ctx.fillStyle = 'rgba(48,242,151,0.14)';
      ctx.fill();
    }
    // Rim: green for the last winner, orange for a pick; both when the pick just won.
    ctx.lineWidth = isChosen || isWinner ? 3 : 1.5;
    ctx.strokeStyle = isWinner ? COLORS.success : (isChosen ? COLORS.accent : COLORS.tileRim);
    hexPath(ctx, x, y, HEX_R - 3);
    ctx.stroke();
    if (isWinner && isChosen) {
      ctx.lineWidth = 2;
      ctx.strokeStyle = COLORS.accent;
      hexPath(ctx, x, y, HEX_R - 8);
      ctx.stroke();
    }
    if (rank !== undefined && rank > 0) {
      // Older winners: a small green dot at the top.
      ctx.beginPath();
      ctx.arc(x, y - HEX_R + 12, 3, 0, Math.PI * 2);
      ctx.fillStyle = COLORS.success;
      ctx.fill();
    }

    text(ctx, `#${tile}`, x, y - 14, 16, isChosen ? COLORS.accent : COLORS.ink, 'center', 'middle', 700);
    if (board) {
      const stake = tileStakeLabel(stakes[i]);
      ctx.font = font(15, 600);
      const sw = ctx.measureText(stake).width;
      const left = x - (sw + 20) / 2;
      coin(ctx, left + 7, y + 6, 7);
      text(ctx, stake, left + 20, y + 6, 15, COLORS.ink, 'left', 'middle');
      const count = integer(miners[i]);
      ctx.font = font(13, 500);
      const cw = ctx.measureText(count).width;
      const cl = x - (cw + 16) / 2;
      person(ctx, cl + 6, y + 24, 5.5, COLORS.muted);
      text(ctx, count, cl + 16, y + 25, 13, COLORS.muted, 'left', 'middle', 500);
    }
  });
}

function drawRail(ctx, state, boardInfo) {
  const x = RAIL_X;
  const w = RAIL_W;
  const right = x + w;
  const inner = x + 14;
  const innerRight = right - 14;
  const neural = (state && state.neural) || {};
  const tiles = Array.isArray(neural.tiles) ? neural.tiles : [];
  const settings = (state && state.settings) || {};
  const portfolio = (state && state.portfolio) || {};
  const live = state && state.mode === 'live';
  const halted = state && state.status && state.status.halted;

  card(ctx, x, BOARD_TOP, w, FOOTER_Y - BOARD_TOP - 10, COLORS.panel, COLORS.line, 10);

  // Selection: how many tiles the fly picked, then the list.
  let y = BOARD_TOP + 28;
  text(ctx, 'SELECTION', inner, y, 13, COLORS.muted, 'left', 'alphabetic', 700);
  text(ctx, `${tiles.length}/21`, innerRight, y + 1, 18, tiles.length ? COLORS.accent : COLORS.muted, 'right', 'alphabetic', 700);
  ctx.font = font(16, 600);
  let lines = ['—'];
  if (tiles.length >= TILES) lines = ['all 21 tiles'];
  else if (tiles.length) lines = wrapWords(ctx, tiles.map((t) => `#${t}`), w - 28, 2);
  lines.forEach((line, i) => text(ctx, line, inner, y + 28 + i * 22, 16, COLORS.ink));
  const pickRound = state && state.board && state.board.round_id != null ? state.board.round_id : null;
  if (pickRound !== null && boardInfo.live && boardInfo.board && boardInfo.board.round_id !== pickRound) {
    text(ctx, `for round #${pickRound}`, inner, y + 28 + lines.length * 22, 13, COLORS.muted, 'left', 'alphabetic', 500);
  }

  // Amount and balance, laid out like the site's deploy panel.
  y = BOARD_TOP + 122;
  rect(ctx, inner, y, w - 28, 1, COLORS.line);
  text(ctx, 'AMOUNT', inner, y + 24, 13, COLORS.muted, 'left', 'alphabetic', 700);
  card(ctx, inner, y + 34, w - 28, 36, COLORS.raised, null, 6);
  coin(ctx, inner + 18, y + 52, 9);
  text(ctx, usd(settings.stake), inner + 34, y + 53, 18, COLORS.ink, 'left', 'middle', 700);
  text(ctx, 'USDC', innerRight - 10, y + 53, 13, COLORS.muted, 'right', 'middle', 500);
  text(ctx, live ? 'BALANCE' : 'PAPER BALANCE', inner, y + 98, 13, COLORS.muted, 'left', 'alphabetic', 700);
  text(ctx, compactUsd(portfolio.equity), innerRight, y + 99, 18, COLORS.ink, 'right', 'alphabetic', 700);

  // START block: orange like the real button. It is a picture of a button, not one.
  const by = y + 116;
  const fill = halted ? COLORS.line : COLORS.accent;
  card(ctx, inner, by, w - 28, 44, fill, null, 6);
  const label = halted ? 'HALTED' : (live ? 'START · LIVE' : 'START · PAPER');
  text(ctx, label, inner + (w - 28) / 2, by + 23, 17, halted ? COLORS.muted : '#ffffff', 'center', 'middle', 700);

  // Last round result.
  y = by + 68;
  rect(ctx, inner, y, w - 28, 1, COLORS.line);
  const last = state && Array.isArray(state.last_outcomes) && state.last_outcomes[0] ? state.last_outcomes[0] : null;
  text(ctx, last && last.simulated ? 'LAST ROUND · SIM' : 'LAST ROUND', inner, y + 24, 13, COLORS.muted, 'left', 'alphabetic', 700);
  if (last) {
    const hit = last.won === true;
    const verdict = last.won === null || last.won === undefined ? 'open' : (hit ? 'hit' : 'miss');
    text(ctx, `#${last.round_id} ${verdict}`, inner, y + 50, 17, hit ? COLORS.success : COLORS.danger, 'left', 'alphabetic', 700);
    // Refund and sats are in the page's ROUNDS table; only the P&L stays legible here.
    const pnl = num(last.pnl_usd);
    text(ctx, signedUsd(pnl), innerRight, y + 50, 17, pnl !== null && pnl >= 0 ? COLORS.success : COLORS.danger, 'right', 'alphabetic', 700);
  } else {
    text(ctx, 'none settled', inner, y + 50, 16, COLORS.muted, 'left', 'alphabetic', 500);
  }
}

function drawFooter(ctx, state) {
  rect(ctx, 0, FOOTER_Y, MONITOR_WIDTH, 1, COLORS.line);
  const neural = (state && state.neural) || {};
  const tick = state && state.tick != null ? state.tick : '—';
  const brainMs = num(neural.brain_ms);
  const brain = brainMs === null ? '—' : (brainMs >= 1000 ? `${(brainMs / 1000).toFixed(1)}s` : `${Math.round(brainMs)}ms`);
  const spikes = num(neural.total_spikes);
  const left = `obs #${tick} · brain ${brain}${spikes === null ? '' : ` · ${integer(spikes)} spikes`}`;
  text(ctx, left, PAD, FOOTER_Y + 29, 16, COLORS.muted, 'left', 'alphabetic', 500);

  const status = (state && state.status) || {};
  const stub = neural.stub || status.stub_brain;
  const modeLabel = stub ? 'STUB BRAIN' : (state && state.mode === 'live' ? 'LIVE WALLET' : 'PAPER');
  text(ctx, modeLabel, MONITOR_WIDTH - PAD, FOOTER_Y + 29, 16, stub ? COLORS.danger : COLORS.success, 'right', 'alphabetic', 700);
  const modeWidth = ctx.measureText(modeLabel).width;

  let phase = status.phase || 'no worker';
  if (status.halted) phase = `halted · ${status.halted}`;
  ctx.font = font(16, 500);
  const leftWidth = ctx.measureText(left).width;
  const maxWidth = MONITOR_WIDTH - PAD * 2 - modeWidth - leftWidth - 48;
  while (phase.length > 1 && ctx.measureText(phase).width > maxWidth) phase = `${phase.slice(0, -2)}…`;
  if (maxWidth > 40) {
    text(ctx, phase, MONITOR_WIDTH - PAD - modeWidth - 24, FOOTER_Y + 29, 16, status.halted ? COLORS.danger : COLORS.muted, 'right', 'alphabetic', 500);
  }
}

function drawWaiting(ctx, now) {
  const blink = Math.floor(now * 2) % 2 === 0;
  card(ctx, RAIL_X, BOARD_TOP, RAIL_W, FOOTER_Y - BOARD_TOP - 10, COLORS.panel, COLORS.line, 10);
  text(ctx, 'waiting for snapshot', RAIL_X + RAIL_W / 2, BOARD_TOP + 150, 15, COLORS.muted, 'center', 'alphabetic', 500);
  if (blink) rect(ctx, RAIL_X + RAIL_W / 2 - 6, BOARD_TOP + 166, 12, 18, COLORS.accent);
  rect(ctx, 0, FOOTER_Y, MONITOR_WIDTH, 1, COLORS.line);
  text(ctx, 'sat rush fly · read-only snapshot', PAD, FOOTER_Y + 29, 15, COLORS.muted, 'left', 'alphabetic', 500);
}

// ---------------------------------------------------------------------------

/**
 * Paint the terminal.
 * @param {CanvasRenderingContext2D} ctx 2D context of a 960x540 canvas
 * @param {object|null} state the /api/state snapshot (may be null or not ready)
 * @param {object|null} board the /api/board document (may be null or {live:false})
 * @param {number} [now] wall clock in Unix seconds, for the countdown
 */
export function drawMonitor(ctx, state, board, now = Date.now() / 1000) {
  ctx.save();
  ctx.setTransform(1, 0, 0, 1, 0, 0);
  ctx.imageSmoothingEnabled = true;
  drawBackdrop(ctx);

  const ready = !!(state && state.ready);
  const boardInfo = pickBoard(ready ? state : null, board);

  drawHeader(ctx, ready ? state : null, boardInfo.board, now);
  drawVaults(ctx, boardInfo.board, now);
  drawBoard(ctx, ready ? state : null, boardInfo.board);
  if (ready) {
    drawRail(ctx, state, boardInfo);
    drawFooter(ctx, state);
  } else {
    drawWaiting(ctx, now);
  }
  ctx.restore();
}
