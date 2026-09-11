// GET /api/state: the snapshot the worker published (`state.json`), served
// with a `publication.served_at` stamp so the page can judge freshness.
//
// The page never gets write access to anything; this function only reads
// the public Blob folder named by SNAPSHOT_BASE_URL.

import { allowGet, describeError, loadSnapshotJson, nowSeconds, sendJson } from "./_lib.js";

const SNAPSHOT_VERSION = 1;

export default async function handler(req, res) {
  if (!allowGet(req, res)) return;
  const served_at = nowSeconds();
  try {
    const state = await loadSnapshotJson("state.json");
    const publication = state.publication && typeof state.publication === "object" ? state.publication : {};
    sendJson(res, 200, { ...state, publication: { ...publication, served_at } });
  } catch (error) {
    // Hosting problems are not worker problems: answer 200 so the page can say "waiting" rather than "broken".
    sendJson(res, 200, {
      ready: false,
      version: SNAPSHOT_VERSION,
      published_at: null,
      error: describeError(error),
      publication: { served_at },
    });
  }
}
