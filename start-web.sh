#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DASHBOARD_DIR="$ROOT_DIR/dashboard"
BACKEND_HOST="${HOST:-127.0.0.1}"
BACKEND_PORT="${PORT:-8765}"
DASHBOARD_PORT="${DASHBOARD_PORT:-3010}"

export BOT_API_URL="http://${BACKEND_HOST}:${BACKEND_PORT}"

stop_port() {
  local port="$1"
  local label="$2"
  local pids

  pids="$(lsof -ti tcp:"$port" 2>/dev/null || true)"
  if [ -z "$pids" ]; then
    return
  fi

  echo "Stopping existing ${label} on port ${port}: ${pids}"
  kill $pids >/dev/null 2>&1 || true
  sleep 1

  pids="$(lsof -ti tcp:"$port" 2>/dev/null || true)"
  if [ -n "$pids" ]; then
    echo "Force stopping ${label} on port ${port}: ${pids}"
    kill -9 $pids >/dev/null 2>&1 || true
  fi
}

if [ ! -d "$DASHBOARD_DIR/node_modules" ]; then
  echo "Installing dashboard dependencies..."
  (cd "$DASHBOARD_DIR" && npm install)
fi

stop_port "$DASHBOARD_PORT" "dashboard"
stop_port "$BACKEND_PORT" "Python backend"

echo "Starting Python backend: ${BOT_API_URL}"
(cd "$ROOT_DIR" && python3 app.py) &
BACKEND_PID=$!

cleanup() {
  echo
  echo "Stopping local services..."
  if [ -n "$BACKEND_PID" ]; then
    kill "$BACKEND_PID" >/dev/null 2>&1 || true
  fi
}
trap cleanup EXIT INT TERM

sleep 1

echo "Starting dashboard: http://127.0.0.1:${DASHBOARD_PORT}"
echo "Open this URL in your browser:"
echo "http://127.0.0.1:${DASHBOARD_PORT}"
cd "$DASHBOARD_DIR"
exec npm run dev -- -p "$DASHBOARD_PORT"
