#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Threadfin xepg.json fixup - run on the box while the threadfin container is STOPPED.

What it does, in order:
  1. Activate every channel (x-active = true), except channels the user hid
     (x-hide-channel = true stays untouched). This undoes Threadfin's rule
     that new channels whose tvg-id matches no EPG stay inactive.
  2. Telecom-first dedupe: channels that share the same cleaned display name
     are grouped; the copy from the highest-priority playlist stays active,
     the rest are deactivated. Priority: LN-Telecom > Combined > Hudie > others.
     Within one playlist tier, a copy that carries an EPG mapping wins.

Safety:
  - A timestamped backup of the original file is written next to it first
    (only the newest 3 backups are kept).
  - The result is written atomically (temp file + rename).
  - Any error -> non-zero exit, original file left untouched (the wrapper
    restores the backup and restarts the container).

Usage: python3 threadfin_fixup.py /path/to/xepg.json
Prints one summary line: total=.. newly_activated=.. deduped_off=.. active_final=..
"""

import json
import os
import re
import shutil
import sys
import time

# Playlist priority by substring of the playlist name (_file.m3u.name):
# Liaoning Telecom first, then the combined source, then Hudie picks.
PLAYLIST_PRIORITY = ["辽宁电信", "综合源", "蝴蝶精选"]
KEEP_BACKUPS = 3

# Bracket pairs used as name decorations: () [] and the CJK variants
# (written as escapes so this file stays pure ASCII outside the names above).
BRACKET_RE = re.compile(r"[\(\[【（][^\)\]】）]*[\)\]】）]")
STRIP_RE = re.compile(r"[^0-9A-Za-z一-鿿]+")


def playlist_rank(name):
    for i, key in enumerate(PLAYLIST_PRIORITY):
        if key in (name or ""):
            return i
    return len(PLAYLIST_PRIORITY)


def clean_name(raw):
    """Normalize a channel display name for duplicate grouping."""
    s = (raw or "").strip()
    s = BRACKET_RE.sub("", s)
    s = STRIP_RE.sub("", s)
    return s.lower()


def display_name(ch):
    return ch.get("x-name") or ch.get("name") or ""


def main():
    if len(sys.argv) != 2:
        print("usage: threadfin_fixup.py <xepg.json>", file=sys.stderr)
        return 2
    path = sys.argv[1]

    with open(path, "r", encoding="utf-8") as f:
        db = json.load(f)
    if not isinstance(db, dict) or not db:
        print("ERROR: xepg.json is empty or not a map", file=sys.stderr)
        return 1

    # --- backup (keep newest KEEP_BACKUPS) ---
    stamp = time.strftime("%Y%m%d-%H%M%S")
    backup = "%s.bak-%s" % (path, stamp)
    shutil.copy2(path, backup)
    folder = os.path.dirname(path) or "."
    base = os.path.basename(path)
    siblings = sorted(p for p in os.listdir(folder) if p.startswith(base + ".bak-"))
    for old in siblings[:-KEEP_BACKUPS]:
        try:
            os.remove(os.path.join(folder, old))
        except OSError:
            pass

    channels = list(db.values())
    total = len(channels)

    # --- step 1: activate everything not hidden ---
    newly_activated = 0
    for ch in channels:
        if ch.get("x-hide-channel"):
            continue
        if not ch.get("x-active"):
            ch["x-active"] = True
            newly_activated += 1

    # --- step 2: telecom-first dedupe by cleaned display name ---
    groups = {}
    for ch in channels:
        if ch.get("x-hide-channel"):
            continue
        key = clean_name(display_name(ch))
        if key:
            groups.setdefault(key, []).append(ch)

    deduped_off = 0
    for key, members in groups.items():
        if len(members) < 2:
            continue

        def sort_key(ch):
            return (
                playlist_rank(ch.get("_file.m3u.name")),
                0 if ch.get("x-mapping") else 1,  # EPG-mapped copy wins ties
            )

        members.sort(key=sort_key)
        for loser in members[1:]:
            if loser.get("x-active"):
                loser["x-active"] = False
                deduped_off += 1

    active_final = sum(1 for ch in channels if ch.get("x-active"))

    # --- atomic write ---
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(db, f, ensure_ascii=False)
    os.replace(tmp, path)

    print(
        "total=%d newly_activated=%d deduped_off=%d active_final=%d"
        % (total, newly_activated, deduped_off, active_final)
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
