// Shared handler for the content-addressed binaries next to the snapshot:
// the worker uploads `<dir>/<sha256>.<ext>` and names the current one in
// `state.publication.<key>`. `?v=` may be the digest or a prefix; when it
// names what is served, the response is immutable.

import { createHash } from "node:crypto";

import {
  allowGet, CACHE_CONTROL, describeError, FRAME_SHA, loadSnapshotBytes, loadSnapshotJson, logError, originUrl,
  sendBytes, sendJson, sendNotModified, snapshotAddressedUrl, UpstreamError,
} from "./_lib.js";

const IMMUTABLE = "public, max-age=31536000, immutable";

function requestedVersion(req) {
  try {
    const v = new URL(req.url || "/", "http://localhost").searchParams.get("v");
    return v && /^[0-9a-f]{1,64}$/.test(v) ? v : null;
  } catch {
    return null;
  }
}

export function addressedHandler({ key, dir, ext, live, contentType, magic }) {
  return async function handler(req, res) {
    if (!allowGet(req, res)) return;
    const wanted = requestedVersion(req);
    let sha = FRAME_SHA.test(wanted || "") ? wanted : null;
    let file;
    try {
      if (!sha) {
        const state = await loadSnapshotJson("state.json");
        const published = state.publication && typeof state.publication === "object" ? state.publication[key] : null;
        if (!FRAME_SHA.test(String(published || ""))) return sendJson(res, 404, { error: `no ${dir} published` });
        sha = published;
      }
      file = await loadSnapshotBytes(snapshotAddressedUrl(dir, sha, ext, live), contentType);
    } catch (error) {
      logError(dir, error);
      if (error instanceof UpstreamError && error.status === 404) return sendJson(res, 404, { error: `no ${dir} published` });
      return sendJson(res, 503, { error: describeError(error) });
    }
    if (magic && (file.bytes.length < magic.length || !file.bytes.subarray(0, magic.length).equals(magic))) {
      return sendJson(res, 503, { error: `published ${dir} file is not what it should be` });
    }
    const actual = createHash("sha256").update(file.bytes).digest("hex");
    let isLive = false;
    try { isLive = !!originUrl(); } catch { isLive = false; }
    if (actual !== sha && !isLive) return sendJson(res, 503, { error: `published ${dir} file does not match its hash` });
    sha = actual;
    const etag = `"${sha}"`;
    const cache = wanted && sha.startsWith(wanted) ? IMMUTABLE : CACHE_CONTROL;
    if (req.headers["if-none-match"] === etag) return sendNotModified(res, etag, cache);
    sendBytes(res, 200, file.bytes, contentType, { etag, "last-modified": file.lastModified }, cache);
  };
}
