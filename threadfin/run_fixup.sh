#!/bin/sh
# Threadfin fixup: activate new channels + sync tvg-ids + repair EPG
# mappings + telecom-first dedupe. Self-updates all threadfin scripts
# from the GitHub repo first, so new versions never need manual copying.
# The host has no python3, so the editor runs in an ephemeral container
# started from the already-installed QingLong image (no new downloads).
#
# v5 (2026-11-26): when the fixup registers a NEW provider in
# settings.json (jackTV), Threadfin imports its channels at startup and
# most arrive inactive (no EPG match). So after the normal start, wait
# for the import to land in xepg.json, then run the fixup a second time
# to activate + dedupe them. One command for the user, one-time cost.

CONF=/opt/docker/threadfin/conf
IMG=whyour/qinglong:2.24.0-debian
LOG="$CONF/fixup.log"
OUT="$CONF/.fixup-out"
ts=$(date '+%Y-%m-%d %H:%M:%S')
RAW=https://raw.githubusercontent.com/shaoyesan7/my-epg/main
EPG1=$RAW/epg.xml.gz
EPG2=https://epg.sxhz.de5.net/epg/index.php?type=gz

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

run_fixup_once() {
    docker run --rm --entrypoint python3 -v "$CONF":/tf "$IMG" /tf/threadfin_fixup.py /tf/xepg.json "$EPG1" "$EPG2" "$RAW/live-ln-telecom.m3u" "$RAW/live.m3u" "$RAW/live-hudie-picks.m3u" "$RAW/live-migu.m3u" >"$OUT" 2>&1
    rc=$?
    cat "$OUT" >>"$LOG"
    return $rc
}

imported() {
    grep -q "Juli-God" "$CONF/xepg.json" 2>/dev/null || grep -q "cdn.qd.je" "$CONF/xepg.json" 2>/dev/null
}

docker stop threadfin >/dev/null 2>&1

if run_fixup_once; then
    echo "$ts fixup OK" >>"$LOG"
else
    echo "$ts fixup FAILED - restoring latest backup" >>"$LOG"
    bak=$(ls -t "$CONF"/xepg.json.bak-* 2>/dev/null | head -1)
    [ -n "$bak" ] && cp "$bak" "$CONF/xepg.json"
fi

docker start threadfin >/dev/null 2>&1

# --- second pass, only when this run registered a new provider ---
if grep -q "provider_registered=" "$OUT" 2>/dev/null; then
    echo "jackTV registered - waiting for Threadfin to import it (up to 6 min)..."
    waited=0
    while [ $waited -lt 360 ]; do
        if imported; then break; fi
        sleep 20
        waited=$((waited + 20))
    done
    if imported; then
        sleep 30
        echo "import seen - second pass: activating the new channels..."
        docker stop threadfin >/dev/null 2>&1
        if run_fixup_once; then
            echo "$(date '+%Y-%m-%d %H:%M:%S') fixup second pass OK" >>"$LOG"
        fi
        docker start threadfin >/dev/null 2>&1
        echo "done - jackTV is in Threadfin now"
    else
        echo "jackTV import not seen within 6 min - its channels will be activated by the next fixup run" | tee -a "$LOG"
    fi
fi
