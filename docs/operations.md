# Running and stopping Stonkfly

Use a dedicated wallet. Stonkfly is an experiment capable of losing its entire allocated balance, and the game's fees make that the expected outcome over time. The funding cap is **100 USDC at initialization**.

## Installation and data

Use Python 3.11 and a C++17 compiler (`clang++`/`c++` on macOS, GCC or Clang on Linux). `python -m stonkfly prepare` downloads about 1.1 GB of upstream data, verifies it, and builds the full graph. `python -m stonkfly verify` independently checks prepared inputs. Set `STONKFLY_DATA` to use another data location.

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

1. Create a new Solana keypair (`solana-keygen new -o satrush-keypair.json`, then `chmod 600`). Fund it with **at most 100 USDC** (mainnet mint `EPjF...Dt1v`) and about 0.02 SOL for fees. Do not use a wallet holding anything else.
2. Copy `.env.example` to `.env`, set `SATRUSH_KEYPAIR`, and set `STONKFLY_LIVE=I_ACCEPT_REAL_DEPLOYS`. On a hosted runner without a file system you control, set `SATRUSH_KEYPAIR_JSON` to the 64-byte array as an injected secret instead. The CLI also requires `--live`; paper mode never signs a transaction even if the variable is present.
3. Run `python -m stonkfly run --live --preflight-only`. This reads the on-chain config, your SOL and USDC balances and your miner account, and initializes the local ledger. **It does not deploy.** The preflight refuses wallets holding more than 100 USDC (including unclaimed game balance) or less than 0.005 SOL.
4. Once you have reviewed the output, run `python -m stonkfly run --live` yourself. `--stake` (1-10 USDC), `--loss-stop`, `--daily-deploys` and `--priority-fee` adjust the limits within their bounds.
5. `python -m stonkfly claim` moves settled USDC and sats shares from the game to the wallet. Claiming sats pays the vault's 10% exit fee. Winnings left unclaimed also fund later deploys.

Devnet (`--network devnet`) uses the game's devnet API, RPC and mints. Devnet USDC comes from the SatRush team's mint authority, not a public faucet.

## Execution guarantees and limits

- Maximum initial funding: 100 USDC. Stake per round: 1-10 USDC, fixed for the run. At most 1,440 deploys per UTC day (default 300) and one deploy per round. No automation escrow, vault tickets, leverage or transfers are exposed.
- A round is only played when it is active, not pending activation, and at least 40 slots (about 13 s) remain. The board is re-read after neural integration; a changed or closing round vetoes the deploy.
- At 20 USDC drawdown from starting equity (wallet USDC plus unclaimed USDC plus the value of unclaimed sats), **stop new deploys**. A round already deployed still settles.
- The ledger records the signed transaction signature before sending. An unconfirmed submission halts the worker; on restart, `reconcile` checks the signature and the deployment account before anything else. A rejected or expired transaction frees the round. A still-unknown outcome older than five minutes with no on-chain deployment is treated as failed; anything else stays halted for review.
- The local process lock prevents two workers using one run directory. It does not coordinate multiple machines or copied ledgers.

## State, recovery and privacy

`runs/<name>/` holds a SQLite ledger, two alternating checkpoints, `events.jsonl`, `latest.json`, `latest-input.png`, and provenance with exact code, graph, readout-cell and parameter hashes. Each deploy intent binds to the preceding neural observation and checkpoint.

To stop: Ctrl-C, or `touch runs/live/STOP` (`runs/paper/STOP` for paper). An already sent transaction may still land; the next start reconciles it.

For an ordinary clean restart, use the same command and run directory. After reviewing a transient failure, remove the STOP file if appropriate and pass `--resume-reviewed`. This cannot clear a loss stop, bypass an unresolved transaction, or accept changed source/configuration. Source changes require a fresh run directory or an explicitly reviewed migration.

Runtime state, balances, wallet addresses, `.env` and keypair filenames are git-ignored. Keep custom key paths outside the repository. Tests use in-memory doubles and never sign or send real transactions.
