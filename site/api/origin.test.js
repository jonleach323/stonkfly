// node --test: origin mode reads the worker's own endpoint; blob mode reads the published folder.
import { test } from "node:test";
import assert from "node:assert/strict";

import { clearMemo, originUrl, snapshotFrameUrl, snapshotUrl } from "./_lib.js";
import configHandler from "./config.js";

const SHA = "a".repeat(64);

function fakeRes() {
  const res = { statusCode: 0, headers: {}, body: null };
  res.setHeader = (k, v) => { res.headers[k.toLowerCase()] = v; };
  res.end = (b) => { res.body = b ? JSON.parse(b.toString()) : null; };
  return res;
}

test("origin mode maps snapshot names onto the worker's /api", () => {
  const env = { STONKFLY_ORIGIN_URL: "https://fly.example.com/" };
  assert.equal(originUrl(env), "https://fly.example.com");
  assert.equal(snapshotUrl("state.json", env), "https://fly.example.com/api/state");
  assert.equal(snapshotUrl("audit.json", env), "https://fly.example.com/api/audit");
  assert.equal(snapshotFrameUrl(SHA, env), "https://fly.example.com/api/sensory.png");
  assert.throws(() => originUrl({ STONKFLY_ORIGIN_URL: "fly.example.com" }), /http\(s\)/);
});

test("blob mode is unchanged and origin is null when unset", () => {
  const env = { SNAPSHOT_BASE_URL: "https://store.public.blob.vercel-storage.com/stonkfly" };
  assert.equal(originUrl(env), null);
  assert.equal(snapshotUrl("state.json", env), "https://store.public.blob.vercel-storage.com/stonkfly/state.json");
  assert.equal(snapshotFrameUrl(SHA, env), `https://store.public.blob.vercel-storage.com/stonkfly/frames/${SHA}.png`);
});

test("/api/config reports the source and caches for a minute", async () => {
  clearMemo();
  const saved = { ...process.env };
  try {
    process.env.STONKFLY_ORIGIN_URL = "https://fly.example.com";
    delete process.env.SNAPSHOT_BASE_URL;
    let res = fakeRes();
    await configHandler({ method: "GET", headers: {} }, res);
    assert.equal(res.statusCode, 200);
    assert.deepEqual(res.body, { origin: "https://fly.example.com", network: "mainnet", source: "origin" });
    assert.match(res.headers["cache-control"], /max-age=60/);
    delete process.env.STONKFLY_ORIGIN_URL;
    process.env.SNAPSHOT_BASE_URL = "https://store.public.blob.vercel-storage.com/stonkfly";
    process.env.SATRUSH_NETWORK = "devnet";
    res = fakeRes();
    await configHandler({ method: "GET", headers: {} }, res);
    assert.deepEqual(res.body, { origin: null, network: "devnet", source: "blob" });
    delete process.env.SNAPSHOT_BASE_URL;
    res = fakeRes();
    await configHandler({ method: "GET", headers: {} }, res);
    assert.equal(res.body.source, "unconfigured");
    res = fakeRes();
    await configHandler({ method: "POST", headers: {} }, res);
    assert.equal(res.statusCode, 405);
  } finally {
    for (const k of Object.keys(process.env)) if (!(k in saved)) delete process.env[k];
    Object.assign(process.env, saved);
  }
});
