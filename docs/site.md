# Watch site

`site/` is a static page plus a few read-only endpoints. It shows what the worker
records; it cannot deploy, claim, or change anything. The data contract is
[`site/STATE.md`](../site/STATE.md).

## The neural replay

The watch page draws the brain as a point cloud that lights up with the spikes of each observation. The worker chooses a fixed subsample of about 16,000 of the 166,700 cells once per run (every readout descending neuron plus a seeded uniform sample of every other cell with an annotated soma, so the brain, its optic lobes and the nerve cord keep their shape) and writes `atlas.bin` / `atlas.json` with their soma positions and classes; photoreceptors have no soma in the dataset and are not drawn. Each observation writes `latest-activity.bin`, the subsample's spikes in ten equal slices of the neural time the observation used; the page shows an activity map (brightness per cell from its spikes over the observation) and plays the slices back once, at real time, when an observation arrives, with a spike raster of the 21 readout groups. Formats are in [`STATE.md`](../site/STATE.md). It is a picture of an approximate model's activity, not a recording of a fly, and nothing in it feeds back into the game.

## Everything on one server (default)

`docker compose up -d` runs the worker and the page together; the `watch`
container serves `site/` and the endpoints from the run directory on
`127.0.0.1:8787`. Put your subdomain in front of that port (Cloudflare Tunnel
ingress or a reverse-proxy block, snippets in `deploy/`) and you are done. No
Vercel, no Blob, nothing else to configure; the page is never more than one
round behind the worker.

## Optional: the page on Vercel instead

| Piece | Where it runs | Why |
| --- | --- | --- |
| Worker (the brain, the ledger, `stonkfly serve`) | your server (Docker Compose, see [operations](operations.md)) | a long-running process with a 1.6 GB dataset and 1 GB of live state; not a serverless workload |
| Page (`site/`) | the same server (default), or Vercel | static files; on Vercel a few tiny functions |

### Vercel page, your server as origin

1. Run the stack on your server and expose the watch endpoint on a hostname,
   for example `https://fly-origin.example.com` → `127.0.0.1:8787` (Cloudflare
   Tunnel or your reverse proxy; snippets in `deploy/`). It serves `/api/state`,
   `/api/board`, `/api/audit`, `/api/sensory.png` with CORS enabled and no
   caching, straight from the run directory.
2. Deploy the page: `vercel --cwd site --prod`, with one environment variable
   on the Vercel project:

   | Variable | Value |
   | --- | --- |
   | `STONKFLY_ORIGIN_URL` | `https://fly-origin.example.com` |
   | `SATRUSH_NETWORK` | `mainnet` (default) or `devnet` |

3. Put your public hostname on the Vercel project as usual.

How it behaves: each visitor loads the static page and calls `/api/config` once
(cached a minute). The browser then polls your origin directly every 2 s, so
Vercel serves only static files and the data is never older than the worker's
last round. If the origin stops answering three times in a row, the page falls
back to this deployment's `/api/*` functions, which proxy the same origin with
a one-second memo, so the page keeps working through a tunnel restart.

Cost: with the origin configured, function invocations are about one per
visit. Without it (functions proxying every poll) a single always-open tab
costs about 85,000 invocations a day, which exceeds Vercel's Hobby allowance
within a couple of weeks.

### Or publish snapshots to Vercel Blob

For a worker that must not be reachable from the internet: run it with
`--publish` and `BLOB_READ_WRITE_TOKEN` (optional `STONKFLY_PUBLISH_PREFIX`,
default `stonkfly`). After each round it uploads `state.json`, `audit.json`
and the frame as `frames/<sha256>.png` to a public Blob folder and prints the
folder URL once. Set `SNAPSHOT_BASE_URL` to that URL on the Vercel project and
leave `STONKFLY_ORIGIN_URL` unset. Blob's edge cache has a 60 s minimum, so the
page can lag a round behind, and every poll is a function invocation.

## Local

```sh
python -m stonkfly serve --out runs/paper          # http://127.0.0.1:8787/
```

The local server hosts `site/` and computes the same JSON from the run
directory on request; the board proxy makes at most one upstream call per
second. Node tests for the functions: `cd site && node --test`.

## What is public

The page shows the wallet address, transaction signatures and round results,
all of which are public on chain anyway. Keys, the ledger file and checkpoints
never leave the worker. The endpoints are read-only and answer only GET.
