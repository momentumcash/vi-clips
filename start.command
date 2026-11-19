#!/bin/bash
# Double-click in Finder to start VI Clips.
cd "$(dirname "$0")" && ./start.sh || { read -n 1 -s -r -p "Press any key to close."; }
