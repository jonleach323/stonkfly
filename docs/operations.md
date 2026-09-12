# Running and stopping Sat Rush Fly

Use a dedicated wallet. Sat Rush Fly is an experiment capable of losing its entire allocated balance, and the game's fees make that the expected outcome over time. The funding cap is **100 USDC at initialization**.

## Installation and data

Use Python 3.11 and a C++17 compiler (`clang++`/`c++` on macOS, GCC or Clang on Linux). `python -m stonkfly prepare` downloads about 1.1 GB of upstream data, verifies it, and builds the full graph (about 2.5 minutes and 1.7 GB of RAM at peak; the running brain then uses about 1 GB and one core). `python -m stonkfly verify` independently checks prepared inputs. Set `STONKFLY_DATA` to use another data location.

Existing DOOMFLY researchers can reuse verified local files with `python -m stonkfly prepare --reuse-doomfly /path/to/working-copy`.

## Paper modes

```sh
# Real public board and real round results; simulated deploys.
python -m stonkfly run --steps 10

# Synthetic offline rounds and a seeded random "brain": plumbing check only.
python -m stonkfly run --fixture --stub-brain --steps 10 --out runs/fixture

# Frozen-memory control, always in a separate run directory.
python -m stonkfly run --frozen --steps 10 --out runs/frozen
```

`--fixture` never claims real game data. `--stub-brain` is seeded noise, never neural output; both are refused with `--live`. Each observation advances the configured neural time and then waits for the current round to end.

## Wallet setup, performed by you

1. Create a new Solana keypair: `python -m stonkfly keygen` (or `solana-keygen new -o satrush-keypair.json` if you have the Solana CLI). It prints the public address only. Fund that address with **at most 100 USDC** (mainnet mint `EPjF...Dt1v`) and about 0.02 SOL for fees. Do not use a wallet holding anything else. With Docker: `docker compose run --rm worker keygen --out /runs/satrush-keypair.json`, show it once with `docker compose run --rm worker cat /runs/satrush-keypair.json`, copy that array into `SATRUSH_KEYPAIR_JSON` in `.env`, and remove the file with `docker compose run --rm worker sh -c 'rm /runs/satrush-keypair.json'` if you keep the array elsewhere.
2. Copy `.env.example` to `.env`, set `SATRUSH_KEYPAIR`, and set `STONKFLY_LIVE=I_ACCEPT_REAL_DEPLOYS`. On a hosted runner without a file system you control, set `SATRUSH_KEYPAIR_JSON` to the 64-byte array as an injected secret instead. The CLI also requires `--live`; paper mode never signs a transaction even if the variable is present.
3. Run `python -m stonkfly run --live --preflight-only`. This reads the on-chain config, your SOL and USDC balances and your miner account, and initializes the local ledger. **It does not deploy.** The preflight refuses wallets holding more than 100 USDC (including unclaimed game balance) or less than 0.005 SOL.
4. Once you have reviewed the output, run `python -m stonkfly run --live` yourself. `--stake` (1-10 USDC), `--loss-stop`, `--daily-deploys` and `--priority-fee` adjust the limits within their bounds.
5. `python -m stonkfly claim` moves settled USDC and sats shares from the game to the wallet. Claiming sats pays the vault's 10% exit fee. Winnings left unclaimed also fund later deploys.

If you run your own Solana RPC node, set `SATRUSH_RPC_URL` to it (from Docker on the same host: `http://host.docker.internal:8899`). The worker sends the deploy, polls `getSignatureStatuses` and reads a handful of accounts through it; the SatRush API is still used for the board and settled results. The node must be on mainnet-beta and answer `getSignatureStatuses` with `searchTransactionHistory` for signatures a few minutes old.

Devnet (`--network devnet`) uses the game's devnet API, RPC and mints. Devnet USDC comes from the SatRush team's mint authority, not a public faucet.

## Execution guarantees and limits

- Maximum initial funding: 100 USDC. Stake per round: 1-10 USDC, fixed for the run. At most 1,440 deploys per UTC day (default 300) and one deploy per round. No automation escrow, vault tickets, leverage or transfers are exposed.
- A round is only played when it is active, not pending activation, and at least 40 slots (about 13 s) remain. The board is re-read after neural integration; a changed or closing round vetoes the deploy.
- At 20 USDC drawdown from starting equity (wallet USDC plus unclaimed USDC plus the value of unclaimed sats), **stop new deploys**. A round already deployed still settles.
- The ledger records the signed transaction signature before sending. An unconfirmed submission halts the worker; on restart, `reconcile` checks the signature and the deployment account before anything else. A rejected or expired transaction frees the round. A still-unknown outcome older than five minutes with no on-chain deployment is treated as failed; anything else stays halted for review.
- The local process lock prevents two workers using one run directory. It does not coordinate multiple machines or copied ledgers.

## Run on a server

The repository ships a Docker image that plays and serves the watch page from one run directory.

```sh
git clone https://github.com/jonleach323/stonkfly && cd stonkfly
cp .env.example .env        # STONKFLY_MODE=paper needs nothing else
docker compose up -d        # builds the image, downloads and builds the dataset on first start
docker compose logs -f worker
```

The page is at `http://127.0.0.1:8787/` on the host. Set `STONKFLY_WATCH_BIND=0.0.0.0` to expose it, or set `WATCH_DOMAIN` and run `docker compose --profile https up -d` for automatic HTTPS through Caddy.

For live play, put the dedicated wallet's 64-byte array in `SATRUSH_KEYPAIR_JSON`, set `STONKFLY_MODE=live` and `STONKFLY_LIVE=I_ACCEPT_REAL_DEPLOYS`, then `docker compose up -d --force-recreate worker`. Run `docker compose run --rm worker run --live --preflight-only --network mainnet --out /runs/live` first to check balances without deploying.

### Behind Cloudflare on a shared server

If the server already serves other subdomains, do not use the `https` profile (it would take ports 80 and 443). Leave `STONKFLY_WATCH_BIND=127.0.0.1` and point your existing front door at `127.0.0.1:8787`:

- **Cloudflare Tunnel (cloudflared):** add the ingress rule from `deploy/cloudflared-ingress.example.yml` for the new hostname to your tunnel's `config.yml`, then add the DNS route: `cloudflared tunnel route dns <tunnel> fly.example.com` and restart cloudflared.
- **Proxied DNS + your own reverse proxy:** add an A/AAAA record for the subdomain in Cloudflare (proxied) and a server block like `deploy/nginx-watch.conf` (or the Caddy equivalent, `fly.example.com { reverse_proxy 127.0.0.1:8787 }`) that proxies to `127.0.0.1:8787`.

Cloudflare's default cache does not store HTML or `/api/*` (the functions send `no-store` or `max-age=1`), and the page's own files are sent with `no-store` so a rebuilt page is picked up on the next load without a purge. The page polls every two seconds; that is a few requests per viewer per second at most, well within the free plan.

### Updating

```sh
git pull
docker compose build                    # one image for both services; `build watch` reports nothing to build
docker compose up -d --force-recreate   # the worker rebuilds nothing: the dataset volume persists
```

Until the worker is back (it reloads the graph for a minute or two), `/api/state` reports `ready: false` and the page shows the boot screen. If a browser still shows the old page after that, purge the hostname once in the Cloudflare dashboard (Caching → Configuration → Purge Everything) and reload: builds before this note let Cloudflare cache the scripts for four hours.

The dataset lives in the `data` volume (built once, about 1.6 GB) and run state in the `runs` volume; `docker compose down` keeps both, `docker compose down -v` deletes them. A halt (loss stop, unknown deploy outcome) exits the worker cleanly and it stays down until you review; see recovery below. Outages of the SatRush API or the RPC are not halts: the worker backs off (5 s doubling to 60 s), reports `retrying after …` as its phase on the watch page, and resumes on its own. `deploy/stonkfly-*.service` are systemd units for a bare-metal install with the same entrypoint.

## Watching a run

`python -m stonkfly serve --out runs/paper` serves the watch page from the run directory and proxies the public board; it never writes to the run. `run --publish` uploads the same documents to Vercel Blob for the hosted page. Details in [site.md](site.md).

## State, recovery and privacy

`runs/<name>/` holds a SQLite ledger, two alternating checkpoints, `events.jsonl`, `latest.json`, `latest-input.png`, and provenance with exact code, graph, readout-cell and parameter hashes. Each deploy intent binds to the preceding neural observation and checkpoint.

To stop: Ctrl-C, or `touch runs/live/STOP` (`runs/paper/STOP` for paper). An already sent transaction may still land; the next start reconciles it.

For an ordinary clean restart, use the same command and run directory. After reviewing a transient failure, remove the STOP file if appropriate and pass `--resume-reviewed`. This cannot clear a loss stop or bypass an unresolved transaction.

Updating the code does not stop a run: the worker logs `source_changed` with the files that differ and appends them to the ledger's `source_history`, so the audit still shows which code played which rounds. What a run cannot resume with is a different protocol: settings (stake, network, neural timing), dataset, readout cells, circuit rule, mode or feed. Then it stops with "Run source/protocol changed"; start a fresh run directory (in Docker, `docker compose run --rm worker sh -c 'mv /runs/paper /runs/paper-$(date +%s)'` then `docker compose up -d worker`) or change the setting back.

Runtime state, balances, wallet addresses, `.env` and keypair filenames are git-ignored. Keep custom key paths outside the repository. Tests use in-memory doubles and never sign or send real transactions.
