#!/bin/bash
# Stop the engine + app server that ./start.sh started (by saved PID only).
ROOT="$(cd "$(dirname "$0")" && pwd)"
DATA="${VI_DATA:-$ROOT/data}"
for n in server engine; do
  f="$DATA/$n.pid"
  if [ -f "$f" ] && kill -0 "$(cat "$f")" 2>/dev/null; then kill "$(cat "$f")" && echo "stopped $n ($(cat "$f"))"; fi
  rm -f "$f"
done
