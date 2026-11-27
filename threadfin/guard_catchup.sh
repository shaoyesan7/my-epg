#!/bin/sh
# guard_catchup.sh - self-healing watcher for the Threadfin catchup patch.
# Threadfin regenerates threadfin.m3u on every rebuild and strips the
# catchup attributes. If fewer than 100 channels carry catchup-source
# AND the rebuild is finished (file untouched for 3+ minutes),
# re-apply the patch automatically.
# Scheduled EPG refresh: the user's EPG is rebuilt upstream at 06:30 and
# 18:30 Beijing, but Threadfin's own update runs at 00:00, so fresh builds
# sit unpulled for hours (the guide ran dry every early morning before
# the 07:00 leg existed). v4 restarts Threadfin once in the 07:00 hour
# and once in the 19:00 hour - about 30 min after each upstream build -
# so it re-imports the fresh EPG. Stamp-guarded per slot: once only.
# A restart never cuts a channel that is already playing (Threadfin
# answers with a 302 redirect; traffic does not transit the box).
CONF=/opt/docker/threadfin/conf
M3U=$CONF/data/threadfin.m3u
LOCK=$CONF/.guard_catchup.lock
LOG=$CONF/fixup.log

[ -f "$M3U" ] || exit 0
# --- scheduled EPG refresh (v4): 07:00 and 19:00 slots ---
today=$(date +%Y%m%d)
H=$(date +%H)
STAMP=""
[ "$H" = "07" ] && STAMP=$CONF/.morning_refresh.stamp
[ "$H" = "19" ] && STAMP=$CONF/.evening_refresh.stamp
if [ -n "$STAMP" ] && [ "$(cat "$STAMP" 2>/dev/null)" != "$today" ]; then
    echo "$today" > "$STAMP"
    echo "$(date '+%Y-%m-%d %H:%M:%S') guard: scheduled EPG refresh (${H}:00 slot) - restarting threadfin" >> "$LOG"
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
