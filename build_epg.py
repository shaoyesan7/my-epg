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

外加一道「补预告」工序（不是第五个平级源）：
  epgpw https://epg.pw/xmltv/epg_CN.xml  无回放, 预告最长(约6天)。它的节目时间轴与上面
  四源对不齐（同一节目开始时间常差几分钟），若当平级源按时段去重, 会把今天/明天的
  节目单叠出双份。所以只做尾部补齐：四源合并完之后, 对每个频道, 只把 epgpw 里
  开始时间晚于该频道现有最晚节目的条目接在后面（自带去重, 上限今天+6天）。
  已有日期范围一条不动；epgpw 抓取失败时整道工序跳过, 不影响主单发布。

合并规则：
  - 频道按「规范名」对齐（CCTV-1 / CCTV1 / CCTV1综合 视为同一频道）
  - 同一频道同一开始时间的节目，按源优先级取先出现的一条（去重）
  - 四源只保留 [今天-6天, 今天+3天] 窗口内的节目；epgpw 补齐部分单独放宽到今天+6天
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

PW_URL = "https://epg.pw/xmltv/epg_CN.xml"

SXHZ_URL = "https://epg.sxhz.de5.net/epg/index.php?type=gz"

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
    # CCTV5+ 的各种写法（CCTV5PLUS / CCTV5+体育赛事 等）统一为 CCTV5+，避免与 CCTV5 混淆或被拆成多个频道
    if n == "CCTV5PLUS" or n.startswith("CCTV5+"):
        return "CCTV5+"
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


def append_pw(merged, chan_display, today):
    """尾部补齐：把 epgpw 的节目按规范名对齐, 只接在每个频道现有覆盖的末尾之后。
    返回实际补进的条数。抓取/解析失败会抛异常, 由调用方兜底跳过。"""
    raw = fetch(PW_URL)
    channels, programmes = parse_xmltv(raw)
    id2canon = {}
    for cid, dn in channels.items():
        c = canon(dn)
        if c in chan_display:           # 只补已有频道, 不新建频道
            id2canon[cid] = c
    last_start = {}
    for c, start, stop, title, desc in merged:
        if start > last_start.get(c, ""):
            last_start[c] = start
    cap = (today + timedelta(days=6)).strftime("%Y%m%d")
    seen = {(c, s) for c, s, *_ in merged}
    added = 0
    for cid, start, stop, title, desc in programmes:
        c = id2canon.get(cid)
        if not c:
            continue
        if start[:8] > cap:
            continue
        if start <= last_start.get(c, ""):
            continue                    # 已有覆盖范围之内, 一条不碰
        key = (c, start)
        if key in seen:
            continue                    # epgpw 内部同台多份, 去重
        seen.add(key)
        merged.append((c, start, stop, title, desc))
        added += 1
    return added


def append_sxhz(merged, chan_display, today, win_start):
    """sxhz 并入（用户思路, 2026-11-27）：sxhz 原来整份登记在 Threadfin 里,
    与主单同名的频道会被 Threadfin 随机挂到较短的那份上（CCTV-1 只到当天）。
    改为像 epgpw 一样并进本流水线当原料, Threadfin 只用主单一份：
      - 已有频道（按规范名对齐, 或 sxhz 的原始 id 恰好就是主单频道 id）：
        只做尾部补齐, 规则同 epgpw（晚于现有最晚节目才接, 去重, 上限+6天）；
      - 全新频道（sxhz 独有）：整台并入, 频道 id 沿用 sxhz 的 id（Threadfin
        里已有频道正是按这个 id 挂 sxhz 的, 并入后可原样改挂主单）, 节目裁到
        [今天-6天, 今天+6天] 窗口, 自带去重；没有任何在窗口内节目的不建台。
    返回 (尾部补齐条数, 新增频道数)。抓取/解析失败抛异常, 调用方跳过。"""
    raw = fetch(SXHZ_URL)
    channels, programmes = parse_xmltv(raw)
    out_keys = set(chan_display)
    id2key = {}
    new_ids = {}
    for cid, dn in channels.items():
        c = canon(dn)
        if c in out_keys:
            id2key[cid] = c
        elif cid in out_keys:
            id2key[cid] = cid
        else:
            id2key[cid] = cid
            new_ids[cid] = dn
    last_start = {}
    for c, start, stop, title, desc in merged:
        if start > last_start.get(c, ""):
            last_start[c] = start
    cap = (today + timedelta(days=6)).strftime("%Y%m%d")
    win_lo = win_start.strftime("%Y%m%d")
    seen = {(c, s) for c, s, *_ in merged}
    added_tail = 0
    new_prog = {}
    for cid, start, stop, title, desc in programmes:
        key = id2key.get(cid)
        if key is None:
            continue
        if start[:8] > cap:
            continue
        k = (key, start)
        if key in new_ids:
            if start[:8] < win_lo:
                continue
            if k in seen:
                continue
            seen.add(k)
            merged.append((key, start, stop, title, desc))
            new_prog[key] = new_prog.get(key, 0) + 1
        else:
            if start <= last_start.get(key, ""):
                continue
            if k in seen:
                continue
            seen.add(k)
            merged.append((key, start, stop, title, desc))
            added_tail += 1
    added_ch = 0
    for cid, dn in new_ids.items():
        if new_prog.get(cid):
            chan_display[cid] = dn
            added_ch += 1
    return added_tail, added_ch


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

    try:
        pw_added = append_pw(merged, chan_display, today)
        per_source["epgpw"] = f"appended_programmes={pw_added}"
    except Exception as e:
        per_source["epgpw"] = f"FAILED: {e}"

    try:
        sx_tail, sx_new = append_sxhz(merged, chan_display, today, win_start)
        per_source["sxhz"] = f"appended_programmes={sx_tail} new_channels={sx_new}"
    except Exception as e:
        per_source["sxhz"] = f"FAILED: {e}"

    merged.sort(key=lambda x: (x[0], x[1]))

    tv = ET.Element("tv", {
        "generator-info-name": "epg-merge (personal)",
        "source-info-name": "zsdc + kuke31 + plsy1 + 112114 + epgpw(tail) + sxhz(tail+new)",
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
