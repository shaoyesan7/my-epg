#!/bin/sh
# Restore catchup (replay) attributes in Threadfin's exported M3U.
# No threadfin restart: it serves the file from disk per request.
# Runs with the QingLong image's python3 (the host has none).
# Schedule: 40 0 * * * /opt/docker/threadfin/conf/run_catchup.sh

CONF=/opt/docker/threadfin/conf
IMG=whyour/qinglong:2.24.0-debian
SRC=/tmp/telecom-src.m3u
URL="https://raw.githubusercontent.com/shaoyesan7/my-epg/main/live-ln-telecom.m3u"
ts=$(date '+%Y-%m-%d %H:%M:%S')

if curl -sL --max-time 30 -o "$SRC" "$URL"; then
    docker run --rm --entrypoint python3 -v "$CONF":/tf -v "$SRC":/tmp/telecom-src.m3u "$IMG" /tf/patch_catchup.py /tf/data/threadfin.m3u /tmp/telecom-src.m3u >>"$CONF/fixup.log" 2>&1 \
        || echo "$ts catchup patch FAILED" >>"$CONF/fixup.log"
else
    echo "$ts catchup patch: source download FAILED" >>"$CONF/fixup.log"
fi
