#!/usr/bin/env bash
set -euo pipefail

CONFIG_FILE="/logs/config.env"

# Mirror docker-compose environment into /logs/config.env so there is a single
# canonical place for runtime settings (targets, interval, mtr toggles).
mkdir -p /logs
cat > "$CONFIG_FILE" <<EOF
TARGETS=${TARGETS:-}
INTERVAL_SECONDS=${INTERVAL_SECONDS:-30}
ENABLE_MTR=${ENABLE_MTR:-0}
MTR_CYCLES=${MTR_CYCLES:-1}
MTR_MAX_HOPS=${MTR_MAX_HOPS:-32}
MTR_TIMEOUT_SECONDS=${MTR_TIMEOUT_SECONDS:-6}
EOF

echo "Wrote runtime config to ${CONFIG_FILE} from docker-compose environment"

# Start the connectivity logger in the background
/usr/local/bin/connectivity.sh &

# Start the webserver (foreground)
/usr/local/bin/webserver.py

