![Sat Rush Fly: a pixel fly beside a candlestick chart](assets/stonkfly.png)

# Sat Rush Fly

A fly-connectome simulation that plays [SatRush](https://satrush.io), an on-chain tile game on Solana. Actual neural output, actual program transactions. Winning has not been demonstrated and, by the game's fee structure, is not expected.

**How it works:** The public SatRush board (21 tiles, the stake on each, recent winning tiles, pot, countdown) is rendered as a 320×180 RGB image every round. It stimulates 3,335 brightness inputs and 811 R8 color inputs in the retained **MaleCNS v1.0 graph: 166,700 neurons, 25.6 million connections**. A fixed readout over 21 pre-registered groups of descending neurons proposes which tiles to stake. A guard checks limits and sends one `DeployPublic` transaction from a dedicated wallet.

When a round settles, a hit (the winning tile among the picks) stimulates 15 identified PAM11 dopamine cells; a miss stimulates two PPL101 aversive dopamine cells. A candidate memory rule changes existing KC-to-MBON connections. These are engineered reinforcement signals, **not modeled pain receptors**. Synaptic changes do not establish that it learns to play well. [Model and evidence](docs/model.md). [Game rules as reconstructed](docs/game.md).

## Run it

Python 3.11, a C++17 compiler, macOS/Linux. Measured needs: 1.7 GB RAM peak to build the graph, 1 GB while running, 1.6 GB of disk for the dataset, one CPU core (2 to 5 s per round on a 2.8 GHz core). 4 GB RAM and 5 GB free disk are comfortable.

```sh
python3.11 -m venv .venv
source .venv/bin/activate
pip install -e '.[test]'
python -m stonkfly prepare
python -m stonkfly run
```

Default: **paper play against the real public board, $100 simulated balance, $1 per round**. No wallet needed. Outcomes are simulated from each finished round's real winning tile and pot. Local logs, sensory images and resumable brain state go in `runs/paper/`. Ctrl-C stops it; the same command resumes.

For real deploys, create a **dedicated Solana wallet holding only what you are willing to lose and a little SOL**. Copy `.env.example` to `.env`, point it at the keypair file, then run these commands yourself:

```sh
python -m stonkfly run --live --preflight-only
python -m stonkfly run --live
python -m stonkfly claim   # move settled USDC and sats to the wallet
```

Defaults: $1 per round (program minimum; at most $10), 300 rounds per UTC day, one deploy per round. A $20 drawdown stops new deploys; **it does not cancel a round in progress or cap the game's fees**. Every round costs about 6% in fees plus an 11% loss on each missed tile, so the balance is expected to decline. [Operation and recovery](docs/operations.md).

```sh
python -m stonkfly status
python -m stonkfly serve          # watch page at http://127.0.0.1:8787/
python -m pytest -q
python -m stonkfly run --fixture --stub-brain --steps 5 --out runs/smoke   # no data, no network
```

**On a server:** `cp .env.example .env && docker compose up -d` builds everything, plays in paper mode, and serves the watch page on port 8787. [Server notes](docs/operations.md#run-on-a-server).

**Watch it:** `serve` hosts a read-only page next to any run directory: the fly at its terminal, the board it just saw, the tiles it chose, each round's result, and the neural counts behind it. The same page deploys to Vercel and reads snapshots the worker publishes. [Site notes](docs/site.md).

The repo does not come funded or connected to anyone's wallet. Live execution needs your local keypair and explicit opt-in. Sat Rush Fly is not affiliated with SatRush.
