#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Patch Threadfin's exported M3U: restore catchup (replay) attributes.

Threadfin regenerates threadfin.m3u with only a few attributes per channel
(channelID, tvg-chno, tvg-name, tvg-id, tvg-logo, group-title). The catchup /
catchup-source attributes of the Liaoning Telecom list - which the TV apps
need for the replay feature - are dropped. This script copies them back:

  - reads the original telecom M3U, maps each live URL to its catchup attrs
  - for every entry in threadfin.m3u whose URL matches, inserts the attrs
    into the #EXTINF line (before the comma that ends the attribute section)
  - entries that already carry catchup attributes are left alone (idempotent)
  - the file is replaced atomically; Threadfin serves it from disk per
    request, so NO container restart is needed for this patch.

Usage: python3 patch_catchup.py <threadfin.m3u> <telecom-source.m3u>
Prints: matched=<n> patched=<n> already=<n>
"""

import os
import re
import sys

ATTR_RE = re.compile(r'(catchup(?:-source|-days)?)="([^"]*)"')


def parse_source(path):
    """Return {live_url: 'catchup=".." catchup-source=".."'} from a source M3U."""
    mapping = {}
    pending_attrs = None
    with open(path, "r", encoding="utf-8", errors="replace") as f:
        for line in f:
            line = line.rstrip("\n")
            if line.startswith("#EXTINF"):
                found = ATTR_RE.findall(line)
                pending_attrs = None
                if found:
                    pending_attrs = " ".join('%s="%s"' % kv for kv in found)
            elif line and not line.startswith("#"):
                if pending_attrs:
                    mapping[line.strip()] = pending_attrs
                pending_attrs = None
    return mapping


def insert_attrs(extinf_line, attrs):
    """Insert attrs before the first comma that sits outside quotes."""
    in_quotes = False
    for i, ch in enumerate(extinf_line):
        if ch == '"':
            in_quotes = not in_quotes
        elif ch == "," and not in_quotes:
            return extinf_line[:i] + " " + attrs + extinf_line[i:]
    return extinf_line  # no separator comma found; leave untouched


def main():
    if len(sys.argv) != 3:
        print("usage: patch_catchup.py <threadfin.m3u> <telecom.m3u>", file=sys.stderr)
        return 2
    target_path, source_path = sys.argv[1], sys.argv[2]

    mapping = parse_source(source_path)
    if not mapping:
        print("ERROR: no catchup attributes found in source", file=sys.stderr)
        return 1

    with open(target_path, "r", encoding="utf-8", errors="replace") as f:
        lines = f.read().split("\n")

    matched = patched = already = 0
    for i, line in enumerate(lines):
        if not line.startswith("#EXTINF"):
            continue
        # The stream URL is the next non-empty, non-comment line.
        url = None
        for j in range(i + 1, min(i + 3, len(lines))):
            nxt = lines[j].strip()
            if nxt and not nxt.startswith("#"):
                url = nxt
                break
        if url is None or url not in mapping:
            continue
        matched += 1
        if "catchup" in line:
            already += 1
            continue
        lines[i] = insert_attrs(line, mapping[url])
        patched += 1

    tmp = target_path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    os.replace(tmp, target_path)

    print("matched=%d patched=%d already=%d" % (matched, patched, already))
    return 0


if __name__ == "__main__":
    sys.exit(main())
