#!/usr/bin/env bash
set -e

DIR="$(cd "$(dirname "$0")" && pwd)"
FRONTEND="$DIR/frontend"
DIST="$FRONTEND/dist"

# Ensure a compatible Node.js version (Vite 7 requires >=20.19 or >=22.12)
if command -v nvm &>/dev/null || [ -s "${NVM_DIR:-$HOME/.nvm}/nvm.sh" ]; then
  source "${NVM_DIR:-$HOME/.nvm}/nvm.sh" 2>/dev/null
  NODE_MAJOR=$(node -p 'process.versions.node.split(".")[0]')
  NODE_MINOR=$(node -p 'process.versions.node.split(".")[1]')
  if { [ "$NODE_MAJOR" -eq 20 ] && [ "$NODE_MINOR" -lt 19 ]; } || { [ "$NODE_MAJOR" -eq 21 ]; }; then
    echo "Node.js $(node -v) is too old for Vite 7. Switching via nvm..."
    nvm use 22 2>/dev/null || nvm use --lts 2>/dev/null || { echo "Error: No compatible Node.js found. Install Node >=20.19 or >=22.12"; exit 1; }
  fi
fi

# Build frontend if dist is missing or source is newer
if [ ! -d "$DIST" ] || [ -n "$(find "$FRONTEND/src" "$FRONTEND/index.html" -newer "$DIST/index.html" 2>/dev/null)" ]; then
  echo "Building frontend..."
  (cd "$FRONTEND" && npm install --silent && npm run build)
fi

PORT="${1:-8080}"
exec uv run --directory "$DIR" uvicorn main:app --host 0.0.0.0 --port "$PORT" --app-dir "$DIR/backend" --ws-ping-interval 30 --ws-ping-timeout 10
