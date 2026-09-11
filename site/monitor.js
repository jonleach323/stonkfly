// monitor.js — paints the SatRush-style terminal that the 3D fly is looking at.
//
// drawMonitor(ctx, state, board, now) draws onto a 960x540 2D canvas that
// scene.js uploads as a CanvasTexture. It only reads the snapshot documented in
// STATE.md; nothing here is a source of truth. Figures are drawn at 20px or
// larger and secondary labels at 16px: the texture covers about a third of the
// render width once projected onto the CRT, so anything smaller is a smear.

export const MONITOR_WIDTH = 960;
export const MONITOR_HEIGHT = 540;

const COLORS = {
  bg: '#0a0c10',
  header: '#11151c',
  panel: '#0e1015',
  line: '#32353e',
  muted: '#989aaa',
  ink: '#f3f4ed',
  acid: '#bdff32',
  red: '#ff4b78',
  blue: '#7376ff',
  tileLow: [19, 23, 31],       // empty tile
  tileHigh: [214, 112, 42],    // heaviest stake (warm ramp, like the retina frame)
};

// Loads nothing itself: the page declares the face, the canvas falls back to monospace.
const PIXEL = '"Press Start 2P", "Courier New", monospace';
const font = (px) => `${px}px ${PIXEL}`;

const COLUMNS = 7;
const ROWS = 3;
const TILES = COLUMNS * ROWS;

// Layout (all in canvas pixels).
const PAD = 24;
const HEADER_H = 56;
const ROUND_ROW_Y = 66;
const GRID_TOP = 112;
const TILE_W = 78;
const TILE_H = 100;
const TILE_GAP = 8;
const GRID_W = COLUMNS * TILE_W + (COLUMNS - 1) * TILE_GAP; // 594
const RAIL_X = PAD + GRID_W + PAD; // 642
const RAIL_W = MONITOR_WIDTH - PAD - RAIL_X; // 294
const FOOTER_Y = 462;

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

// Whole dollars from $1,000 so the 20px rail line still fits.
function signedUsd(value) {
  const n = num(value);
  if (n === null) return '—';
  return (n > 0 ? '+' : '') + usd(n, Math.abs(n) >= 1000 ? 0 : 2);
}

// Short stake label that fits a tile at 16px (4 glyphs max): "$0", "$2.2", "$30", "$12K".
function tileStakeLabel(value) {
  const n = num(value);
  if (n === null) return '';
  if (n >= 1000) return `$${Math.round(n / 1000)}K`;
  if (n >= 10) return `$${Math.round(n)}`;
  return `$${n.toFixed(1)}`;
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

// Word-wrap by character count (the pixel font is square, so 1 char = font px).
function wrapWords(words, maxChars, maxLines) {
  const lines = [];
  let current = '';
  for (const word of words) {
    const next = current ? `${current} ${word}` : word;
    if (next.length <= maxChars) {
      current = next;
    } else {
      lines.push(current);
      current = word;
    }
  }
  if (current) lines.push(current);
  if (lines.length > maxLines) {
    const shown = lines.slice(0, maxLines);
    const hidden = words.length - shown.join(' ').split(' ').length;
    shown[maxLines - 1] = `${shown[maxLines - 1]} +${hidden}`;
    if (shown[maxLines - 1].length > maxChars) shown[maxLines - 1] = `+${hidden} MORE`;
    return shown;
  }
  return lines;
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

function frame(ctx, x, y, w, h, stroke, width = 2) {
  ctx.lineWidth = width;
  ctx.strokeStyle = stroke;
  ctx.strokeRect(x + width / 2, y + width / 2, w - width, h - width);
}

function text(ctx, str, x, y, px, color, align = 'left', baseline = 'alphabetic') {
  ctx.font = font(px);
  ctx.fillStyle = color;
  ctx.textAlign = align;
  ctx.textBaseline = baseline;
  ctx.fillText(str, x, y);
}

// ---------------------------------------------------------------------------
// Sections.

function drawHeader(ctx, state, board, now) {
  rect(ctx, 0, 0, MONITOR_WIDTH, HEADER_H, COLORS.header);
  rect(ctx, 0, HEADER_H, MONITOR_WIDTH, 2, COLORS.line);

  // Wordmark with a small acid block, like a favicon.
  rect(ctx, PAD, 16, 24, 24, COLORS.acid);
  text(ctx, 'SAT RUSH', PAD + 36, 40, 28, COLORS.ink);

  if (board) {
    const miners = integer(board.miners);
    const minersLabel = `MINERS ${miners}`;
    text(ctx, minersLabel, MONITOR_WIDTH - PAD, 38, 20, COLORS.muted, 'right');
    const minersWidth = ctx.measureText(minersLabel).width;
    text(ctx, `POT ${compactUsd(board.pot_usd)}`, MONITOR_WIDTH - PAD - minersWidth - 32, 38, 20, COLORS.ink, 'right');
  } else {
    text(ctx, 'NO BOARD', MONITOR_WIDTH - PAD, 38, 20, COLORS.muted, 'right');
  }

  // Round row: number, countdown bar, seconds left.
  const y = ROUND_ROW_Y;
  const roundId = board && board.round_id != null ? `#${board.round_id}` : '#—';
  text(ctx, `ROUND ${roundId}`, PAD, y + 28, 20, COLORS.ink);

  const barX = PAD + 20 * 12 + 24;
  const barW = MONITOR_WIDTH - PAD - 20 * 8 - 16 - barX; // leaves room for 'ROTATING'
  rect(ctx, barX, y + 12, barW, 18, COLORS.panel);
  frame(ctx, barX, y + 12, barW, 18, COLORS.line, 2);

  const cd = countdown(board, now);
  let label = '—';
  if (cd) {
    // Same words and m:ss as the page's LIVE BOARD panel.
    if (cd.pending) label = 'ROTATING';
    else if (cd.over) label = board.live === true ? 'DRAWING' : 'ENDED';
    else {
      const s = Math.ceil(cd.remaining);
      label = `${Math.floor(s / 60)}:${String(s % 60).padStart(2, '0')}`;
    }
    if (cd.fraction !== null && cd.fraction > 0) {
      const inner = Math.max(2, Math.round((barW - 8) * cd.fraction));
      rect(ctx, barX + 4, y + 16, inner, 10, cd.remaining < 10 ? COLORS.red : COLORS.acid);
    }
  }
  text(ctx, label, MONITOR_WIDTH - PAD, y + 28, 20, cd && cd.over ? COLORS.muted : COLORS.acid, 'right');
}

function drawGrid(ctx, state, board) {
  const stakes = board ? board.tile_stakes.map((t) => num(t && t.stake_usd) || 0) : new Array(TILES).fill(0);
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

  for (let i = 0; i < TILES; i += 1) {
    const col = i % COLUMNS;
    const row = Math.floor(i / COLUMNS);
    const x = PAD + col * (TILE_W + TILE_GAP);
    const y = GRID_TOP + row * (TILE_H + TILE_GAP);
    const tile = i + 1;
    const share = maxStake > 0 ? stakes[i] / maxStake : 0;

    rect(ctx, x, y, TILE_W, TILE_H, lerpColor(COLORS.tileLow, COLORS.tileHigh, share * 0.9));
    frame(ctx, x, y, TILE_W, TILE_H, COLORS.line, 2);

    const rank = recent.get(tile);
    const isWinner = rank === 0;
    const isChosen = chosen.has(tile);

    if (rank !== undefined && rank > 0) frame(ctx, x + 2, y + 2, TILE_W - 4, TILE_H - 4, 'rgba(255,75,120,0.35)', 2);
    if (isWinner) {
      rect(ctx, x, y, TILE_W, TILE_H, 'rgba(255,75,120,0.28)');
      frame(ctx, x, y, TILE_W, TILE_H, COLORS.red, 4);
    }
    if (isChosen) {
      const inset = isWinner ? 6 : 0;
      frame(ctx, x + inset, y + inset, TILE_W - inset * 2, TILE_H - inset * 2, COLORS.acid, 4);
      rect(ctx, x + TILE_W - 18, y + 8, 10, 10, COLORS.acid);
    }

    const numberColor = isChosen ? COLORS.acid : (share > 0.55 ? COLORS.bg : COLORS.ink);
    text(ctx, String(tile), x + 9, y + 32, 20, numberColor);
    if (board) {
      const stakeColor = share > 0.55 ? COLORS.bg : COLORS.muted;
      text(ctx, tileStakeLabel(stakes[i]), x + 9, y + TILE_H - 12, 16, stakeColor);
    }
  }
}

function drawRail(ctx, state, boardInfo) {
  const x = RAIL_X;
  const right = RAIL_X + RAIL_W;
  const neural = (state && state.neural) || {};
  const tiles = Array.isArray(neural.tiles) ? neural.tiles : [];
  const maxChars = Math.floor(RAIL_W / 20);

  // NEURAL PICK: the tiles proposed by the fixed readout for the observed round.
  let y = GRID_TOP + 14;
  text(ctx, 'NEURAL PICK', x, y, 16, COLORS.muted);
  text(ctx, `${tiles.length}/21`, right, y + 2, 20, tiles.length ? COLORS.acid : COLORS.muted, 'right');
  let lines = ['—'];
  if (tiles.length >= TILES) lines = ['ALL 21 TILES'];
  else if (tiles.length) lines = wrapWords(tiles.map(String), maxChars, 2);
  lines.forEach((line, i) => text(ctx, line, x, y + 32 + i * 26, 20, COLORS.acid));
  const pickRound = state && state.board && state.board.round_id != null ? state.board.round_id : null;
  if (pickRound !== null && boardInfo.live && boardInfo.board && boardInfo.board.round_id !== pickRound) {
    text(ctx, `FOR ROUND #${pickRound}`, x, y + 32 + lines.length * 26 - 4, 16, COLORS.muted);
  }

  // STAKE / VALUE: label left, figure right. Paper balances are simulated.
  y = GRID_TOP + 126;
  const settings = (state && state.settings) || {};
  const portfolio = (state && state.portfolio) || {};
  text(ctx, 'STAKE', x, y, 16, COLORS.muted);
  text(ctx, usd(settings.stake), right, y + 1, 20, COLORS.ink, 'right');
  text(ctx, state && state.mode === 'live' ? 'VALUE' : 'PAPER', x, y + 30, 16, COLORS.muted);
  text(ctx, compactUsd(portfolio.equity), right, y + 31, 20, COLORS.ink, 'right');

  // LAST ROUND
  y = GRID_TOP + 186;
  const last = state && Array.isArray(state.last_outcomes) && state.last_outcomes[0] ? state.last_outcomes[0] : null;
  text(ctx, last && last.simulated ? 'LAST ROUND · SIM' : 'LAST ROUND', x, y, 16, COLORS.muted);
  if (last) {
    const hit = last.won === true;
    const verdict = last.won === null || last.won === undefined ? 'OPEN' : (hit ? 'HIT' : 'MISS');
    text(ctx, `#${last.round_id} ${verdict}`, x, y + 28, 20, hit ? COLORS.acid : COLORS.red);
    // Refund and sats are in the page's ROUNDS table; only the P&L stays legible here.
    const pnl = num(last.pnl_usd);
    text(ctx, `P&L ${signedUsd(pnl)}`, x, y + 56, 20, pnl !== null && pnl >= 0 ? COLORS.acid : COLORS.red);
  } else {
    text(ctx, 'NONE SETTLED', x, y + 28, 20, COLORS.muted);
  }

  // DEPLOY block: reads PAPER or LIVE. It is a picture of a button, not one.
  const by = FOOTER_Y - 56;
  const live = state && state.mode === 'live';
  const halted = state && state.status && state.status.halted;
  const fill = halted ? COLORS.line : (live ? COLORS.blue : COLORS.acid);
  rect(ctx, x, by, RAIL_W, 44, fill);
  rect(ctx, x + 3, by + 44, RAIL_W, 3, '#000000');
  rect(ctx, x + RAIL_W, by + 3, 3, 44, '#000000');
  const label = halted ? 'HALTED' : (live ? 'DEPLOY · LIVE' : 'DEPLOY · PAPER');
  text(ctx, label, x + RAIL_W / 2, by + 23, 18, live ? COLORS.ink : COLORS.bg, 'center', 'middle');
}

function drawFooter(ctx, state) {
  rect(ctx, 0, FOOTER_Y, MONITOR_WIDTH, 2, COLORS.line);
  const neural = (state && state.neural) || {};
  const tick = state && state.tick != null ? state.tick : '—';
  const brainMs = num(neural.brain_ms);
  const brain = brainMs === null ? '—' : (brainMs >= 1000 ? `${(brainMs / 1000).toFixed(1)}S` : `${Math.round(brainMs)}MS`);
  text(ctx, `OBS #${tick} · BRAIN ${brain}`, PAD, FOOTER_Y + 30, 20, COLORS.muted);
  const spikes = num(neural.total_spikes);
  text(ctx, spikes === null ? '' : `${integer(spikes)} SPIKES`, MONITOR_WIDTH - PAD, FOOTER_Y + 30, 20, COLORS.muted, 'right');

  const status = (state && state.status) || {};
  const modeLabel = neural.stub || status.stub_brain ? 'STUB BRAIN' : (state && state.mode === 'live' ? 'LIVE WALLET' : 'PAPER');
  let phase = (status.phase || 'no worker').toUpperCase();
  if (status.halted) phase = `HALTED · ${String(status.halted).toUpperCase()}`;
  // The phase takes whatever width the mode label leaves on the same line.
  const maxChars = Math.floor((MONITOR_WIDTH - PAD * 2 - modeLabel.length * 20 - 16) / 20);
  if (phase.length > maxChars) phase = `${phase.slice(0, maxChars - 1)}…`;
  text(ctx, phase, PAD, FOOTER_Y + 58, 20, status.halted ? COLORS.red : COLORS.muted);
  text(ctx, modeLabel, MONITOR_WIDTH - PAD, FOOTER_Y + 58, 20, neural.stub || status.stub_brain ? COLORS.red : COLORS.acid, 'right');
}

function drawWaiting(ctx, now) {
  const blink = Math.floor(now * 2) % 2 === 0;
  text(ctx, 'WAITING FOR SNAPSHOT', RAIL_X + RAIL_W / 2, GRID_TOP + 150, 16, COLORS.muted, 'center');
  if (blink) rect(ctx, RAIL_X + RAIL_W / 2 - 8, GRID_TOP + 170, 16, 20, COLORS.acid);
  rect(ctx, 0, FOOTER_Y, MONITOR_WIDTH, 2, COLORS.line);
  text(ctx, 'STONKFLY · READ-ONLY SNAPSHOT', PAD, FOOTER_Y + 30, 16, COLORS.muted);
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
  ctx.imageSmoothingEnabled = false;
  rect(ctx, 0, 0, MONITOR_WIDTH, MONITOR_HEIGHT, COLORS.bg);

  const ready = !!(state && state.ready);
  const boardInfo = pickBoard(ready ? state : null, board);

  drawHeader(ctx, ready ? state : null, boardInfo.board, now);
  drawGrid(ctx, ready ? state : null, boardInfo.board);
  if (ready) {
    drawRail(ctx, state, boardInfo);
    drawFooter(ctx, state);
  } else {
    drawWaiting(ctx, now);
  }
  ctx.restore();
}
