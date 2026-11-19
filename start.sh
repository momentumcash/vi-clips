#!/bin/bash
# VI Clips: start the clip engine + the app server, then open the app.
#   ./start.sh            start (or reuse) both and open the browser
#   ./start.sh --no-open  start without opening a browser
# Ports / folders: VI_ENGINE_PORT (8870), VI_SERVER_PORT (8885), VI_DATA (./data), VI_EXPORT_DIR (~/Movies/VI Clips)
set -u
ROOT="$(cd "$(dirname "$0")" && pwd)"
PY="$ROOT/.venv/bin/python"
EP="${VI_ENGINE_PORT:-8870}"
SP="${VI_SERVER_PORT:-8885}"
DATA="${VI_DATA:-$ROOT/data}"
export VI_ENGINE_PORT="$EP" VI_SERVER_PORT="$SP" VI_DATA="$DATA"
LOGS="$DATA/logs"
mkdir -p "$LOGS" "$DATA/engine" "$DATA/app"

if [ ! -x "$PY" ]; then
  echo "No venv yet. Run:  python3.12 -m venv .venv && .venv/bin/pip install -r requirements.txt   (see SETUP.md)"
  exit 1
fi

up() { curl -s -m 2 "http://127.0.0.1:$1/api/ping" | grep -q '"ok": *true'; }

if ! up "$EP"; then
  echo "Starting engine on :$EP"
  (cd "$ROOT" && nohup "$PY" "$ROOT/engine/server.py" --port "$EP" >> "$LOGS/engine.log" 2>&1 & echo $! > "$DATA/engine.pid")
fi
if ! up "$SP"; then
  echo "Starting app server on :$SP"
  (cd "$ROOT" && nohup "$PY" "$ROOT/server/vi_server.py" >> "$LOGS/server.log" 2>&1 & echo $! > "$DATA/server.pid")
fi

for i in $(seq 1 60); do up "$EP" && up "$SP" && break; sleep 0.5; done
if up "$EP" && up "$SP"; then
  URL="http://127.0.0.1:$SP/app/index.html"
  echo "VI Clips is running: $URL"
  [ "${1:-}" = "--no-open" ] || open "$URL"
else
  up "$EP" || { echo "Engine did not start. Last log lines:"; tail -20 "$LOGS/engine.log"; }
  up "$SP" || { echo "App server did not start. Last log lines:"; tail -20 "$LOGS/server.log"; }
  exit 1
fi
