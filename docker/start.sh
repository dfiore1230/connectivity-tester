#!/usr/bin/env bash
set -euo pipefail

# Start the connectivity logger in the background
/usr/local/bin/connectivity.sh &

# Start the webserver (foreground)
/usr/local/bin/webserver.py

