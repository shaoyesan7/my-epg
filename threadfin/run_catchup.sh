#!/bin/bash
# Patch the exported threadfin.m3u: telecom channels first, tvg-chno
# renumbered, catchup attributes restored; also swap the XMLTV export
# for the user's own EPG file (patch_catchup.py job 3). Runs AFTER
# Threadfin has regenerated the list (i.e. after the fixup job + its
# restart), because a regeneration would overwrite the patch. No restart
# is needed: Threadfin serves the files from disk per request.
# Matching goes through xepg.json because the export contains only
# Threadfin /stream/<md5> URLs, never the original stream URLs.
set -u
CONF=/opt/docker/threadfin/conf
IMG=whyour/qinglong:2.24.0-debian
LOG=$CONF/fixup.log
SRC_URL=https://raw.githubusercontent.com/shaoyesan7/my-epg/main/live-ln-telecom.m3u
PC_URL=https://raw.githubusercontent.com/shaoyesan7/my-epg/main/threadfin/patch_catchup.py

# Self-update the patcher from the repo (fail-open: if the download
# fails or does not carry the v5.3 marker, keep the local copy).
if curl -sL --max-time 30 -o "$CONF/patch_catchup.py.new" "$PC_URL" \
   && [ -s "$CONF/patch_catchup.py.new" ] \
   && grep -q "sys.exit(main())" "$CONF/patch_catchup.py.new"; then
  mv "$CONF/patch_catchup.py.new" "$CONF/patch_catchup.py"
  chmod +x "$CONF/patch_catchup.py"
else
  rm -f "$CONF/patch_catchup.py.new"
  echo "$(date '+%F %T') catchup: patcher self-update skipped" >> "$LOG"
fi

if ! curl -sL --max-time 30 -o /tmp/telecom-src.m3u "$SRC_URL"; then
  echo "$(date '+%F %T') catchup FAILED: source download" >> "$LOG"
  exit 1
fi

docker run --rm --entrypoint python3 \
  -v "$CONF":/tf -v /tmp/telecom-src.m3u:/tmp/telecom-src.m3u \
  "$IMG" /tf/patch_catchup.py /tf/data/threadfin.m3u /tf/xepg.json /tmp/telecom-src.m3u >> "$LOG" 2>&1
rc=$?
if [ $rc -ne 0 ]; then
  echo "$(date '+%F %T') catchup FAILED rc=$rc" >> "$LOG"
  exit $rc
fi
echo "$(date '+%F %T') catchup OK" >> "$LOG"
