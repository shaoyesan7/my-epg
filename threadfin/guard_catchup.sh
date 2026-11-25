#!/bin/sh
# guard_catchup.sh - self-healing watcher for the Threadfin catchup patch.
# Threadfin regenerates threadfin.m3u on every rebuild and strips the
# catchup attributes. If they are gone AND the rebuild is finished
# (file untouched for 3+ minutes), re-apply the patch automatically.
CONF=/opt/docker/threadfin/conf
M3U=$CONF/data/threadfin.m3u
LOCK=$CONF/.guard_catchup.lock
LOG=$CONF/fixup.log

[ -f "$M3U" ] || exit 0
# already patched? nothing to do
grep -q 'catchup' "$M3U" && exit 0
# file written within the last 180s? a rebuild may still be running - wait
now=$(date +%s)
mt=$(stat -c %Y "$M3U" 2>/dev/null || echo 0)
[ $((now - mt)) -lt 180 ] && exit 0
# sanity: refuse to touch a truncated / half-written export
n=$(grep -c '#EXTINF' "$M3U")
[ "$n" -lt 1700 ] && exit 0
# single instance only
[ -e "$LOCK" ] && exit 0
touch "$LOCK"
trap 'rm -f "$LOCK"' EXIT
echo "$(date '+%Y-%m-%d %H:%M:%S') guard: catchup missing, re-applying patch" >> "$LOG"
$CONF/run_catchup.sh
