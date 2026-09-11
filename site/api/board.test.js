// node --test site/api/board.test.js
// Checks the JS port of stonkfly/satrush/api.py (parse_board + Board.summary)
// against the committed public board fixture, and the handler against a stub
// upstream. No network.

import test from "node:test";
import assert from "node:assert/strict";
import http from "node:http";
import { readFileSync } from "node:fs";

import handler, { fetchBoardSummary, parseBoard, summarizeBoard, utcSeconds } from "./board.js";
import { clearMemo, USER_AGENT } from "./_lib.js";

const FIXTURE = JSON.parse(readFileSync(new URL("../../tests/fixtures/board.json", import.meta.url), "utf8"));
const DATA = FIXTURE.data;
const U64_MAX = "18446744073709551615";

test("utcSeconds keeps microseconds from nanosecond RFC 3339 stamps", () => {
  // Reference values computed with stonkfly.satrush.api.utc_seconds.
  assert.ok(Math.abs(utcSeconds("2026-09-11T03:41:57.329573295Z") - 1789098117.329573) < 1e-6);
  assert.ok(Math.abs(utcSeconds("2026-09-11T03:40:54.283573295Z") - 1789098054.283573) < 1e-6);
  assert.equal(utcSeconds("2026-09-11T03:41:57Z"), 1789098117);
  assert.equal(utcSeconds("2026-09-11T05:41:57+02:00"), 1789098117);
  assert.equal(utcSeconds("2026-09-11T03:41:57.5+00:00"), 1789098117.5);
  // Offsets without a colon or without minutes, as Python 3.11 fromisoformat accepts them.
  assert.equal(utcSeconds("2026-09-11T05:41:57+0200"), 1789098117);
  assert.equal(utcSeconds("2026-09-11T05:41:57+02"), 1789098117);
  assert.equal(utcSeconds("2026-09-10T22:11:57-0530"), 1789098117);
  assert.equal(utcSeconds(1789105317329), 1789105317.329); // milliseconds
  assert.equal(utcSeconds(1789105317.5), 1789105317.5); // seconds
  assert.equal(utcSeconds(null), null);
  assert.throws(() => utcSeconds("2026-09-11T03:41:57"), /timezone/);
  assert.throws(() => utcSeconds("yesterday"), /RFC 3339/);
});

test("parses the public board fixture like the Python worker", () => {
  const summary = summarizeBoard(parseBoard(DATA, 0));
  assert.equal(summary.round_id, 55576);
  assert.equal(summary.state, "active");
  assert.equal(summary.pending_activation, false);
  assert.equal(summary.slots_remaining, 39);
  assert.ok(Math.abs(summary.slot_ms - 315.2285864424593) < 1e-9);
  assert.ok(Math.abs(summary.started_at - 1789098054.283573) < 1e-6);
  assert.ok(Math.abs(summary.ends_at - 1789098117.329573) < 1e-6);
  assert.ok(Math.abs(summary.ends_at - summary.started_at - 63) < 2);
  assert.ok(Math.abs(summary.pot_usd - 476.935692) < 1e-9);
  assert.equal(summary.miners, 76);
  assert.equal(summary.tile_stakes.length, 21);
  assert.deepEqual(summary.tile_stakes[0], { stake_usd: 20.154744, miners: 70 });
  assert.deepEqual(summary.tile_stakes[20], { stake_usd: 19.885443, miners: 67 });
  assert.deepEqual(summary.previous_winners[0], { round_id: 55575, tile: 21 });
  assert.deepEqual(
    summary.previous_winners.map((w) => w.round_id),
    [55575, 55574, 55573, 55572, 55571],
  );
  assert.deepEqual(summary.previous_round, { round_id: 55575, winning_tile: 21, pot_sats: 57742, winners: 68, miners: 77 });
  assert.ok(summary.prices.btc > 0);
  assert.deepEqual(Object.keys(summary.prices), ["btc", "sat", "token", "token_share"]);
  assert.equal(summary.fetched_at, 0);
  assert.deepEqual(Object.keys(summary), [
    "round_id", "state", "pending_activation", "started_at", "ends_at", "slots_remaining", "slot_ms",
    "pot_usd", "miners", "tile_stakes", "previous_winners", "previous_round", "prices", "fetched_at",
  ]);
  assert.doesNotThrow(() => JSON.stringify(summary)); // no BigInt leaks into the response
});

test("previous winners are sorted newest first and 1-based", () => {
  const data = { ...DATA, previous_rounds: [{ round_id: 1, winning_tile: 0 }, { round_id: 3, winning_tile: 20 }, { round_id: 2 }, "junk"] };
  const summary = summarizeBoard(parseBoard(data, 0));
  assert.deepEqual(summary.previous_winners, [{ round_id: 3, tile: 21 }, { round_id: 1, tile: 1 }]);
});

test("pending activation follows start_slot == 2^64-1", () => {
  const summary = summarizeBoard(parseBoard({ ...DATA, start_slot: U64_MAX, end_slot: U64_MAX }, 0));
  assert.equal(summary.pending_activation, true);
  assert.equal(summary.slots_remaining, null);
});

test("previous round without a result and non-numeric prices", () => {
  const data = { ...DATA, previous_round: { id: 9, winning_tile: null, deployed_btc_amount: "0" }, prices: { btc: 1, note: "x", nan: null } };
  const summary = summarizeBoard(parseBoard(data, 0));
  assert.deepEqual(summary.previous_round, { round_id: 9, winning_tile: null, pot_sats: null, winners: null, miners: null });
  assert.deepEqual(summary.prices, { btc: 1 });
  assert.equal(summarizeBoard(parseBoard({ ...DATA, previous_round: null }, 0)).previous_round, null);
  assert.equal(summarizeBoard(parseBoard({ ...DATA, previous_round: {} }, 0)).previous_round, null);
});

test("rejects bad boards", () => {
  assert.throws(() => parseBoard({}), /round_id/);
  assert.throws(() => parseBoard(FIXTURE), /round_id/); // the envelope, not its data
  assert.throws(() => parseBoard({ ...DATA, active_round: { tile_stakes: [] } }), /21 tiles/);
  assert.throws(() => parseBoard({ ...DATA, start_slot: "soon" }), /Slot/);
});

test("integer fields follow Python int(): float strings are rejected, float numbers truncated", () => {
  const withStake = (stake) => ({ ...DATA, active_round: { ...DATA.active_round, tile_stakes: DATA.active_round.tile_stakes.map((t, i) => (i === 0 ? { ...t, stake } : t)) } });
  assert.throws(() => parseBoard(withStake("1.5")), /not an integer/);
  assert.throws(() => parseBoard(withStake("1e6")), /not an integer/);
  assert.equal(parseBoard(withStake(" +12 ")).tile_stakes[0][0], 12);
  assert.equal(parseBoard(withStake(1.9)).tile_stakes[0][0], 1);
});

// ---------------------------------------------------------------------------
// Handler against a stub upstream

function listen(server) {
  return new Promise((resolve) => server.listen(0, "127.0.0.1", () => resolve(`http://127.0.0.1:${server.address().port}`)));
}

test("handler proxies, memoizes and reports upstream failure", async (t) => {
  const upstream = { hits: 0, status: 200, body: JSON.stringify(FIXTURE), agents: [] };
  const stub = http.createServer((req, res) => {
    upstream.hits += 1;
    upstream.agents.push(req.headers["user-agent"]);
    assert.equal(req.url, "/api/v1/board");
    res.writeHead(upstream.status, { "content-type": "application/json" });
    res.end(upstream.body);
  });
  const app = http.createServer((req, res) => handler(req, res));
  const stubUrl = await listen(stub);
  const appUrl = await listen(app);
  const savedEnv = { api: process.env.SATRUSH_API_URL, network: process.env.SATRUSH_NETWORK };
  process.env.SATRUSH_API_URL = `${stubUrl}/api/`;
  const quiet = t.mock.method(console, "error", () => {}); // the handler logs every upstream failure
  t.after(() => {
    stub.close();
    app.close();
    quiet.mock.restore();
    if (savedEnv.api === undefined) delete process.env.SATRUSH_API_URL; else process.env.SATRUSH_API_URL = savedEnv.api;
    if (savedEnv.network === undefined) delete process.env.SATRUSH_NETWORK; else process.env.SATRUSH_NETWORK = savedEnv.network;
    clearMemo();
  });
  clearMemo();

  const first = await fetch(`${appUrl}/api/board`);
  assert.equal(first.status, 200);
  assert.equal(first.headers.get("cache-control"), "public, max-age=1, s-maxage=1, stale-while-revalidate=5");
  assert.match(first.headers.get("content-type"), /application\/json/);
  const board = await first.json();
  assert.equal(board.live, true);
  assert.equal(board.round_id, 55576);
  assert.equal(board.tile_stakes.length, 21);
  assert.deepEqual(board.previous_winners[0], { round_id: 55575, tile: 21 });
  assert.ok(Math.abs(board.fetched_at - Date.now() / 1000) < 5);
  assert.equal(upstream.agents[0], USER_AGENT);

  // Many viewers within the same second share one upstream read.
  const burst = await Promise.all([1, 2, 3, 4, 5].map(() => fetch(`${appUrl}/api/board`).then((r) => r.json())));
  assert.ok(burst.every((b) => b.live === true && b.round_id === 55576));
  assert.equal(upstream.hits, 1);

  // A method other than GET is refused.
  const post = await fetch(`${appUrl}/api/board`, { method: "POST" });
  assert.equal(post.status, 405);
  assert.equal(post.headers.get("allow"), "GET, HEAD");

  // Upstream failure: 502 {live:false, error}, not cached.
  clearMemo();
  upstream.status = 503;
  upstream.body = "busy";
  const failed = await fetch(`${appUrl}/api/board`);
  assert.equal(failed.status, 502);
  assert.equal(failed.headers.get("cache-control"), "no-store");
  const failure = await failed.json();
  assert.equal(failure.live, false);
  assert.match(failure.error, /HTTP 503/);

  // A body without the data envelope is a failure too.
  clearMemo();
  upstream.status = 200;
  upstream.body = JSON.stringify({ round_id: 1 });
  const noEnvelope = await (await fetch(`${appUrl}/api/board`)).json();
  assert.equal(noEnvelope.live, false);
  assert.match(noEnvelope.error, /data envelope/);

  // Direct call: a malformed board inside the envelope is rejected by the parser.
  upstream.body = JSON.stringify({ data: { round_id: 1 } });
  await assert.rejects(fetchBoardSummary(), /21 tiles/);
});

test("devnet and mainnet defaults", async () => {
  const { satrushApiUrl } = await import("./_lib.js");
  assert.equal(satrushApiUrl({}), "https://api.satrush.io/api");
  assert.equal(satrushApiUrl({ SATRUSH_NETWORK: "devnet" }), "https://api-devnet.satrush.io/api");
  assert.equal(satrushApiUrl({ SATRUSH_API_URL: "http://localhost:9/api/" }), "http://localhost:9/api");
});
