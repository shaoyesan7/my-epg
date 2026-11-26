#!/bin/sh
# guard_catchup.sh - self-healing watcher for the Threadfin catchup patch.
# Threadfin regenerates threadfin.m3u on every rebuild and strips the
# catchup attributes. If fewer than 100 channels carry catchup-source
# AND the rebuild is finished (file untouched for 3+ minutes),
# re-apply the patch automatically.
# v3 adds a morning EPG refresh: the user's EPG is rebuilt upstream at
# 06:30/18:30 Beijing, but Threadfin only pulls it at 00:00 and most
# channels' listings end at midnight, so the guide runs dry every early
# morning. Once a day in the 07:00 hour (after the 06:30 build), restart
# Threadfin - it re-imports the EPG on startup. Stamp-guarded: once only.
CONF=/opt/docker/threadfin/conf
M3U=$CONF/data/threadfin.m3u
LOCK=$CONF/.guard_catchup.lock
LOG=$CONF/fixup.log
STAMP=$CONF/.morning_refresh.stamp

[ -f "$M3U" ] || exit 0
# --- morning EPG refresh (v3): once a day, in the 07:00 hour ---
today=$(date +%Y%m%d)
if [ "$(date +%H)" = "07" ] && [ "$(cat "$STAMP" 2>/dev/null)" != "$today" ]; then
    echo "$today" > "$STAMP"
    echo "$(date '+%Y-%m-%d %H:%M:%S') guard: morning EPG refresh - restarting threadfin" >> "$LOG"
    docker restart threadfin >/dev/null 2>&1
    exit 0
fi
# healthy? (a full patch carries 100+ catchup-source lines)
n=$(grep -c 'catchup-source' "$M3U")
[ "$n" -ge 100 ] && exit 0
# file written within the last 180s? a rebuild may still be running - wait
now=$(date +%s)
mt=$(stat -c %Y "$M3U" 2>/dev/null || echo 0)
[ $((now - mt)) -lt 180 ] && exit 0
# sanity: refuse to touch a truncated / half-written export
e=$(grep -c '#EXTINF' "$M3U")
[ "$e" -lt 1500 ] && exit 0
# single instance only
[ -e "$LOCK" ] && exit 0
touch "$LOCK"
trap 'rm -f "$LOCK"' EXIT
echo "$(date '+%Y-%m-%d %H:%M:%S') guard: catchup count=$n, re-applying patch" >> "$LOG"
$CONF/run_catchup.sh
