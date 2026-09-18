#!/bin/sh
set -eu

REPOSITORY_DIR=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
cd "$REPOSITORY_DIR"

LOCK_DIR="$REPOSITORY_DIR/var/host-ops-cycle.lock"
mkdir -p "$REPOSITORY_DIR/var"
if ! mkdir "$LOCK_DIR" 2>/dev/null; then
  echo "Another Host Ops cycle is already running; skipping." >&2
  exit 0
fi
trap 'rmdir "$LOCK_DIR"' EXIT HUP INT TERM

if [ ! -f .env ]; then
  echo "Missing private .env configuration." >&2
  exit 1
fi

set -a
. ./.env
set +a

export PYTHONPATH="$REPOSITORY_DIR/src"
python3 -m host_ops.cli --db var/host-ops.db --config config/property.json poll-ical
python3 -m host_ops.cli --db var/host-ops.db --config config/property.json run-due
python3 -m host_ops.cli --db var/host-ops.db --config config/property.json \
  deliver-outbox --automatic
