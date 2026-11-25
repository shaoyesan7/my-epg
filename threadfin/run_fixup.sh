#!/bin/sh
# Threadfin nightly fixup: activate new channels + telecom-first dedupe.
# The host has no python3, so the editor runs in an ephemeral container
# started from the already-installed QingLong image (no new downloads).
# Schedule: 20 0 * * * /opt/docker/threadfin/conf/run_fixup.sh

CONF=/opt/docker/threadfin/conf
IMG=whyour/qinglong:2.24.0-debian
LOG="$CONF/fixup.log"
ts=$(date '+%Y-%m-%d %H:%M:%S')

docker stop threadfin >/dev/null 2>&1

if docker run --rm --entrypoint python3 -v "$CONF":/tf "$IMG" /tf/threadfin_fixup.py /tf/xepg.json >>"$LOG" 2>&1; then
    echo "$ts fixup OK" >>"$LOG"
else
    echo "$ts fixup FAILED - restoring latest backup" >>"$LOG"
    bak=$(ls -t "$CONF"/xepg.json.bak-* 2>/dev/null | head -1)
    [ -n "$bak" ] && cp "$bak" "$CONF/xepg.json"
fi

docker start threadfin >/dev/null 2>&1
