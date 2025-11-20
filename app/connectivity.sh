#!/usr/bin/env bash
set -euo pipefail

TARGET_HOST="${TARGET_HOST:-8.8.8.8}"
TARGETS_ENV_DEFAULT="${TARGETS:-}"
TARGETS_ENV="$TARGETS_ENV_DEFAULT"
INTERVAL_DEFAULT="${INTERVAL_SECONDS:-30}"
INTERVAL="$INTERVAL_DEFAULT"
LOG_ROOT="/logs"
LOG_FILE="${LOG_ROOT}/connectivity.log"
CONFIG_FILE="${LOG_ROOT}/config.env"
ROTATE_MAX_SIZE="$((1024 * 1024 * 5))"   # 5MB
ROTATE_MAX_AGE="$((60 * 60 * 24))"      # 1 day
ROTATE_KEEP="5"

MTR_ENABLED_RAW_DEFAULT="${ENABLE_MTR:-0}"
MTR_CYCLES_DEFAULT="${MTR_CYCLES:-1}"
MTR_MAX_HOPS_DEFAULT="${MTR_MAX_HOPS:-32}"
MTR_TIMEOUT_DEFAULT="${MTR_TIMEOUT_SECONDS:-6}"

MTR_ENABLED_RAW="$MTR_ENABLED_RAW_DEFAULT"
MTR_CYCLES="$MTR_CYCLES_DEFAULT"
MTR_MAX_HOPS="$MTR_MAX_HOPS_DEFAULT"
MTR_TIMEOUT="$MTR_TIMEOUT_DEFAULT"
MTR_AVAILABLE=0
MTR_WARNED=0

echo "Starting connectivity tester"
echo "Default interval: ${INTERVAL_DEFAULT} seconds"
echo "Log file: ${LOG_FILE}"
echo "Initial env TARGETS: ${TARGETS_ENV:-<none>} (TARGET_HOST=${TARGET_HOST})"
echo "Startup MTR parameters: ENABLE_MTR=${MTR_ENABLED_RAW_DEFAULT}, CYCLES=${MTR_CYCLES_DEFAULT}, MAX_HOPS=${MTR_MAX_HOPS_DEFAULT}, TIMEOUT_SECONDS=${MTR_TIMEOUT_DEFAULT}"

is_truthy() {
  local value
  value="${1:-}"
  value="$(echo "$value" | tr '[:upper:]' '[:lower:]')"
  case "$value" in
    1|true|yes|on) return 0 ;;
  esac
  return 1
}

evaluate_mtr_state() {
  if is_truthy "$MTR_ENABLED_RAW"; then
    MTR_ENABLED=1
  else
    MTR_ENABLED=0
  fi

  if [ "$MTR_ENABLED" != "1" ]; then
    MTR_AVAILABLE=0
    MTR_WARNED=0
    return
  fi

  if ! printf '%s' "$MTR_CYCLES" | grep -Eq '^[0-9]+$'; then
    MTR_CYCLES="$MTR_CYCLES_DEFAULT"
  fi
  if ! printf '%s' "$MTR_MAX_HOPS" | grep -Eq '^[0-9]+$'; then
    MTR_MAX_HOPS="$MTR_MAX_HOPS_DEFAULT"
  fi
  if ! printf '%s' "$MTR_TIMEOUT" | grep -Eq '^[0-9]+$'; then
    MTR_TIMEOUT="$MTR_TIMEOUT_DEFAULT"
  fi

  if command -v mtr >/dev/null 2>&1; then
    if [ "$MTR_AVAILABLE" != "1" ]; then
      echo "mtr support enabled (cycles=${MTR_CYCLES}, max_hops=${MTR_MAX_HOPS}, timeout=${MTR_TIMEOUT}s)"
    fi
    MTR_AVAILABLE=1
    MTR_WARNED=0
  else
    if [ "$MTR_WARNED" != "1" ]; then
      echo "WARN: ENABLE_MTR=1 but mtr command not found; skipping path insights"
      MTR_WARNED=1
    fi
    MTR_AVAILABLE=0
  fi
}

evaluate_mtr_state

mkdir -p "$LOG_ROOT"
touch "$LOG_FILE"

strip_cr() {
  printf '%s' "$1" | tr -d '\r'
}

trim_ws() {
  local val
  val="$(strip_cr "$1")"
  # Trim leading whitespace
  val="${val#${val%%[![:space:]]*}}"
  # Trim trailing whitespace
  val="${val%${val##*[![:space:]]}}"
  printf '%s' "$val"
}

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
  TARGETS_ENV="$TARGETS_ENV_DEFAULT"
  INTERVAL="$INTERVAL_DEFAULT"
  MTR_ENABLED_RAW="$MTR_ENABLED_RAW_DEFAULT"
  MTR_CYCLES="$MTR_CYCLES_DEFAULT"
  MTR_MAX_HOPS="$MTR_MAX_HOPS_DEFAULT"
  MTR_TIMEOUT="$MTR_TIMEOUT_DEFAULT"

  if [ -f "$CONFIG_FILE" ]; then
    while IFS='=' read -r key value; do
      key="$(trim_ws "$key")"
      value="$(trim_ws "$value")"

      case "$key" in
        ''|\#*) continue ;;
        TARGETS) TARGETS_ENV="$value" ;;
        INTERVAL_SECONDS) INTERVAL="$value" ;;
        ENABLE_MTR) MTR_ENABLED_RAW="$value" ;;
        MTR_CYCLES) MTR_CYCLES="$value" ;;
        MTR_MAX_HOPS) MTR_MAX_HOPS="$value" ;;
        MTR_TIMEOUT_SECONDS) MTR_TIMEOUT="$value" ;;
      esac
    done < "$CONFIG_FILE"
  fi

  evaluate_mtr_state

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
      echo "WARN: could not determine local src IP for ${TARGET_HOST_CURRENT}; ip route get returned no src"
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

    # Optional hop-by-hop insight via mtr (best effort)
    MTR_HOPS=0
    MTR_LAST_HOP=""
    MTR_LAST_LOSS="null"
    MTR_LAST_AVG="null"

    if [ "$MTR_AVAILABLE" -eq 1 ]; then
      MTR_OUTPUT=""
      MTR_EXIT=0
      if ! MTR_OUTPUT="$(timeout "$MTR_TIMEOUT" mtr -r -n -c "$MTR_CYCLES" -m "$MTR_MAX_HOPS" "$TARGET_HOST_CURRENT" 2>&1)"; then
        MTR_EXIT=$?
      fi

      if [ "$MTR_EXIT" -ne 0 ] || [ -z "$MTR_OUTPUT" ]; then
        echo "WARN: mtr run failed for ${TARGET_HOST_CURRENT} (exit=${MTR_EXIT}, output=${MTR_OUTPUT:-<empty>})"

        if echo "$MTR_OUTPUT" | grep -qiE 'Operation not permitted|raw socket'; then
          echo "HINT: mtr needs raw-socket access; add cap_add: NET_RAW (or run privileged) and rebuild/recreate the container"
        fi
      fi

      if [ -n "$MTR_OUTPUT" ]; then
        # Count hops and track the last visible hop's metrics
        # Format reference: hop.|-- host loss% snt last avg best wrst stdev
        MTR_STATS=$(echo "$MTR_OUTPUT" | awk 'NR>2 && $2!~/^-/ {
          hop=$1; gsub("[^0-9]", "", hop);
          host=$2;
          loss=$3; gsub("%", "", loss);
          avg=$(NF-3);
          printf("%s %s %s %s\n", hop, host, loss, avg);
        }')

        if [ -n "$MTR_STATS" ]; then
          MTR_HOPS=$(echo "$MTR_STATS" | wc -l | tr -d ' ')
          MTR_LAST_LINE=$(echo "$MTR_STATS" | tail -n 1)
          MTR_LAST_HOP=$(echo "$MTR_LAST_LINE" | awk '{print $2}')
          MTR_LAST_LOSS_RAW=$(echo "$MTR_LAST_LINE" | awk '{print $3}')
          MTR_LAST_AVG_RAW=$(echo "$MTR_LAST_LINE" | awk '{print $4}')

          if [ -n "$MTR_LAST_LOSS_RAW" ]; then
            MTR_LAST_LOSS="$MTR_LAST_LOSS_RAW"
          fi
          if [ -n "$MTR_LAST_AVG_RAW" ]; then
            MTR_LAST_AVG="$MTR_LAST_AVG_RAW"
          fi
        fi
      fi
    fi

    MTR_LAST_HOP_ESCAPED="$(printf '%s' "$MTR_LAST_HOP" | sed 's/"/\\"/g')"

    # JSON-style log line, including target name
    LOG_LINE=$(cat <<EOF
{"timestamp":"$TS","target":"$TARGET_NAME","src_ip":"$SRC_IP","public_ip":"$PUB_IP","dst_host":"$TARGET_HOST_CURRENT","dst_ip":"$DST_IP","sent":$SENT,"received":$RECEIVED,"loss_pct":$LOSS,"rtt_avg_ms":$RTT_AVG,"mtr_hops":$MTR_HOPS,"mtr_last_hop":"$MTR_LAST_HOP_ESCAPED","mtr_last_loss_pct":$MTR_LAST_LOSS,"mtr_last_avg_ms":$MTR_LAST_AVG}
EOF
)

    echo "$LOG_LINE" | tee -a "$LOG_FILE"

    if [ -n "${WEBHOOK_URL:-}" ]; then
      CURL_OPTS=("-s" "-m" "3" "-H" "Content-Type: application/json")
      if [ -n "${WEBHOOK_TOKEN:-}" ]; then
        CURL_OPTS+=("-H" "Authorization: Bearer ${WEBHOOK_TOKEN}")
      fi
      if [ "${WEBHOOK_INSECURE:-0}" = "1" ]; then
        CURL_OPTS+=("-k")
      fi
      CURL_OPTS+=("-d" "$LOG_LINE" "${WEBHOOK_URL}")
      curl "${CURL_OPTS[@]}" >/dev/null 2>&1 || true
    fi
  done

  sleep "$INTERVAL"
done

