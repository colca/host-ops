#!/bin/sh
set -eu

REPOSITORY_DIR=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
cd "$REPOSITORY_DIR"

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
