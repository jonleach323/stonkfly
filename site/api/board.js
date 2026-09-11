// GET /api/board: the public SatRush board, proxied server-side and summarized
// exactly like `Board.summary()` in stonkfly/satrush/api.py, plus `live: true`.
//
// The proxy exists because the game's edge blocks browser and default user
// agents; it adds no data of its own. Memoized for one second per instance.

import { allowGet, describeError, fetchUpstream, logError, memo, nowSeconds, satrushApiUrl, sendJson, UpstreamError } from "./_lib.js";

export const TILES = 21;
const U64_MAX = (1n << 64n) - 1n;
// Zone forms Python's fromisoformat accepts: Z, +HH:MM, +HHMM and +HH.
const ISO = /^(\d{4})-(\d{2})-(\d{2})[T ](\d{2}):(\d{2}):(\d{2})(?:\.(\d+))?\s*(Z|z|[+-]\d{2}(?::?\d{2})?)?$/;
const INTEGER = /^\s*[+-]?\d+\s*$/;

/** Port of `utc_seconds`: RFC 3339 (any number of fractional digits) or a number → Unix seconds. */
export function utcSeconds(value) {
  if (value === null || value === undefined) return null;
  if (typeof value === "number") {
    if (!Number.isFinite(value)) throw new TypeError("API timestamp is not finite");
    return value / (value > 1e12 ? 1000 : 1);
  }
  const text = String(value).trim();
  const m = ISO.exec(text);
  if (!m) throw new TypeError("API timestamp is not RFC 3339");
  const [, year, month, day, hour, minute, second, fraction, zone] = m;
  if (!zone) throw new TypeError("API timestamp requires a timezone");
  const whole = Date.UTC(+year, +month - 1, +day, +hour, +minute, +second) / 1000;
  if (!Number.isFinite(whole)) throw new TypeError("API timestamp is out of range");
  // Like the Python side, keep microseconds and drop anything finer.
  const micro = fraction ? Number(fraction.slice(0, 6).padEnd(6, "0")) / 1e6 : 0;
  let offset = 0;
  if (zone !== "Z" && zone !== "z") {
    const sign = zone[0] === "-" ? -1 : 1;
    const digits = zone.slice(1).replace(":", "").padEnd(4, "0");
    offset = sign * (Number(digits.slice(0, 2)) * 3600 + Number(digits.slice(2, 4)) * 60);
  }
  return whole + micro - offset;
}

function isInt(value) {
  return typeof value === "number" && Number.isInteger(value);
}

/** Python `int(x)` for the API's numeric strings; `0` for missing values when `fallback` is given. */
function toInt(value, fallback) {
  if (value === null || value === undefined || value === "") {
    if (fallback !== undefined) return fallback;
    throw new TypeError("Board field is missing");
  }
  // Python truncates a float but rejects a non-integer string ("1.5"): a malformed stake must not become money.
  if (typeof value !== "number" && !INTEGER.test(String(value))) throw new TypeError("Board field is not an integer");
  const n = typeof value === "number" ? value : Number(String(value).trim());
  if (!Number.isFinite(n)) throw new TypeError("Board field is not a number");
  return Math.trunc(n);
}

function toBigInt(value) {
  if (typeof value === "bigint") return value;
  if (typeof value === "number") {
    if (!Number.isInteger(value)) throw new TypeError("Slot is not an integer");
    return BigInt(value);
  }
  const text = String(value ?? "").trim();
  if (!/^-?\d+$/.test(text)) throw new TypeError("Slot is not an integer");
  return BigInt(text);
}

/** Port of `parse_board`: validates the public `/v1/board` data and normalizes it. */
export function parseBoard(data, fetchedAt = nowSeconds()) {
  if (!data || typeof data !== "object" || Array.isArray(data) || !isInt(data.round_id)) {
    throw new UpstreamError("Board response has no round_id");
  }
  const active = data.active_round && typeof data.active_round === "object" ? data.active_round : {};
  const stakes = Array.isArray(active.tile_stakes) ? active.tile_stakes : [];
  if (stakes.length !== TILES) throw new UpstreamError("Board must have 21 tiles");
  const tileStakes = stakes.map((t) => [toInt(t.stake), toInt(t.deploy_count, 0)]);
  const previous = [];
  for (const r of Array.isArray(data.previous_rounds) ? data.previous_rounds : []) {
    if (r && typeof r === "object" && isInt(r.round_id) && isInt(r.winning_tile)) {
      previous.push([r.round_id, r.winning_tile + 1]);
    }
  }
  previous.sort((a, b) => b[0] - a[0]);
  const startSlot = toBigInt(data.start_slot);
  const endSlot = toBigInt(data.end_slot);
  const currentSlot = toBigInt(data.current_slot);
  return {
    round_id: data.round_id,
    round_state: typeof active.state === "string" ? active.state : null,
    start_slot: startSlot,
    end_slot: endSlot,
    current_slot: currentSlot,
    slot_ms: Number(data.slot_duration_ms) || 400,
    round_started_at: utcSeconds(data.round_started_at ?? null),
    round_ends_at: utcSeconds(data.round_ends_at ?? null),
    tile_stakes: tileStakes,
    miners_count: toInt(active.miners_count, 0),
    deployed_usd: toInt(active.deployed_pending_usd_amount, 0) + toInt(active.deployed_usd_amount, 0),
    previous_round: data.previous_round && typeof data.previous_round === "object" ? data.previous_round : null,
    previous_winners: previous,
    vaults: vaultSummary(data),
    prices: data.prices && typeof data.prices === "object" ? data.prices : {},
    fetched_at: fetchedAt,
  };
}

function finite(value) {
  const n = typeof value === "string" ? Number(value.trim() === "" ? NaN : value) : Number(value);
  return Number.isFinite(n) ? n : null;
}

/** Port of `vault_summary`: the three prize vaults shown above the SatRush board, in display units. */
export function vaultSummary(data) {
  const obj = (v) => (v && typeof v === "object" && !Array.isArray(v) ? v : {});
  const strike = obj(data?.strike);
  const epoch = obj(data?.epoch_vault);
  const oneBtc = obj(data?.one_btc_vault);
  const sats = finite(oneBtc.btc_amount);
  return {
    strike_usd: finite(strike.pool_combined_usd_amount),
    epoch_usd: finite(epoch.active_pool_combined_usd_amount),
    epoch_ends_at: epoch.iteration_ends_at ? utcSeconds(epoch.iteration_ends_at) : null,
    one_btc_btc: sats === null ? null : sats / 1e8,
  };
}

/** Port of `Board.summary()`: the JSON-safe public view, same shape as `state.board`. */
export function summarizeBoard(board) {
  const pending = board.start_slot === U64_MAX;
  const previous = board.previous_round && Object.keys(board.previous_round).length ? board.previous_round : null;
  // Deliberate difference from Python, which keeps every price value: only finite numbers are prices here
  // (a boolean, string or NaN in the upstream feed would otherwise reach the page, or break the JSON).
  const prices = {};
  for (const [key, value] of Object.entries(board.prices)) {
    if (typeof value === "number" && Number.isFinite(value)) prices[key] = value;
  }
  return {
    round_id: board.round_id,
    state: board.round_state,
    pending_activation: pending,
    started_at: board.round_started_at,
    ends_at: board.round_ends_at,
    slots_remaining: pending ? null : Number(board.end_slot - board.current_slot),
    slot_ms: board.slot_ms,
    pot_usd: board.deployed_usd / 1e6,
    miners: board.miners_count,
    tile_stakes: board.tile_stakes.map(([stake, count]) => ({ stake_usd: stake / 1e6, miners: count })),
    previous_winners: board.previous_winners.map(([round_id, tile]) => ({ round_id, tile })),
    previous_round: previous
      ? {
          round_id: previous.id ?? null,
          winning_tile: previous.winning_tile === null || previous.winning_tile === undefined ? null : toInt(previous.winning_tile) + 1,
          pot_sats: toInt(previous.deployed_btc_amount, 0) || null,
          winners: previous.winners_count ?? null,
          miners: previous.miners_count ?? null,
        }
      : null,
    vaults: board.vaults,
    prices,
    fetched_at: board.fetched_at,
  };
}

/** Fetch `/v1/board` from the public API and summarize it. Throws UpstreamError on any problem. */
export async function fetchBoardSummary(env = process.env) {
  const url = `${satrushApiUrl(env)}/v1/board`;
  const response = await fetchUpstream(url);
  if (!response.ok) throw new UpstreamError(`SatRush API answered HTTP ${response.status}`, response.status);
  let payload;
  try {
    payload = await response.json();
  } catch {
    throw new UpstreamError("SatRush API answered with invalid JSON");
  }
  if (!payload || typeof payload !== "object" || !("data" in payload)) {
    throw new UpstreamError("SatRush API answered without a data envelope");
  }
  try {
    return { ...summarizeBoard(parseBoard(payload.data, nowSeconds())), live: true };
  } catch (error) {
    if (error instanceof UpstreamError) throw error;
    throw new UpstreamError(`SatRush board is malformed: ${error && error.message ? error.message : error}`);
  }
}

export default async function handler(req, res) {
  if (!allowGet(req, res)) return;
  try {
    const board = await memo(`board:${satrushApiUrl()}`, () => fetchBoardSummary());
    sendJson(res, 200, board);
  } catch (error) {
    logError("board", error);
    sendJson(res, 502, { live: false, error: describeError(error) });
  }
}
