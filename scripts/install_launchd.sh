#!/bin/sh
set -eu

REPOSITORY_DIR=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
RUNTIME_DIR=${HOST_OPS_RUNTIME_DIR:-"$HOME/Library/Application Support/HostOps"}
LAUNCH_AGENTS_DIR="$HOME/Library/LaunchAgents"
PLIST_NAME="com.host-ops.poll.plist"
PLIST_PATH="$LAUNCH_AGENTS_DIR/$PLIST_NAME"

if [ ! -f "$REPOSITORY_DIR/.env" ] || [ ! -f "$REPOSITORY_DIR/config/property.json" ]; then
  echo "Private .env and config/property.json are required." >&2
  exit 1
fi

mkdir -p "$RUNTIME_DIR/src" "$RUNTIME_DIR/scripts" "$RUNTIME_DIR/config" \
  "$RUNTIME_DIR/var" "$LAUNCH_AGENTS_DIR"
chmod 700 "$RUNTIME_DIR" "$RUNTIME_DIR/config" "$RUNTIME_DIR/var"
cp -R "$REPOSITORY_DIR/src/." "$RUNTIME_DIR/src/"
install -m 700 "$REPOSITORY_DIR/scripts/run_host_ops.sh" \
  "$RUNTIME_DIR/scripts/run_host_ops.sh"
install -m 600 "$REPOSITORY_DIR/.env" "$RUNTIME_DIR/.env"
install -m 600 "$REPOSITORY_DIR/config/property.json" \
  "$RUNTIME_DIR/config/property.json"

for runtime_file in host-ops.db cleaner-outbox.jsonl; do
  if [ -f "$REPOSITORY_DIR/var/$runtime_file" ] && \
     [ ! -f "$RUNTIME_DIR/var/$runtime_file" ]; then
    install -m 600 "$REPOSITORY_DIR/var/$runtime_file" \
      "$RUNTIME_DIR/var/$runtime_file"
  fi
done

python3 "$REPOSITORY_DIR/scripts/render_launchd_plist.py" \
  --runtime-dir "$RUNTIME_DIR" --destination "$RUNTIME_DIR/$PLIST_NAME"
plutil -lint "$RUNTIME_DIR/$PLIST_NAME"

launchctl bootout "gui/$(id -u)/com.host-ops.poll" >/dev/null 2>&1 || true
install -m 600 "$RUNTIME_DIR/$PLIST_NAME" "$PLIST_PATH"
launchctl bootstrap "gui/$(id -u)" "$PLIST_PATH"
launchctl enable "gui/$(id -u)/com.host-ops.poll"
launchctl kickstart "gui/$(id -u)/com.host-ops.poll"

echo "Installed Host Ops runtime at $RUNTIME_DIR"
