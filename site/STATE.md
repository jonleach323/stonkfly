# Watch site data contract

The watch site is a static page plus four read-only endpoints. A local server
(`python -m stonkfly serve --out runs/paper`) computes them from the run
directory; the Vercel deployment serves the same documents from snapshots the
worker publishes (`run --publish`). The page never has write access to anything.

| Endpoint | Content | Refresh |
| --- | --- | --- |
| `GET /api/state` | The snapshot below (`application/json`) | every 2 s |
| `GET /api/board` | Live public SatRush board summary, proxied server-side | every 2 s |
| `GET /api/audit` | Every deploy intent with signature, hashes and outcome | on demand |
| `GET /api/sensory.png` | The exact 320×180 frame the retina last received | when `publication.frame_sha256` changes |
| `GET /api/atlas.bin`, `/api/atlas.json` | The run's neuron atlas for the neural replay: `SFAT`, u32 version, u32 n, then int16 xyz ×n (soma positions scaled to ±30,000), u8 class ×n, u8 readout group ×n; the JSON carries class names and counts | once per run (`publication.atlas_sha256`) |
| `GET /api/activity.bin` | The latest observation's spikes for the atlas cells: `SFAC`, u32 version, tick, n, bins, bin_ms, then u8 counts [bins × n] | when `publication.activity_sha256` changes |

## `/api/state`

Produced by `stonkfly.publish.snapshot`. Money fields are decimal strings in USDC
with six decimals. Times are Unix seconds (floats). `null` means unknown.

```jsonc
{
  "ready": true,                       // false → only {ready, version, published_at}
  "version": 1,
  "game": "satrush",
  "mode": "paper" | "live",
  "network": "mainnet" | "devnet",
  "status": {
    "running": true,                   // heartbeat within 180 s and not halted
    "phase": "reading the board" | "simulating neurons" | "deploying" | "waiting for the round to settle" | "stopped",
    "heartbeat": 1789107000.1,         // null when the worker never reported
    "feed": "satrush-public-mainnet" | "satrush-public-devnet" | "fixture",
    "halted": null | "Loss stop reached" | "Unknown deploy outcome" | "...",
    "stub_brain": false                // true = seeded noise stood in for the connectome (paper only)
  },
  "tick": 5,                           // observations so far
  "observed_at": 1789105160.5,         // wall time of the latest observation
  "published_at": 1789107572.3,
  "wallet": { "address": null | "Base58", "explorer": null | "https://solscan.io/account/…" },
  "board": {                           // the board the retina saw at the latest observation (null before the first)
    "round_id": 55681, "state": "active", "pending_activation": false,
    "started_at": 1789105100.0, "ends_at": 1789105163.3, "slots_remaining": 150, "slot_ms": 316.0,
    "pot_usd": 454.1, "miners": 50,
    "tile_stakes": [ { "stake_usd": 22.1, "miners": 7 }, … 21 entries, index 0 = tile 1 ],
    "previous_winners": [ { "round_id": 55680, "tile": 9 }, … newest first, up to 5 ],
    "previous_round": { "round_id": 55680, "winning_tile": 9, "pot_sats": 57742, "winners": 68, "miners": 77 } | null,
    "vaults": { "strike_usd": 8243.4, "epoch_usd": 12117.0, "epoch_ends_at": 1789249738.8, "one_btc_btc": 0.911 },  // any field null when absent
    "prices": { "btc": 77211.3, "sat": 7.7e-7, … },
    "fetched_at": 1789105160.0
  },
  "portfolio": {
    "equity": "99.661712",             // cash + in_play (paper); wallet + unclaimed + sats value (live)
    "initial": "100.000000",
    "cash": "98.661712",               // USDC not in a round
    "in_play": "1.000000",             // stake of rounds not yet settled
    "pnl": "-0.338288", "pnl_percent": "-0.338288",
    "fees": "0.240000",                // 6% deploy fees on settled rounds
    "refunds": "3.471000",             // USDC returned from losing tiles
    "deployed": "4.000000",            // total staked in settled rounds
    "sats_won": 247, "sats_won_usd": "0.190712",
    "rounds_played": 4, "rounds_won": 1, "hit_rate_percent": "25.000000" | null,
    "best_round_pnl": "-0.008288" | null,
    "open_rounds": [55681],
    "strikes_played": 1, "strikes_hit": 0, "strike_won_usd": "0",   // Sat Strike rounds the fly played / hit, bonus won
    "hashrate": 0,                     // hashrate earned (live settlements report it; paper cannot)
    "vaults": null | {                 // live only: the wallet's standing, read from the public API
      "epoch": { "iteration": 14, "tickets": 120, "rank": null, "won": false, "won_usd": 0 } | null,
      "one_btc": { "iteration": 2, "tickets": 3, "won": false } | null,
      "epoch_wins": 0, "one_btc_wins": 0, "fetched_at": 1789105160.0
    }
  },
  "rounds": [                          // newest first, up to 30; every deploy intent
    {
      "round_id": 55681, "time": 1789105168.7,
      "status": "PAPER" | "PREPARED" | "SENT" | "CONFIRMED" | "UNKNOWN" | "FAILED" | "SETTLED",
      "tiles": [1,2,3,4,5,6,8,9,12,21], "tile_count": 10, "stake": "1.000000",
      "signature": null | "Base58 transaction signature",
      "won": null | true | false, "winning_tile": null | 9,
      "refund": null | "0.801000", "sats": null | 247, "sats_usd": null | "0.190712",
      "token_usd": null | "0.020000", "fee": null | "0.060000", "pnl": null | "-0.008288",
      "strike": null | false | true,    // the round was a Sat Strike (its bonus is inside refund/sats/pnl)
      "strike_usd": null | "0", "hashrate": null | 0,
      "simulated": null | true | false  // true = paper settlement from real round results
    }
  ],
  "round_count": 5,
  "decisions": [                       // newest first, up to 60; one per observation
    { "tick": 5, "time": 1789105160.5, "round_id": 55681, "tiles": [...], "tile_count": 10,
      "status": "PAPER" | "CONFIRMED" | "FAILED" | "VETO", "reason": null | "Round closing before submission",
      "stimulus": "none" | "reward" | "aversive", "kc_spikes": 4376, "changed_edges": 1795, "median_excess_hz": 4.186 }
  ],
  "neural": {                          // latest observation
    "tiles": [1,2,3,4,5,6,8,9,12,21],
    "group_hz": [36.156, … 21 values],  // mean rate of each tile's neuron group
    "excess_hz": [26.523, … 21 values], // rate minus that group's running average
    "median_excess_hz": 4.186,
    "excess_rel": [2.76, … 21 values],   // (rate - average) / (average + 1 Hz): what the selection rule ranks
    "median_excess_rel": 0.42,
    "brain_ms": 2500.0, "total_spikes": 615021, "reward_spikes": 0, "aversive_spikes": 125, "KC_spikes": 4376,
    "stimulus": "none" | "reward" | "aversive", "stimulus_ms": 0.0, "changed_edges": 1795,
    "stub": false, "compute_seconds": 4.8
  },
  "settings": { "stake": "1", "daily_deploys": 300, "loss_stop": "20", "min_slots_remaining": 40,
                "neural_ms": 500, "learning": true, "min_tiles": 1, "max_tiles": 21, "capital": "100" },
  "costs": { "deploys_today": 5, "fee_bps": 600 | null },
  "history": [ { "time": 1789105000.0, "equity": "100.000000" }, … up to ~600, oldest first ],
  "last_outcomes": [ { "round_id": 55680, "won": true, "winning_tile": 9, "tiles": [...], "stake_usd": "0.94",
                       "refund_usd": "0.801", "sats": 247, "sats_usd": "0.190712", "btc_price": "77211.37",
                       "pnl_usd": "-0.008288", "simulated": true, "fee_bps": 600 } ],
  "readout": { "model": "dn-21-group-relative-median-v3", "cells": 1342, "group_sizes": [64, …], "rule": "…", "validated": false } | null,
  "model": { "connectome": "MaleCNS v1.0", "neurons": 166700, "retained_edges": 25582938, "readout": "…", "learning_validated": false },
  "policy": "…", "animation": "…", "learning_validated": false,
  "publication": { "frame_sha256": "…", "activity_sha256": "…" | null, "atlas_sha256": "…" | null, "provenance_sha256": "…", "publish_error": null, "audit_sha256": "…" }
}
```

### Hosted additions

The Vercel functions add `publication.served_at` (Unix seconds) and, when the
snapshot cannot be served, answer `{"ready": false, "error": "…", "hosting":
"unconfigured" | "unpublished" | "…"}` so the page can tell a missing worker
from a missing deployment. Frames are published content-addressed as
`frames/<sha256>.png` and `/api/sensory.png?sha=<sha256>` fetches one.

## `/api/board`

`Board.summary()` of a fresh public board plus `"live": true`; same shape as
`state.board`. On upstream failure: `{"live": false, "error": "…"}` with status 502.

## `/api/audit`

```jsonc
{ "ready": true, "mode": "paper", "wallet": null | "Base58", "program": "satRushGBRY2vgapeTAkoxz26vL2cYqyPi6CnBj7Tco",
  "provenance_sha256": "…", "model": {…}, "execution": "…",
  "deployments": [ { "round_id": 55681, "status": "PAPER", "created": 1789105168.7, "tiles": [...], "selection_mask": 6543,
                     "amount_micro_usdc": 1000000, "signature": null, "explorer": null | "https://solscan.io/tx/…",
                     "outcome": {…} | null, "tick": 5, "input_sha256": "…", "spike_sha256": "…", "brain_ms": 2500.0 } ],
  "network": "mainnet", "deployment_count": 5, "truncated": false,
  "note": "…" }
```

The audit lists at most the 1,000 most recent deploy intents; `truncated` says
when the worker's ledger holds more.

## Game facts the page may state

- 21 tiles, one winning tile per round (~63 s, 200 slots) drawn on chain.
- One deploy per wallet per round; the stake is split evenly across chosen tiles after a 6% fee.
- Losing tiles refund their stake minus an 11% loss; the winning tile's stakes plus those haircuts are paid out in BTC (sats) to whoever picked it, pro rata.
- Expected value per round is negative; no readout of a random draw is profitable on average.
- Paper mode settles hypothetical deploys from the real winning tile and pot; live mode reads settled results from the public API.
