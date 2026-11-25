#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Patch Threadfin's exported M3U. Three jobs, in order:

  1. ORDER: Liaoning Telecom channels (stream host 10.255.128.136) move to
     the front of the file, keeping their relative order; all other channels
     follow in theirs. tvg-chno is then renumbered 1..N in file order, so
     apps that sort by channel number show the same order as apps that
     follow file order.
  2. CATCHUP: Threadfin drops the catchup / catchup-source attributes of
     the telecom list when it regenerates the M3U, which kills the replay
     feature in TV apps. They are copied back from the original telecom
     M3U, matched per channel by its live URL.
  3. Idempotent: a second run changes nothing (order already right,
     catchup already present). The file is replaced atomically; Threadfin
     serves it from disk per request, so NO container restart is needed.

Usage: python3 patch_catchup.py <threadfin.m3u> <telecom-source.m3u>
Prints: entries=.. telecom_front=.. matched=.. patched=.. already=..
"""

import os
import re
import sys

TELECOM_HOST = "10.255.128.136"
ATTR_RE = re.compile(r'(catchup(?:-source|-days)?)="([^"]*)"')
CHNO_RE = re.compile(r'tvg-chno="[^"]*"')


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
    return extinf_line


def old_chno(extinf_line):
    m = re.search(r'tvg-chno="(\d+)"', extinf_line)
    return int(m.group(1)) if m else 1 << 30


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
        raw_lines = f.read().split("\n")

    # Split into header / entries / footer. An entry is an #EXTINF line,
    # any directive lines that follow it, and the first URL line.
    header, entries, footer = [], [], []
    i = 0
    while i < len(raw_lines) and not raw_lines[i].startswith("#EXTINF"):
        header.append(raw_lines[i])
        i += 1
    while i < len(raw_lines):
        if raw_lines[i].startswith("#EXTINF"):
            block = [raw_lines[i]]
            i += 1
            while i < len(raw_lines) and raw_lines[i].startswith("#"):
                block.append(raw_lines[i])
                i += 1
            url = ""
            if i < len(raw_lines):
                url = raw_lines[i].strip()
                block.append(raw_lines[i])
                i += 1
            entries.append({"block": block, "url": url, "idx": len(entries)})
        else:
            footer.append(raw_lines[i])
            i += 1

    if not entries:
        print("ERROR: no entries found in target", file=sys.stderr)
        return 1

    # --- job 1: telecom first (stable), then renumber tvg-chno 1..N ---
    def sort_key(e):
        front = 0 if TELECOM_HOST in e["url"] else 1
        return (front, old_chno(e["block"][0]), e["idx"])

    entries.sort(key=sort_key)
    telecom_front = sum(1 for e in entries if TELECOM_HOST in e["url"])
    for n, e in enumerate(entries, start=1):
        e["block"][0] = CHNO_RE.sub('tvg-chno="%d"' % n, e["block"][0], count=1)

    # --- job 2: restore catchup attributes by live URL ---
    matched = patched = already = 0
    for e in entries:
        attrs = mapping.get(e["url"])
        if attrs is None:
            continue
        matched += 1
        if "catchup" in e["block"][0]:
            already += 1
            continue
        e["block"][0] = insert_attrs(e["block"][0], attrs)
        patched += 1

    out = header + [line for e in entries for line in e["block"]] + footer
    tmp = target_path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write("\n".join(out))
    os.replace(tmp, target_path)

    print(
        "entries=%d telecom_front=%d matched=%d patched=%d already=%d"
        % (len(entries), telecom_front, matched, patched, already)
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
