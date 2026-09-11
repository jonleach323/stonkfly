// GET /api/config: where the page should read its data from.
//
// On Vercel the cheapest, freshest setup is to let browsers read the worker's
// own `stonkfly serve` endpoint directly (STONKFLY_ORIGIN_URL, behind your
// domain or Cloudflare tunnel). Then this deployment serves static files plus
// this one small answer per visitor, and the other functions only run as a
// fallback when that origin is unreachable. Cached for a minute.

import { allowGet, originUrl, sendJson } from "./_lib.js";

export default async function handler(req, res) {
  if (!allowGet(req, res)) return;
  let origin = null;
  try {
    origin = originUrl();
  } catch {
    origin = null;
  }
  const network = process.env.SATRUSH_NETWORK === "devnet" ? "devnet" : "mainnet";
  const source = origin ? "origin" : process.env.SNAPSHOT_BASE_URL ? "blob" : "unconfigured";
  sendJson(res, 200, { origin, network, source }, "public, max-age=60, s-maxage=60, stale-while-revalidate=300");
}
