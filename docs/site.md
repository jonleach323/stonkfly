# Watch site

`site/` is a static page plus four read-only endpoints. It shows what the worker
publishes; it cannot deploy, claim, or change anything. The data contract is
[`site/STATE.md`](../site/STATE.md).

| Endpoint | Source on Vercel | Source locally |
| --- | --- | --- |
| `/api/state` | `SNAPSHOT_BASE_URL/state.json` | computed from the run directory |
| `/api/audit` | `SNAPSHOT_BASE_URL/audit.json` | computed from the run directory |
| `/api/sensory.png` | `SNAPSHOT_BASE_URL/frames/<sha256>.png`, the frame `state.publication.frame_sha256` names | `<run>/latest-input.png` |
| `/api/board` | public SatRush API, proxied | public SatRush API, proxied |

## Run locally

```sh
python -m stonkfly serve --out runs/paper          # http://127.0.0.1:8787/
python -m stonkfly serve --out runs/fixture --port 8800 --network devnet
```

`serve` hosts `site/` and computes the JSON from the run directory on request,
so the page works on a laptop with no hosting account. It can run next to a
worker that is writing the same directory. The board proxy makes at most one
upstream call per second.

## Deploy

The hosted version is Vercel serverless functions in `site/api/` plus the
static files. No framework, no build step, no dependencies (`site/package.json`
is only there to declare ES modules and the Node version).

```sh
vercel --cwd site            # preview
vercel --cwd site --prod
```

Environment variables for the Vercel project:

| Variable | Meaning |
| --- | --- |
| `SNAPSHOT_BASE_URL` | Public Blob folder the worker publishes to, e.g. `https://<store>.public.blob.vercel-storage.com/stonkfly`; `run --publish` prints it after its first upload. Without it `/api/state` and `/api/audit` answer `{"ready": false, "hosting": "unconfigured", "error": "hosting not configured"}` and `/api/sensory.png` answers 503. |
| `SATRUSH_NETWORK` | `mainnet` (default) or `devnet`; selects the public API the board proxy reads. |
| `SATRUSH_API_URL` | Optional explicit API base; overrides `SATRUSH_NETWORK`. |

Behaviour of the functions:

- Each upstream document is fetched with `cache: "no-store"` and memoized for
  one second per function instance, so many viewers share one read. That does
  not bypass the Blob edge cache: `state.json` and `audit.json` are overwritten
  in place and Vercel Blob caches an overwritten blob for at least 60 seconds
  (its documented minimum), so the hosted page can lag the worker by up to a
  minute. `publication.served_at - published_at` is that lag.
- Successful responses carry `cache-control: public, max-age=1, s-maxage=1,
  stale-while-revalidate=5` (cacheable for a second, served up to ~6 s stale
  while revalidating); errors are `no-store`. The functions are the only source
  of that header; `vercel.json` adds `X-Content-Type-Options` only.
- `/api/state` adds `publication.served_at` (Unix seconds) to the snapshot.
- `/api/sensory.png` reads `state.publication.frame_sha256`, fetches
  `frames/<sha>.png`, verifies the digest and answers with `etag: "<sha>"`.
  With `?v=<sha or prefix>` naming the served frame the response is immutable
  (`max-age=31536000`); the frame on the page therefore always matches the SHA
  printed next to it.
- Hosting failures are not worker failures: JSON endpoints answer 200 with
  `ready: false`, an `error` phrase and `hosting`: `"unconfigured"` (no
  `SNAPSHOT_BASE_URL`), `"unpublished"` (the store has no snapshot yet) or
  `"unreachable"`; `/api/sensory.png` answers 404 when no frame is published
  and 503 otherwise; the board proxy answers 502 with
  `{"live": false, "error": ...}`. Public `error` text is fixed phrases; the
  underlying exception goes to the function log. The page shows "waiting" or
  "disconnected" accordingly and never invents numbers.
- The board proxy sends the user agent
  `stonkfly-watch/0.2 (+https://github.com/jonleach323/stonkfly)`; the game's
  edge blocks default agents.

Tests for the functions: `node --test` inside `site/` (or `npm test`) runs
`api/board.test.js` (parser parity with `tests/fixtures/board.json`, proxy
against a stub upstream) and `api/snapshot.test.js` (snapshot pass-through,
hosting problems, the content-addressed frame). No network.

## How the worker publishes

```sh
python -m stonkfly run --publish            # paper play, snapshots to Vercel Blob
python -m stonkfly run --live --publish
```

`--publish` needs `BLOB_READ_WRITE_TOKEN` (a `vercel_blob_rw_...` token from the
Blob store, in `.env`) and optionally `STONKFLY_PUBLISH_PREFIX` (default
`stonkfly`). After every observation (and once more when the worker stops) it
builds `state.json`, `audit.json` and `sensory.png`
(`stonkfly.publish.write_files`) and `stonkfly.publish.BlobPublisher` uploads
what changed: `<prefix>/state.json` and `<prefix>/audit.json` are overwritten
in place with the 60-second cache lifetime that is Vercel Blob's minimum, and
the frame goes to `<prefix>/frames/<sha256>.png`, immutable. After the first
upload the worker prints `{"publish": {"base_url": ...}}`; that is the value
for `SNAPSHOT_BASE_URL`. A settlement that arrives on a step without an
observation is published with the next observation.

Every `put()` is a Blob "advanced operation": up to three per observation,
about 4,000 a day at one round per minute. The Hobby plan includes 2,000 a
month and then blocks the store for 30 days, so `--publish` needs a Pro store
(10,000 included, then $5 per million, i.e. well under a dollar a month).
`audit.json` lists the 1,000 most recent deploy intents (`deployment_count`
and `truncated` say when older rows exist only in the ledger) so that the
document re-uploaded on every change stays small.

Without `--publish` the same three files are written to `<run>/site/` for
static hosting or inspection. Upload failures are recorded in
`state.publication.publish_error` (the exception class; the status and API
body go to the worker's log as `{"publish_error": ...}`) and retried three
times on 429/5xx or network errors; they never stop the worker.

## What is public and what is not

Public, by design (every deploy is public on chain anyway):

- the wallet address and its explorer link, transaction signatures, tiles,
  stakes and settled outcomes of every deploy intent;
- the neural summary of each observation (rates, spike counts, changed edges),
  the sensory frame, SHA-256 hashes of the input, the spikes, the provenance
  and the audit document;
- the engineered settings (stake, limits, timing) and the model description.

Never published and never readable by the site:

- the keypair (`SATRUSH_KEYPAIR`, `SATRUSH_KEYPAIR_JSON`) and the Blob token;
- the ledger (`ledger.sqlite`), the event log, brain state and the run
  directory itself. The page only reads snapshots the worker chose to upload.

One worker owns the ledger; the functions are stateless readers. Paper results
are simulated settlements of real rounds and are labelled as such; nothing on
the page is a claim of profit or of learning.
