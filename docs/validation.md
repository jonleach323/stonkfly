# Validation status

Recorded during implementation on 2026-09-11. All transaction tests use an in-memory RPC double; **no real deploys or funded-wallet checks were performed**. The full-connectome test was not run in this environment (no dataset); the readout and controller changes were exercised with the stub brain only.

Final local result: **44 tests passed, 1 skipped** (the opt-in full-connectome test).

| Check | Observed result | What it does not establish |
| --- | --- | --- |
| Program-derived addresses | Board, config, round, deployment, sats-vault and token-vault PDAs equal the addresses published by the SatRush API; the on-chain board, config, miner and round accounts decode with the expected field values | That the program's instruction semantics match the reconstructed client in every case |
| Settlement arithmetic | Fee, per-tile refund and pro-rata BTC share reproduce every deployment in a real settled round exactly (`tests/fixtures/round-55575.json`) | The value of RUSH tokens or hashrate, or the BTC conversion price of future rounds |
| Execution/unit tests | Stake and capital bounds, stale board, closing round, one intent per round, STOP file, loss stop, rejected transaction, unknown outcome halt and reconciliation, claim-before-deploy on shortfall | Successful execution against a live wallet or under mainnet congestion |
| Paper loop | Synthetic rounds with a seeded stub brain: one deploy per round, settlement credited, reinforcement scheduled for the next observation, checkpoints written | Anything about neural behavior |
| Two paper observations of the real public board with the stub brain | Two deploys simulated against real rounds; one real round result settled the first deploy; the round fee and winning-tile stake came from public round data | A real game return; the stub brain is seeded noise, not neural output |

Before any live use, the operator should run `python -m stonkfly run --live --preflight-only` and then a single-step live run with the minimum stake, and inspect the transaction on an explorer. The on-chain program can change; PDAs and discriminators would then need re-verification against `satrush.io`'s client.

## Reproduce

```sh
python -m pytest -q
OPENBLAS_NUM_THREADS=1 STONKFLY_FULL_TEST=1 python -m pytest -q
python -m stonkfly verify
python -m stonkfly run --fixture --stub-brain --steps 6 --out runs/check-fixture
python -m stonkfly run --stub-brain --steps 3 --out runs/check-public
```

The last command reads the current public board and simulates deploys. Rounds, results and paper outcomes will differ. All runtime evidence stays local in `runs/`; it is not uploaded with this report.

Before claiming learned behavior, implement the frozen-weight and shuffled-reinforcement controls, independent starts and memory-reset comparisons described in [the model](model.md). Because the draw is random and the fees are fixed, the only honest performance baseline is the expected loss of the same stake and tile counts placed uniformly at random.
