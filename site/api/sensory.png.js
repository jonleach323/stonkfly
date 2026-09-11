// GET /api/sensory.png: the exact 320x180 frame the retina last received.
//
// The worker publishes each frame content-addressed as `frames/<sha256>.png`
// and names the current one in `state.publication.frame_sha256`, so the bytes
// served here always carry the SHA the page prints next to them. `?v=` may be
// the full digest (served directly) or a prefix of it; when it names the frame
// that is served the response is immutable, otherwise it is cached for a
// second like the snapshot. 404 when no frame is published, 503 when the
// hosting layer cannot read it.

import { createHash } from "node:crypto";

import {
  allowGet, CACHE_CONTROL, describeError, FRAME_SHA, loadSnapshotBytes, loadSnapshotJson, logError, originUrl,
  sendBytes, sendJson, sendNotModified, snapshotFrameUrl, UpstreamError,
} from "./_lib.js";

const PNG_MAGIC = Buffer.from([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a]);
const IMMUTABLE = "public, max-age=31536000, immutable";

function requestedVersion(req) {
  try {
    const v = new URL(req.url || "/", "http://localhost").searchParams.get("v");
    return v && /^[0-9a-f]{1,64}$/.test(v) ? v : null;
  } catch {
    return null;
  }
}

export default async function handler(req, res) {
  if (!allowGet(req, res)) return;
  const wanted = requestedVersion(req);
  let sha = FRAME_SHA.test(wanted || "") ? wanted : null;
  let frame;
  try {
    if (!sha) {
      const state = await loadSnapshotJson("state.json");
      const published = state.publication && typeof state.publication === "object" ? state.publication.frame_sha256 : null;
      if (!FRAME_SHA.test(String(published || ""))) return sendJson(res, 404, { error: "no frame published" });
      sha = published;
    }
    frame = await loadSnapshotBytes(snapshotFrameUrl(sha), "image/png");
  } catch (error) {
    logError("sensory", error);
    if (error instanceof UpstreamError && error.status === 404) return sendJson(res, 404, { error: "no frame published" });
    return sendJson(res, 503, { error: describeError(error) });
  }
  if (frame.bytes.length < PNG_MAGIC.length || !frame.bytes.subarray(0, PNG_MAGIC.length).equals(PNG_MAGIC)) {
    return sendJson(res, 503, { error: "published frame is not a PNG" });
  }
  const actual = createHash("sha256").update(frame.bytes).digest("hex");
  let live = false;
  try { live = !!originUrl(); } catch { live = false; }
  if (actual !== sha && !live) {
    return sendJson(res, 503, { error: "published frame does not match its hash" });
  }
  // From a live origin the frame may already be newer than the snapshot that named it: serve what it is.
  sha = actual;
  const etag = `"${sha}"`;
  const cache = wanted && sha.startsWith(wanted) ? IMMUTABLE : CACHE_CONTROL;
  if (req.headers["if-none-match"] === etag) return sendNotModified(res, etag, cache);
  sendBytes(res, 200, frame.bytes, "image/png", { etag, "last-modified": frame.lastModified }, cache);
}

