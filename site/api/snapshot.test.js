// node --test site/api/snapshot.test.js
// The snapshot functions (/api/state, /api/audit, /api/sensory.png) against a
// stub "Blob store": pass-through, hosting-problem reporting and the
// content-addressed frame. No network.

import test from "node:test";
import assert from "node:assert/strict";
import http from "node:http";
import { createHash } from "node:crypto";

import stateHandler from "./state.js";
import auditHandler from "./audit.js";
import sensoryHandler from "./sensory.png.js";
import { clearMemo, describeError, ConfigError, UpstreamError } from "./_lib.js";

const PNG = Buffer.concat([Buffer.from([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a]), Buffer.from("frame-one")]);
const SHA = createHash("sha256").update(PNG).digest("hex");
const STATE = { ready: true, version: 1, published_at: 1789107572.3, portfolio: { equity: "1.000000" }, publication: { frame_sha256: SHA, publish_error: null } };
const AUDIT = { ready: true, deployments: [] };

function listen(server) {
  return new Promise((resolve) => server.listen(0, "127.0.0.1", () => resolve(`http://127.0.0.1:${server.address().port}`)));
}

function route(req, res) {
  const path = req.url.split("?")[0];
  if (path === "/api/state") return stateHandler(req, res);
  if (path === "/api/audit") return auditHandler(req, res);
  if (path === "/api/sensory.png") return sensoryHandler(req, res);
  res.writeHead(404).end();
}

test("snapshot functions pass documents through and report the failing layer", async (t) => {
  const store = { state: STATE, audit: AUDIT, frames: { [SHA]: PNG }, hits: [] };
  const stub = http.createServer((req, res) => {
    store.hits.push(req.url);
    const m = /^\/stonkfly\/(state\.json|audit\.json|frames\/([0-9a-f]{64})\.png)$/.exec(req.url);
    if (!m) return res.writeHead(404).end("no");
    if (m[1] === "state.json" && store.state) return res.writeHead(200, { "content-type": "application/json" }).end(JSON.stringify(store.state));
    if (m[1] === "audit.json" && store.audit) return res.writeHead(200, { "content-type": "application/json" }).end(JSON.stringify(store.audit));
    if (m[2] && store.frames[m[2]]) return res.writeHead(200, { "content-type": "image/png" }).end(store.frames[m[2]]);
    res.writeHead(404).end("no");
  });
  const app = http.createServer(route);
  const stubUrl = await listen(stub);
  const appUrl = await listen(app);
  const saved = process.env.SNAPSHOT_BASE_URL;
  const quiet = t.mock.method(console, "error", () => {});
  t.after(() => {
    stub.close();
    app.close();
    quiet.mock.restore();
    if (saved === undefined) delete process.env.SNAPSHOT_BASE_URL; else process.env.SNAPSHOT_BASE_URL = saved;
    clearMemo();
  });

  // Unconfigured hosting is named as such, with no environment detail in the public text.
  delete process.env.SNAPSHOT_BASE_URL;
  clearMemo();
  let r = await fetch(`${appUrl}/api/state`);
  assert.equal(r.status, 200);
  let body = await r.json();
  assert.deepEqual([body.ready, body.hosting, body.error], [false, "unconfigured", "hosting not configured"]);
  assert.ok(typeof body.publication.served_at === "number");
  body = await (await fetch(`${appUrl}/api/audit`)).json();
  assert.deepEqual([body.ready, body.hosting], [false, "unconfigured"]);
  r = await fetch(`${appUrl}/api/sensory.png`);
  assert.equal(r.status, 503);
  assert.equal(r.headers.get("cache-control"), "no-store");

  process.env.SNAPSHOT_BASE_URL = `${stubUrl}/stonkfly/`;

  // Nothing published yet: the store answers 404.
  clearMemo();
  store.state = null;
  body = await (await fetch(`${appUrl}/api/state`)).json();
  assert.deepEqual([body.ready, body.hosting, body.error], [false, "unpublished", "state.json: upstream answered HTTP 404"]);
  r = await fetch(`${appUrl}/api/sensory.png`);
  assert.equal(r.status, 404);
  assert.equal(r.headers.get("cache-control"), "no-store");

  // Published: state passes through with served_at added; the frame carries the SHA the state names.
  clearMemo();
  store.state = STATE;
  r = await fetch(`${appUrl}/api/state`);
  assert.equal(r.headers.get("cache-control"), "public, max-age=1, s-maxage=1, stale-while-revalidate=5");
  body = await r.json();
  assert.equal(body.ready, true);
  assert.equal(body.publication.frame_sha256, SHA);
  assert.ok(Math.abs(body.publication.served_at - Date.now() / 1000) < 5);
  assert.equal(body.error, undefined);
  assert.deepEqual(await (await fetch(`${appUrl}/api/audit`)).json(), AUDIT);

  r = await fetch(`${appUrl}/api/sensory.png`);
  assert.equal(r.status, 200);
  assert.equal(r.headers.get("content-type"), "image/png");
  assert.equal(r.headers.get("etag"), `"${SHA}"`);
  assert.equal(r.headers.get("cache-control"), "public, max-age=1, s-maxage=1, stale-while-revalidate=5"); // no version asked for
  assert.ok(Buffer.from(await r.arrayBuffer()).equals(PNG));
  assert.ok(store.hits.includes(`/stonkfly/frames/${SHA}.png`));

  // A version that names the served frame (prefix or full digest) is immutable; a full digest skips state.json.
  r = await fetch(`${appUrl}/api/sensory.png?v=${SHA.slice(0, 16)}`);
  assert.equal(r.headers.get("cache-control"), "public, max-age=31536000, immutable");
  clearMemo();
  store.state = null;
  r = await fetch(`${appUrl}/api/sensory.png?v=${SHA}`);
  assert.equal(r.status, 200);
  assert.equal(r.headers.get("cache-control"), "public, max-age=31536000, immutable");
  store.state = STATE;

  // A version the function cannot serve gets the current frame, but not immutably.
  clearMemo();
  r = await fetch(`${appUrl}/api/sensory.png?v=0123456789abcdef`);
  assert.equal(r.status, 200);
  assert.equal(r.headers.get("cache-control"), "public, max-age=1, s-maxage=1, stale-while-revalidate=5");

  // Conditional request.
  r = await fetch(`${appUrl}/api/sensory.png?v=${SHA.slice(0, 16)}`, { headers: { "if-none-match": `"${SHA}"` } });
  assert.equal(r.status, 304);
  assert.equal(r.headers.get("x-content-type-options"), "nosniff");
  assert.equal(r.headers.get("cache-control"), "public, max-age=31536000, immutable");

  // Bytes that do not hash to the name they were published under are refused, not relabelled.
  clearMemo();
  store.frames[SHA] = Buffer.concat([PNG, Buffer.from("!")]);
  r = await fetch(`${appUrl}/api/sensory.png`);
  assert.equal(r.status, 503);
  assert.match((await r.json()).error, /does not match/);
  store.frames[SHA] = PNG;

  // Read-only.
  r = await fetch(`${appUrl}/api/sensory.png`, { method: "POST" });
  assert.equal(r.status, 405);
});

test("describeError never forwards infrastructure detail", () => {
  assert.equal(describeError(new ConfigError("SNAPSHOT_BASE_URL is not configured")), "hosting not configured");
  assert.equal(describeError(new UpstreamError("state.json: upstream answered HTTP 502", 502)), "state.json: upstream answered HTTP 502");
  const fetchFailed = new TypeError("fetch failed");
  fetchFailed.cause = Object.assign(new Error("connect ECONNREFUSED 10.0.0.1:443"), { code: "ECONNREFUSED" });
  assert.equal(describeError(fetchFailed), "upstream unreachable");
  assert.equal(describeError(Object.assign(new Error("x"), { name: "TimeoutError" })), "upstream timed out after 8 s");
});
