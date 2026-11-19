#!/usr/bin/env bash
# Local, resumable backfill of attachments, text and live status. One process at a time (lock dir).
# Stops by itself when nothing is pending, when free disk drops below MIN_FREE_GB, when a chunk fails,
# or after 3 chunks in a row that made no progress. Safe to stop with `kill <pid>` and start again:
# a notice is "pending" until its attachments are fetched, and every chunk is saved before the next.
#
#   nohup scripts/run_backfill.sh > /dev/null 2>&1 &      # progress goes to work/backfill.log
#   tail -f work/backfill.log
set -u
cd "$(dirname "$0")/.."
LOG="${LOG:-work/backfill.log}"
CHUNK="${CHUNK:-100}"            # notices per chunk, about 5 minutes at 2.8 s each
MIN_FREE_GB="${MIN_FREE_GB:-10}"
LOCK=work/backfill.lock
mkdir -p work
if ! mkdir "$LOCK" 2>/dev/null; then echo "another backfill holds $LOCK" | tee -a "$LOG"; exit 1; fi
trap 'rmdir "$LOCK"' EXIT

free_gb() { python3 -c "import shutil;print(int(shutil.disk_usage('.').free/1e9))"; }
stamp() { date -u +%Y-%m-%dT%H:%M:%SZ; }
echo "$(stamp) start pid=$$ chunk=$CHUNK min_free_gb=$MIN_FREE_GB free_gb=$(free_gb)" >> "$LOG"

stalled=0; prev_pending=-1
while true; do
  free=$(free_gb)
  if [ "$free" -lt "$MIN_FREE_GB" ]; then
    echo "$(stamp) STOP: free disk ${free} GB is below ${MIN_FREE_GB} GB" >> "$LOG"; exit 3
  fi
  out=$(.venv/bin/python -m presolicitations backfill fetch --limit "$CHUNK" --max-seconds 1500 2>>"$LOG")
  rc=$?
  if [ $rc -ne 0 ]; then
    echo "$(stamp) STOP: chunk failed (exit $rc); see lines above. Nothing from that chunk was written." >> "$LOG"; exit 2
  fi
  line=$(printf '%s' "$out" | python3 -c "
import sys,json
s=json.load(sys.stdin)
keys=['chunk_done','pending_after','new_notices','attachments_added','attachment_text','attachment_no_text','attachment_excluded','attachment_deleted','attachment_controlled','attachment_download_error','attachment_extract_error','resources_errors','live_errors','vanished_new','became_superseded','seconds']
print(' '.join(f'{k}={s.get(k,0)}' for k in keys))")
  pending=$(printf '%s' "$out" | python3 -c "import sys,json;print(json.load(sys.stdin).get('pending_after',0))")
  done_n=$(printf '%s' "$out" | python3 -c "import sys,json;print(json.load(sys.stdin).get('chunk_done',0))")
  echo "$(stamp) $line free_gb=$(free_gb)" >> "$LOG"
  if [ "$pending" -eq 0 ]; then echo "$(stamp) DONE: nothing pending" >> "$LOG"; exit 0; fi
  no_progress=0
  if [ "$done_n" -eq 0 ]; then no_progress=1; fi
  if [ "$prev_pending" -ge 0 ] && [ "$pending" -ge "$prev_pending" ]; then no_progress=1; fi
  if [ "$no_progress" -eq 1 ]; then stalled=$((stalled+1)); else stalled=0; fi
  if [ "$stalled" -ge 3 ]; then echo "$(stamp) STOP: 3 chunks without progress (pending=$pending)" >> "$LOG"; exit 4; fi
  prev_pending=$pending
done
