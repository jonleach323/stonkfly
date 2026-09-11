// GET /api/sensory.png: the exact 320x180 frame the retina last received,
// as published by the worker. 404 when there is no frame or no snapshot.

import { allowGet, loadSnapshotBytes, sendBytes, sendJson } from "./_lib.js";

const PNG_MAGIC = Buffer.from([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a]);

export default async function handler(req, res) {
  if (!allowGet(req, res)) return;
  let frame;
  try {
    frame = await loadSnapshotBytes("sensory.png", "image/png");
  } catch {
    return sendJson(res, 404, { error: "no frame published" });
  }
  if (frame.bytes.length < PNG_MAGIC.length || !frame.bytes.subarray(0, PNG_MAGIC.length).equals(PNG_MAGIC)) {
    return sendJson(res, 404, { error: "published frame is not a PNG" });
  }
  if (frame.etag && req.headers["if-none-match"] === frame.etag) {
    res.statusCode = 304;
    res.setHeader("etag", frame.etag);
    res.setHeader("cache-control", "public, max-age=1, s-maxage=1, stale-while-revalidate=5");
    return res.end();
  }
  sendBytes(res, 200, frame.bytes, "image/png", { etag: frame.etag, "last-modified": frame.lastModified });
}
