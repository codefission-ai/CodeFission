#!/usr/bin/env bash
# Dev launcher — kills old server, builds UI if needed, runs from source.
# Usage: bash run.sh [port]
#   bash run.sh         → port 19440
#   bash run.sh 8080    → port 8080
set -e

DIR="$(cd "$(dirname "$0")" && pwd)"
UI="$DIR/ui"
DIST="$UI/dist"
STATIC="$DIR/codefission/static"
PORT="${1:-19440}"

# Kill any existing fission process
pkill -f "python -m codefission" 2>/dev/null || true
sleep 1
rm -f ~/.codefission/server.lock

# Build UI if dist is missing or source is newer
if [ ! -d "$DIST" ] || [ -n "$(find "$UI/src" "$UI/index.html" -newer "$DIST/index.html" 2>/dev/null)" ]; then
  echo "Building UI..."
  if command -v nvm &>/dev/null || [ -s "${NVM_DIR:-$HOME/.nvm}/nvm.sh" ]; then
    source "${NVM_DIR:-$HOME/.nvm}/nvm.sh" 2>/dev/null
    nvm use 22 2>/dev/null || true
  fi
  (cd "$UI" && npm install --silent && npm run build)
  rm -rf "$STATIC"
  cp -r "$DIST" "$STATIC"
fi

# Run server. Use exec so Ctrl+C goes directly to Python.
# trap ensures cleanup even if signal handling is weird.
trap 'echo "Caught signal"; kill %1 2>/dev/null; exit 0' INT TERM
PYTHONPATH="$DIR/codefission" python -m codefission --port "$PORT" --browser &
wait $!
