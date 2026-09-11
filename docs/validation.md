# Validation status

Recorded during implementation on 2026-09-11. All transaction tests use an in-memory RPC double; **no real deploys or funded-wallet checks were performed**.

Final local result: **46 tests passed**, plus the opt-in full-connectome test on the freshly prepared MaleCNS graph.

| Check | Observed result | What it does not establish |
| --- | --- | --- |
| Program-derived addresses | Board, config, round, deployment, sats-vault and token-vault PDAs equal the addresses published by the SatRush API; the on-chain board, config, miner and round accounts decode with the expected field values | That the program's instruction semantics match the reconstructed client in every case |
| Settlement arithmetic | Fee, per-tile refund and pro-rata BTC share reproduce every deployment in a real settled round exactly (`tests/fixtures/round-55575.json`) | The value of RUSH tokens or hashrate, or the BTC conversion price of future rounds |
| Execution/unit tests | Stake and capital bounds, stale board, closing round, one intent per round, STOP file, loss stop, rejected transaction, unknown outcome halt and reconciliation, claim-before-deploy on shortfall | Successful execution against a live wallet or under mainnet congestion |
| Paper loop | Synthetic rounds with a seeded stub brain: one deploy per round, settlement credited, reinforcement scheduled for the next observation, checkpoints written | Anything about neural behavior |
| Full-network sensory/feedback test on a real board frame | The frame drives Kenyon cells; reward and aversive pulses spike the identified DAN cells; eligible synapses change; reward differs from an unpaired control; frozen memory stays unchanged; checkpoints restore | Accurate fly vision or an acquired association |
| Palette probe | Blue tile ramps gave about 10 KC spikes on some boards and about 4,500 on others; the warm ramp gave 2,300-4,400 on all four boards probed | That the active regime is the biologically right one |
| Real-brain paper play against the live board | Consecutive real rounds observed, tiles chosen by the DN readout, deploys simulated and settled from real results, aversive pulses delivered on losses, 5 s per observation | A real game return; an edge of any kind |

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
