# SatRush, as reconstructed from public data

Sat Rush Fly plays the round game only. Vault tickets, the autominer, affiliates and the RUSH token are not used. Everything below comes from the public API (`api.satrush.io/api/v1/...`), the public web client and settled rounds; it is not official documentation and may change with the game.

## A round

- The board has **21 tiles**. A round lasts **200 slots** (about 63 s on mainnet). The API reports `round_id`, `start_slot`, `end_slot`, `current_slot`, the stake and miner count per tile, the previous rounds' winning tiles and a BTC price.
- A wallet may make **one deploy per round**: `DeployPublic(selection_mask u32, amount u64, is_grubstake_funded bool)`. The amount is USDC (6 decimals), minimum `min_deploy_usd_amount` (1 USDC). The mask has one bit per tile.
- At the end of the round the program draws one winning tile from an on-chain RNG program using slot hashes. A crank settles every deployment; results appear in `/v1/users/{wallet}/deployments`.

## Settlement arithmetic

Verified against `tests/fixtures/round-55575.json`, a real settled round, in `tests/test_rules.py`.

1. A 6.0% deploy fee (strike 2.08%, epoch 1.94%, 1 BTC vault 0.48%, protocol 1.0%, buybacks 0.5%) leaves the **stake**. The stake is split evenly across the selected tiles (integer floor).
2. Each **losing tile** returns its stake minus a 500 bps haircut relative to the fee-adjusted amount: `refund = floor(tile_stake * (10000 - 600 - 500) / (10000 - 600))`. Missing a tile therefore costs 11% of what was placed on it.
3. The **winning tile**'s stakes plus every haircut are converted to BTC (cbBTC, in sats). Miners on the winning tile share it pro rata: `sats = floor(tile_stake * pot_sats / winning_tile_total_stake)`.
4. Every deploy also mints RUSH tokens and hashrate. Their value is reported by the API for live rounds and ignored in paper simulation.

Consequences: covering all 21 tiles always "wins" and still loses about 5% per round; picking fewer tiles wins less often with larger shares. The expected value of every deploy is negative by roughly the fee. Sat Rush Fly has no strategy layer that could change this. It is a neural experiment playing a game of chance under caps, not an edge.

## What Sat Rush Fly sends

- `DeployPublic` with the neurally selected mask and the configured stake, funded from the wallet's USDC account. When the wallet is short and the game holds unclaimed USDC for this wallet, a `ClaimUsd` for the shortfall precedes the deploy in the same transaction.
- `claim`: `ClaimUsd` for all unclaimed USDC and `ClaimSats` for all unclaimed sats shares, run manually.
- Never: automation, vault tickets, affiliate registration, settlement cranking or admin instructions.

Program IDs, PDAs and discriminators live in `stonkfly/satrush/program.py`; `tests/test_program.py` checks derived addresses against public accounts.

## Paper simulation

Paper mode reads the real board and, after each round finishes, fetches the real result. The hypothetical deploy is settled with the arithmetic above, enlarging the winning-tile stake and the pot by its own contribution. It is an approximation: real pots convert USDC to BTC at execution prices, and a real deploy would change other players' shares.
