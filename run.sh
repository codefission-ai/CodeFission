#!/usr/bin/env bash
# Quick dev launcher — no install needed, just runs from source.
# Usage: bash run.sh [port]
#   bash run.sh         → port 19440
#   bash run.sh 8080    → port 8080
set -e

DIR="$(cd "$(dirname "$0")" && pwd)"
UI="$DIR/ui"
DIST="$UI/dist"
STATIC="$DIR/codefission/static"

# Always build UI
echo "Building UI..."
if command -v nvm &>/dev/null || [ -s "${NVM_DIR:-$HOME/.nvm}/nvm.sh" ]; then
  source "${NVM_DIR:-$HOME/.nvm}/nvm.sh" 2>/dev/null
  nvm use 22 2>/dev/null || true
fi
(cd "$UI" && npm install --silent && npm run build)
rm -rf "$STATIC"
cp -r "$DIST" "$STATIC"

PORT="${1:-19440}"
exec python -m codefission --port "$PORT"
