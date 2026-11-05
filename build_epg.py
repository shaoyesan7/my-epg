#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
EPG 合成器（给峰少自用）

思路：没有任何单一免费源能同时做到「过去7天 + 未来3天 + 频道全」，
所以把几个每天更新的 XMLTV 源按优先级合并：
  1. zsdc   https://epg.zsdc.eu.org/t.xml.gz            过去7天, 未来约2天, 更新勤
  2. kuke31 https://raw.githubusercontent.com/kuke31/xmlgz/main/all.xml.gz
                                                        过去7天, 频道多(500+)
  3. plsy1  https://raw.githubusercontent.com/plsy1/epg/main/e/seven-days.xml.gz
                                                        过去7天+次日, 运营商数据
  4. 112114 https://epg.112114.xyz/pp.xml.gz            当天, 频道多(500+)

合并规则：
  - 频道按「规范名」对齐（CCTV-1 / CCTV1 / CCTV1综合 视为同一频道）
  - 同一频道同一开始时间的节目，按源优先级取先出现的一条（去重）
  - 只保留 [今天-6天, 今天+3天] 窗口内的节目
输出：epg.xml / epg.xml.gz + stats.json（频道数、节目数、实际日期范围）
"""
import gzip
import io
import json
import re
import sys
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone

CN_TZ = timezone(timedelta(hours=8))

SOURCES = [
    ("zsdc",   "https://epg.zsdc.eu.org/t.xml.gz"),
    ("kuke31", "https://raw.githubusercontent.com/kuke31/xmlgz/main/all.xml.gz"),
    ("plsy1",  "https://raw.githubusercontent.com/plsy1/epg/main/e/seven-days.xml.gz"),
    ("112114", "https://epg.112114.xyz/pp.xml.gz"),
]

UA = {"User-Agent": "Mozilla/5.0 (epg-merge for personal use)"}


def canon(name: str) -> str:
    """频道名规范化：去空格/横杠、全角转半角、去掉常见后缀。"""
    n = name.strip()
    n = n.replace("－", "-").replace("　", " ")
    n = re.sub(r"\s+", "", n)
    n = n.replace("-", "")
    n = n.upper() if re.match(r"^[A-Za-z0-9+\-]+$", n) else n
    for suf in ("综合", "频道"):
        if n.endswith(suf) and len(n) > len(suf) + 1:
            n = n[: -len(suf)]
    return n


def fetch(url: str) -> bytes:
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=60) as r:
        raw = r.read()
    if url.endswith(".gz") or raw[:2] == b"\x1f\x8b":
        raw = gzip.decompress(raw)
    return raw


def parse_xmltv(raw: bytes):
    """返回 (channels: {原id: 显示名}, programmes: [(原id, start, stop, title, desc)])"""
    root = ET.fromstring(raw)
    channels = {}
    for ch in root.iter("channel"):
        cid = ch.get("id")
        dn = ch.findtext("display-name")
        if cid and dn:
            channels[cid] = dn.strip()
    programmes = []
    for p in root.iter("programme"):
        cid = p.get("channel")
        start, stop = p.get("start"), p.get("stop")
        title = p.findtext("title") or ""
        desc = p.findtext("desc") or ""
        if cid and start and stop:
            programmes.append((cid, start, stop, title.strip(), desc.strip()))
    return channels, programmes


def main(out_xml="epg.xml"):
    today = datetime.now(CN_TZ).date()
    win_start = today - timedelta(days=6)   # 过去7天含当天
    win_end = today + timedelta(days=3)     # 未来3天预告

    def in_window(start_str: str) -> bool:
        d = datetime.strptime(start_str[:8], "%Y%m%d").date()
        return win_start <= d <= win_end

    chan_display = {}   # canon -> display name（先到先得，高优先级源优先）
    seen = set()        # (canon, start) 去重
    merged = []         # (canon, start, stop, title, desc)
    per_source = {}

    for sname, url in SOURCES:
        try:
            raw = fetch(url)
            channels, programmes = parse_xmltv(raw)
        except Exception as e:
            per_source[sname] = f"FAILED: {e}"
            continue
        id2canon = {}
        for cid, dn in channels.items():
            c = canon(dn)
            id2canon[cid] = c
            chan_display.setdefault(c, dn)
        added = 0
        for cid, start, stop, title, desc in programmes:
            c = id2canon.get(cid)
            if not c or not in_window(start):
                continue
            key = (c, start)
            if key in seen:
                continue
            seen.add(key)
            merged.append((c, start, stop, title, desc))
            added += 1
        per_source[sname] = f"channels={len(channels)} added_programmes={added}"

    merged.sort(key=lambda x: (x[0], x[1]))

    tv = ET.Element("tv", {
        "generator-info-name": "epg-merge (personal)",
        "source-info-name": "zsdc + kuke31 + plsy1 + 112114",
    })
    used = sorted({m[0] for m in merged})
    for c in used:
        ch = ET.SubElement(tv, "channel", {"id": c})
        dn = ET.SubElement(ch, "display-name", {"lang": "zh"})
        dn.text = chan_display.get(c, c)
    for c, start, stop, title, desc in merged:
        p = ET.SubElement(tv, "programme", {"channel": c, "start": start, "stop": stop})
        t = ET.SubElement(p, "title", {"lang": "zh"})
        t.text = title
        if desc:
            d = ET.SubElement(p, "desc", {"lang": "zh"})
            d.text = desc

    data = ET.tostring(tv, encoding="utf-8", xml_declaration=True)
    with open(out_xml, "wb") as f:
        f.write(data)
    with gzip.open(out_xml + ".gz", "wb") as f:
        f.write(data)

    dates = sorted({m[1][:8] for m in merged})
    stats = {
        "generated_at": datetime.now(CN_TZ).strftime("%Y-%m-%d %H:%M:%S +0800"),
        "window": [str(win_start), str(win_end)],
        "channels": len(used),
        "programmes": len(merged),
        "date_range": [dates[0], dates[-1]] if dates else [],
        "dates": dates,
        "sources": per_source,
    }
    with open("stats.json", "w", encoding="utf-8") as f:
        json.dump(stats, f, ensure_ascii=False, indent=2)
    print(json.dumps(stats, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "epg.xml")
