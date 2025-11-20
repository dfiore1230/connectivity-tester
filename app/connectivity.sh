#!/usr/bin/env bash
set -euo pipefail

TARGET_HOST="${TARGET_HOST:-8.8.8.8}"
TARGETS_ENV="${TARGETS:-}"
INTERVAL_DEFAULT="${INTERVAL_SECONDS:-30}"
INTERVAL="$INTERVAL_DEFAULT"
LOG_FILE="/logs/connectivity.log"
CONFIG_FILE="/logs/config.env"
ROTATE_MAX_SIZE="$((1024 * 1024 * 5))"   # 5MB
ROTATE_MAX_AGE="$((60 * 60 * 24))"      # 1 day
ROTATE_KEEP="5"

echo "Starting connectivity tester"
echo "Default interval: ${INTERVAL_DEFAULT} seconds"
echo "Log file: ${LOG_FILE}"
echo "Initial env TARGETS: ${TARGETS_ENV:-<none>} (TARGET_HOST=${TARGET_HOST})"

touch "$LOG_FILE"

rotate_logs() {
  if [ ! -f "$LOG_FILE" ]; then
    return
  fi

  local now
  now="$(date +%s)"
  local mtime
  mtime="$(stat -c %Y "$LOG_FILE")"
  local age
  age=$((now - mtime))
  local size
  size="$(stat -c %s "$LOG_FILE")"

  if [ "$size" -lt "$ROTATE_MAX_SIZE" ] && [ "$age" -lt "$ROTATE_MAX_AGE" ]; then
    return
  fi

  local ts
  ts="$(date -u +"%Y%m%dT%H%M%SZ")"
  local rotated
  rotated="/logs/connectivity-${ts}.log"

  mv "$LOG_FILE" "$rotated"
  gzip "$rotated"
  touch "$LOG_FILE"

  # Prune old archives (keep newest ROTATE_KEEP)
  ls -1t /logs/connectivity-*.log.gz 2>/dev/null | tail -n +$((ROTATE_KEEP + 1)) | xargs -r rm -f
}

while true; do
  rotate_logs

  # Reload config from /logs/config.env (if present)
  if [ -f "$CONFIG_FILE" ]; then
    NEW_TARGETS_ENV="$TARGETS_ENV"
    NEW_INTERVAL="$INTERVAL"

    while IFS='=' read -r key value; do
      case "$key" in
        TARGETS) NEW_TARGETS_ENV="$value" ;;
        INTERVAL_SECONDS) NEW_INTERVAL="$value" ;;
      esac
    done < "$CONFIG_FILE"

    TARGETS_ENV="$NEW_TARGETS_ENV"
    INTERVAL="$NEW_INTERVAL"
  fi

  # Fallbacks / validation
  if [ -z "${TARGETS_ENV:-}" ]; then
    TARGETS_ENV=""
  fi

  if ! printf '%s' "$INTERVAL" | grep -Eq '^[0-9]+$'; then
    INTERVAL="$INTERVAL_DEFAULT"
  fi
  if [ "$INTERVAL" -lt 1 ] 2>/dev/null; then
    INTERVAL="$INTERVAL_DEFAULT"
  fi

  # Build target list for this loop
  TARGETS_ARR=()
  if [ -n "$TARGETS_ENV" ]; then
    IFS=',' read -ra TARGETS_ARR <<< "$TARGETS_ENV"
  else
    TARGETS_ARR=("$TARGET_HOST")
  fi

  # Shared timestamp for this loop
  TS="$(date -Iseconds)"

  # Public/NAT IP (best-effort, once per loop)
  PUB_IP="$(curl -s --max-time 2 https://api.ipify.org || true)"
  if [ -z "${PUB_IP:-}" ]; then
    PUB_IP="unknown"
  fi

  for T in "${TARGETS_ARR[@]}"; do
    ENTRY="$T"
    if [[ "$ENTRY" == *"="* ]]; then
      TARGET_NAME="${ENTRY%%=*}"
      TARGET_HOST_CURRENT="${ENTRY#*=}"
    else
      TARGET_NAME="$ENTRY"
      TARGET_HOST_CURRENT="$ENTRY"
    fi

    # Resolve destination IP (best effort)
    DST_IP="$(getent hosts "$TARGET_HOST_CURRENT" 2>/dev/null | awk 'NR==1 {print $1}')"
    if [ -z "${DST_IP:-}" ]; then
      DST_IP="$TARGET_HOST_CURRENT"
    fi

    # Determine source IP that would be used to reach the target (LAN IP)
    SRC_IP="$(ip route get "$TARGET_HOST_CURRENT" 2>/dev/null | awk '/src/ {for (i=1;i<=NF;i++) if ($i=="src") print $(i+1)}')"
    if [ -z "${SRC_IP:-}" ]; then
      SRC_IP="unknown"
    fi

    # Short ping test (5 packets, 1s timeout each)
    PING_OUTPUT="$(ping -c 5 -W 1 "$TARGET_HOST_CURRENT" 2>&1 || true)"

    SENT=0
    RECEIVED=0
    LOSS=100
    RTT_AVG="nan"

    PACKETS_LINE="$(echo "$PING_OUTPUT" | grep 'packets transmitted' || true)"
    if [ -n "$PACKETS_LINE" ]; then
      SENT="$(echo "$PACKETS_LINE"    | awk '{print $1}')"
      RECEIVED="$(echo "$PACKETS_LINE" | awk '{print $4}')"
      LOSS="$(echo "$PACKETS_LINE"     | awk -F',' '{print $3}' | tr -dc '0-9')"
    fi

    RTT_LINE="$(echo "$PING_OUTPUT" | grep 'rtt ' || true)"
    if [ -n "$RTT_LINE" ]; then
      RTT_AVG="$(echo "$RTT_LINE" | awk -F'/' '{print $5}')"
    fi

    # JSON-style log line, including target name
    LOG_LINE=$(cat <<EOF
{"timestamp":"$TS","target":"$TARGET_NAME","src_ip":"$SRC_IP","public_ip":"$PUB_IP","dst_host":"$TARGET_HOST_CURRENT","dst_ip":"$DST_IP","sent":$SENT,"received":$RECEIVED,"loss_pct":$LOSS,"rtt_avg_ms":$RTT_AVG}
EOF
)

    echo "$LOG_LINE" | tee -a "$LOG_FILE"
  done

  sleep "$INTERVAL"
done

