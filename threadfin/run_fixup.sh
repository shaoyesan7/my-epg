#!/bin/sh
# Threadfin fixup: activate new channels + sync tvg-ids + repair EPG
# mappings + telecom-first dedupe. Self-updates all threadfin scripts
# from the GitHub repo first, so new versions never need manual copying.
# The host has no python3, so the editor runs in an ephemeral container
# started from the already-installed QingLong image (no new downloads).

CONF=/opt/docker/threadfin/conf
IMG=whyour/qinglong:2.24.0-debian
LOG="$CONF/fixup.log"
ts=$(date '+%Y-%m-%d %H:%M:%S')
RAW=https://raw.githubusercontent.com/shaoyesan7/my-epg/main

# --- self-update: fetch the latest scripts; on any failure keep local ---
for f in threadfin_fixup.py patch_catchup.py run_catchup.sh guard_catchup.sh run_fixup.sh; do
    if curl -fsSL --max-time 20 -o "$CONF/$f.tmp" "$RAW/threadfin/$f" && [ -s "$CONF/$f.tmp" ]; then
        mv "$CONF/$f.tmp" "$CONF/$f"
        case "$f" in *.sh) chmod +x "$CONF/$f" ;; esac
        echo "$ts self-update: $f refreshed" >>"$LOG"
    else
        rm -f "$CONF/$f.tmp"
        echo "$ts self-update: $f fetch failed, keeping local copy" >>"$LOG"
    fi
done

docker stop threadfin >/dev/null 2>&1

EPG1=$RAW/epg.xml.gz
EPG2=https://epg.sxhz.de5.net/epg/index.php?type=gz
if docker run --rm --entrypoint python3 -v "$CONF":/tf "$IMG" /tf/threadfin_fixup.py /tf/xepg.json "$EPG1" "$EPG2" "$RAW/live-ln-telecom.m3u" "$RAW/live.m3u" "$RAW/live-hudie-picks.m3u" "$RAW/live-migu.m3u" >>"$LOG" 2>&1; then
    echo "$ts fixup OK" >>"$LOG"
else
    echo "$ts fixup FAILED - restoring latest backup" >>"$LOG"
    bak=$(ls -t "$CONF"/xepg.json.bak-* 2>/dev/null | head -1)
    [ -n "$bak" ] && cp "$bak" "$CONF/xepg.json"
fi

docker start threadfin >/dev/null 2>&1
