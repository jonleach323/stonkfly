// Shared helpers for the Stonkfly watch functions (Vercel Node.js runtime).
//
// Everything here is read-only. The functions proxy documents the worker
// published to a public Vercel Blob folder and the public SatRush board; they
// hold no keys, no ledger and no game state. Files in api/ whose name starts
// with "_" are not deployed as functions.

export const CACHE_CONTROL = "public, max-age=1, s-maxage=1, stale-while-revalidate=5";
export const NO_STORE = "no-store";
export const USER_AGENT = "stonkfly-watch/0.2 (+https://github.com/jonleach323/stonkfly)";
export const MEMO_MS = 1000;
export const UPSTREAM_TIMEOUT_MS = 8000;
export const SNAPSHOT_FILES = new Set(["state.json", "audit.json", "sensory.png"]);

export class ConfigError extends Error {
  name = "ConfigError";
}

export class UpstreamError extends Error {
  name = "UpstreamError";
  constructor(message, status = null) {
    super(message);
    this.status = status;
  }
}

/** Current wall time in Unix seconds, the unit the snapshot uses. */
export function nowSeconds() {
  return Date.now() / 1000;
}

// ---------------------------------------------------------------------------
// Short server-side memo: one upstream read per key per second per instance,
// shared by every concurrent viewer. Failures are memoized for the same
// second so a broken upstream is not hammered either.

const memoTable = new Map();

export function memo(key, producer, ttlMs = MEMO_MS) {
  const now = Date.now();
  const hit = memoTable.get(key);
  if (hit && now - hit.at < ttlMs) return hit.promise;
  if (memoTable.size >= 64) {
    for (const [k, entry] of memoTable) if (now - entry.at >= ttlMs) memoTable.delete(k);
  }
  const promise = Promise.resolve().then(producer);
  promise.catch(() => {}); // the caller awaits the same promise; avoid a duplicate unhandled-rejection report
  memoTable.set(key, { at: now, promise });
  return promise;
}

export function clearMemo() {
  memoTable.clear();
}

// ---------------------------------------------------------------------------
// Environment

/** Public Blob folder the worker publishes to, without a trailing slash. */
export function snapshotBaseUrl(env = process.env) {
  const base = String(env.SNAPSHOT_BASE_URL || "").trim().replace(/\/+$/, "");
  if (!base) throw new ConfigError("SNAPSHOT_BASE_URL is not configured");
  if (!/^https?:\/\/\S+$/.test(base)) throw new ConfigError("SNAPSHOT_BASE_URL must be an http(s) URL");
  return base;
}

export function snapshotUrl(name, env = process.env) {
  if (!SNAPSHOT_FILES.has(name)) throw new ConfigError(`Unknown snapshot file ${name}`);
  return `${snapshotBaseUrl(env)}/${name}`;
}

/** Public SatRush API base, without a trailing slash. */
export function satrushApiUrl(env = process.env) {
  const explicit = String(env.SATRUSH_API_URL || "").trim().replace(/\/+$/, "");
  if (explicit) return explicit;
  return env.SATRUSH_NETWORK === "devnet" ? "https://api-devnet.satrush.io/api" : "https://api.satrush.io/api";
}

// ---------------------------------------------------------------------------
// Upstream reads

export function fetchUpstream(url, { accept = "application/json", timeoutMs = UPSTREAM_TIMEOUT_MS } = {}) {
  return fetch(url, {
    method: "GET",
    cache: "no-store",
    redirect: "follow",
    signal: AbortSignal.timeout(timeoutMs),
    headers: { accept, "user-agent": USER_AGENT },
  });
}

/** Parsed JSON snapshot (`state.json` or `audit.json`), memoized for a second. */
export function loadSnapshotJson(name, env = process.env) {
  const url = snapshotUrl(name, env);
  return memo(url, async () => {
    const response = await fetchUpstream(url);
    if (!response.ok) throw new UpstreamError(`${name}: upstream answered HTTP ${response.status}`, response.status);
    const text = await response.text();
    let doc;
    try {
      doc = JSON.parse(text);
    } catch {
      throw new UpstreamError(`${name} is not valid JSON`);
    }
    if (!doc || typeof doc !== "object" || Array.isArray(doc)) throw new UpstreamError(`${name} is not a JSON object`);
    return doc;
  });
}

/** Raw snapshot bytes (`sensory.png`), memoized for a second. */
export function loadSnapshotBytes(name, accept, env = process.env) {
  const url = snapshotUrl(name, env);
  return memo(url, async () => {
    const response = await fetchUpstream(url, { accept });
    if (!response.ok) throw new UpstreamError(`${name}: upstream answered HTTP ${response.status}`, response.status);
    return {
      bytes: Buffer.from(await response.arrayBuffer()),
      contentType: response.headers.get("content-type") || "",
      etag: response.headers.get("etag") || null,
      lastModified: response.headers.get("last-modified") || null,
    };
  });
}

/** One short line for the `error` field; never a stack trace. */
export function describeError(error) {
  if (!error) return "unknown error";
  if (error.name === "TimeoutError" || error.name === "AbortError") {
    return `upstream timed out after ${Math.round(UPSTREAM_TIMEOUT_MS / 1000)} s`;
  }
  // undici wraps network failures: the useful code sits on `cause`, or on the first of an AggregateError's `errors`.
  const inner = error.cause && Array.isArray(error.cause.errors) && error.cause.errors.length ? error.cause.errors[0] : error.cause;
  const cause = inner && (inner.code || inner.message) ? ` (${inner.code || inner.message})` : "";
  const text = `${error.name || "Error"}: ${error.message || ""}${cause}`.replace(/\s+/g, " ").trim();
  return text.length > 200 ? `${text.slice(0, 197)}...` : text;
}

// ---------------------------------------------------------------------------
// Responses (plain node:http API so the handlers also run outside Vercel)

export function sendJson(res, status, body, cache = status < 400 ? CACHE_CONTROL : NO_STORE) {
  const payload = Buffer.from(JSON.stringify(body));
  res.statusCode = status;
  res.setHeader("content-type", "application/json; charset=utf-8");
  res.setHeader("cache-control", cache);
  res.setHeader("content-length", String(payload.length));
  res.setHeader("x-content-type-options", "nosniff");
  res.end(payload);
}

export function sendBytes(res, status, bytes, contentType, extraHeaders = {}, cache = status < 400 ? CACHE_CONTROL : NO_STORE) {
  res.statusCode = status;
  res.setHeader("content-type", contentType);
  res.setHeader("cache-control", cache);
  res.setHeader("content-length", String(bytes.length));
  res.setHeader("x-content-type-options", "nosniff");
  for (const [key, value] of Object.entries(extraHeaders)) if (value) res.setHeader(key, value);
  res.end(bytes);
}

/** These endpoints are read-only: anything but GET/HEAD gets a 405. Returns false when the response was already sent. */
export function allowGet(req, res) {
  if (req.method === "GET" || req.method === "HEAD") return true;
  res.setHeader("allow", "GET, HEAD");
  sendJson(res, 405, { error: "method not allowed" });
  return false;
}
