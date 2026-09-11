# Watch site

`site/` is a static page plus four read-only endpoints. It shows what the worker
publishes; it cannot deploy, claim, or change anything. The data contract is
[`site/STATE.md`](../site/STATE.md).

| Endpoint | Source on Vercel | Source locally |
| --- | --- | --- |
| `/api/state` | `SNAPSHOT_BASE_URL/state.json` | computed from the run directory |
| `/api/audit` | `SNAPSHOT_BASE_URL/audit.json` | computed from the run directory |
| `/api/sensory.png` | `SNAPSHOT_BASE_URL/sensory.png` | `<run>/latest-input.png` |
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
| `SNAPSHOT_BASE_URL` | Public Blob folder the worker publishes to, e.g. `https://<store>.public.blob.vercel-storage.com/stonkfly`. Without it `/api/state` and `/api/audit` answer `{"ready": false, "error": ...}` and `/api/sensory.png` answers 404. |
| `SATRUSH_NETWORK` | `mainnet` (default) or `devnet`; selects the public API the board proxy reads. |
| `SATRUSH_API_URL` | Optional explicit API base; overrides `SATRUSH_NETWORK`. |

Behaviour of the functions:

- Each upstream document is fetched with `cache: "no-store"` and memoized for
  one second per function instance, so many viewers share one read.
- Responses carry `cache-control: public, max-age=1, s-maxage=1,
  stale-while-revalidate=5`; `vercel.json` sets the same header on `/api/*` so
  the CDN never serves anything older than a second or so.
- `/api/state` adds `publication.served_at` (Unix seconds) to the snapshot.
- Hosting failures are not worker failures: JSON endpoints answer 200 with
  `ready: false` and an `error` string; the board proxy answers 502 with
  `{"live": false, "error": ...}`. The page shows "waiting" or "disconnected"
  accordingly and never invents numbers.
- The board proxy sends the user agent
  `stonkfly-watch/0.2 (+https://github.com/jonleach323/stonkfly)`; the game's
  edge blocks default agents.

Tests for the board parser and proxy: `node --test site/api/board.test.js`
(or `npm test` inside `site/`). They use `tests/fixtures/board.json` and a stub
upstream; no network.

## How the worker publishes

```sh
python -m stonkfly run --publish            # paper play, snapshots to Vercel Blob
python -m stonkfly run --live --publish
```

`--publish` needs `BLOB_READ_WRITE_TOKEN` (a `vercel_blob_rw_...` token from the
Blob store, in `.env`) and optionally `STONKFLY_PUBLISH_PREFIX` (default
`stonkfly`). After every observation and settlement the worker builds
`state.json`, `audit.json` and `sensory.png` (`stonkfly.publish.write_files`)
and `stonkfly.publish.BlobPublisher` uploads them to the public store under
fixed pathnames `<prefix>/state.json` etc., overwriting in place, with a
5-second cache lifetime at the Blob edge. The first upload's URL minus the
file name is the value for `SNAPSHOT_BASE_URL`.

Without `--publish` the same three files are written to `<run>/site/` for
static hosting or inspection. Upload failures are recorded in
`state.publication.publish_error`; they never stop the worker.

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
