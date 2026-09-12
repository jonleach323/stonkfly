// GET /api/atlas.json: the atlas metadata (counts per class, layout notes).
import { allowGet, describeError, loadSnapshotBytes, logError, originUrl, sendBytes, sendJson, snapshotBaseUrl } from "./_lib.js";

export default async function handler(req, res) {
  if (!allowGet(req, res)) return;
  let file;
  try {
    const origin = originUrl();
    file = await loadSnapshotBytes(origin ? `${origin}/api/atlas.json` : `${snapshotBaseUrl()}/atlas.json`, "application/json");
  } catch (error) {
    logError("atlas", error);
    return sendJson(res, 503, { error: describeError(error) });
  }
  sendBytes(res, 200, file.bytes, "application/json; charset=utf-8", { "last-modified": file.lastModified });
}
