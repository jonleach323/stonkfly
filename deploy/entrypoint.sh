#!/bin/sh
# Container entrypoint. `worker` plays; `watch` serves the page; anything else runs the CLI.
set -eu

DATA="${STONKFLY_DATA:-/data}"
RUNS="${STONKFLY_RUNS:-/runs}"
MODE="${STONKFLY_MODE:-paper}"
NETWORK="${STONKFLY_NETWORK:-mainnet}"
OUT="$RUNS/$MODE"

prepare_if_needed() {
  if [ ! -f "$DATA/graph.npz" ]; then
    echo "stonkfly: building the connectome under $DATA (about 1.1 GB download, a few minutes)"
    python -m stonkfly prepare
  fi
  python -m stonkfly verify
}

# A bare keypair filename refers to the run volume: that is where `keygen` writes it.
if [ -n "${SATRUSH_KEYPAIR:-}" ] && [ ! -f "$SATRUSH_KEYPAIR" ] && [ -f "$RUNS/$SATRUSH_KEYPAIR" ]; then
  export SATRUSH_KEYPAIR="$RUNS/$SATRUSH_KEYPAIR"
fi

case "${1:-worker}" in
  worker)
    prepare_if_needed
    mkdir -p "$OUT"
    set -- run --network "$NETWORK" --out "$OUT" \
      --stake "${STONKFLY_STAKE:-1}" \
      --loss-stop "${STONKFLY_LOSS_STOP:-20}" \
      --daily-deploys "${STONKFLY_DAILY_DEPLOYS:-300}" \
      --neural-ms "${STONKFLY_NEURAL_MS:-500}"
    [ "$MODE" = "live" ] && set -- "$@" --live
    [ "${STONKFLY_FROZEN:-0}" = "1" ] && set -- "$@" --frozen
    [ -n "${BLOB_READ_WRITE_TOKEN:-}" ] && set -- "$@" --publish
    [ -n "${STONKFLY_PRIORITY_FEE:-}" ] && set -- "$@" --priority-fee "$STONKFLY_PRIORITY_FEE"
    echo "stonkfly: $MODE worker on $NETWORK, run directory $OUT"
    exec python -m stonkfly "$@"
    ;;
  watch)
    mkdir -p "$OUT"
    exec python -m stonkfly serve --out "$OUT" --host 0.0.0.0 --port "${STONKFLY_WATCH_PORT:-8787}" --network "$NETWORK"
    ;;
  sh|bash|python|python3|cat)
    exec "$@"
    ;;
  *)
    exec python -m stonkfly "$@"
    ;;
esac
