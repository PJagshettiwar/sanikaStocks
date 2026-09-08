#!/bin/bash
# Read the container's logs from OCI Logging. No SSH, no firewall change.
#   logs.sh          last 30 minutes
#   logs.sh 6h       last 6 hours (m, h or d)
set -euo pipefail

TF_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
WINDOW="${1:-30m}"
LIMIT=1000

number=${WINDOW%[mhd]}
unit=${WINDOW#"$number"}
if ! [[ $number =~ ^[0-9]+$ ]]; then
  echo "Use a window like 30m, 6h or 2d." >&2
  exit 1
fi
case "$unit" in
  m) seconds=$(( number * 60 )) ;;
  h) seconds=$(( number * 3600 )) ;;
  d) seconds=$(( number * 86400 )) ;;
  *) echo "Use a window like 30m, 6h or 2d." >&2; exit 1 ;;
esac

start=$(python3 -c "import datetime,sys;print((datetime.datetime.now(datetime.timezone.utc)-datetime.timedelta(seconds=int(sys.argv[1]))).strftime('%Y-%m-%dT%H:%M:%SZ'))" "$seconds")
end=$(date -u +%Y-%m-%dT%H:%M:%SZ)

log_path=$(terraform -chdir="$TF_DIR" output -raw container_log_search_path)

echo "Logs from $start to $end (newest $LIMIT lines)"

# Newest first so that hitting the limit drops the oldest lines, then reversed
# for reading. join() makes it one string, which is what --raw-output needs to
# print plain text instead of a JSON array.
SUPPRESS_LABEL_WARNING=True oci logging-search search-logs \
  --search-query "search \"$log_path\" | sort by datetime desc" \
  --time-start "$start" \
  --time-end "$end" \
  --limit "$LIMIT" \
  --query 'join(`""`, reverse(data.results[].data."logContent".data.msg))' \
  --raw-output
