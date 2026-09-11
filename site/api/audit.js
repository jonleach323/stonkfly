// GET /api/audit: every deploy intent the worker published (`audit.json`),
// passed through unchanged. Signatures and hashes support traceability;
// they are not proof of skill and never include keys.

import { allowGet, describeError, hostingProblem, loadSnapshotJson, logError, sendJson } from "./_lib.js";

export default async function handler(req, res) {
  if (!allowGet(req, res)) return;
  try {
    sendJson(res, 200, await loadSnapshotJson("audit.json"));
  } catch (error) {
    logError("audit", error);
    sendJson(res, 200, { ready: false, error: describeError(error), hosting: hostingProblem(error) });
  }
}
