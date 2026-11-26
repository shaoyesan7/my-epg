#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Threadfin xepg.json fixup v3.4.1 - run on the box while the threadfin container is STOPPED.

What it does, in order:
  0. Provider upkeep in settings.json: keep EXTRA_PROVIDERS registered;
     remove REMOVE_PROVIDERS (currently EMPTY - the v3.3 removal of
     jackTV was a one-off; v3.4 brings jackTV back, but only through the
     filtered playlist live-jacktv.m3u, whose entries with tvg-ids that
     collide with the other playlists were stripped at the source, so
     Threadfin's cross-playlist merge by tvg-id can no longer overwrite
     the telecom records' stream URLs with jackTV's). DB records
     belonging to a removed provider are dropped, EXCEPT records whose
     tvg-id belongs to the telecom playlist - those are merge victims,
     kept and repaired by the URL restore in step 2b.
  1. Activate every channel (x-active = true), except channels the user hid
     (x-hide-channel = true stays untouched). This undoes Threadfin's rule
     that new channels whose tvg-id matches no EPG stay inactive.
  2. Sync channel identity from the playlists: Threadfin keys existing
     channels by a name/URL hash and NEVER refreshes their tvg-id when a
     playlist entry is renamed, so a corrected tvg-id in the playlist never
     reaches the DB (proven 2026-11-26: the Shanghai Dongfang record still
     carried tvg-id "上海东方卫视" a day after the playlist fix). For every
     DB channel whose URL appears in a playlist with a different tvg-id,
     adopt the playlist's tvg-id (internal keys are left untouched).
  2b. Restore stream URLs by tvg-id (v3.3): for every DB channel whose
     tvg-id exists in the telecom playlist, force its URL back to the
     telecom playlist's URL for that tvg-id. This undoes the URL half
     of the tvg-id merge in step 0's note (the poisoned record keeps the
     telecom name and tvg-id, so the URL-keyed sync in step 2 cannot
     see it).
  3. Repair EPG mappings: Threadfin's own auto-mapping only ever runs for
     INACTIVE channels with no XMLTV file assigned (see mapping() in its
     xepg.go), so an active channel whose tvg-id was fixed later keeps an
     empty mapping forever and shows no programme guide. For every active
     channel whose x-mapping is empty / "-", if its tvg-id exists as a
     channel id in one of the EPG sources, set x-xmltv-file + x-mapping.
     Evidence order: (a) mappings already used by healthy channels in this
     DB, (b) channel ids fetched from the EPG URLs passed on the command
     line (file rank = healthy-mapping count, descending).
  4. Telecom-first dedupe: channels that share the same cleaned display name
     are grouped; the copy from the highest-priority playlist stays active,
     the rest are deactivated. Priority: LN-Telecom > hrtv > Combined >
     Hudie > jackTV (lowest - a jackTV copy never wins a name tie).
     Within one playlist tier, a copy that carries an EPG mapping wins.
     v3.4.1: champions are picked per category - jackTV records and hrtv
     records each keep their own best copy inside a name group, separate
     from the main lists' champion, so the three can stay active side by
     side instead of eating each other; only duplicates within the same
     category still collapse to one.

Safety:
  - A timestamped backup of the original file is written next to it first
    (only the newest 3 backups are kept).
  - The result is written atomically (temp file + rename).
  - Any error -> non-zero exit, original file left untouched (the wrapper
    restores the backup and restarts the container).

Usage: python3 threadfin_fixup.py /path/to/xepg.json [url ...]
Extra URLs are classified by content: XMLTV sources feed the mapping
repair, M3U playlists feed the identity sync.
Prints one summary line: total=.. newly_activated=.. tvgid_synced=..
mapping_repaired=.. deduped_off=.. active_final=.. plus detail lines.
"""

import gzip
import hashlib
import json
import os
import re
import shutil
import sys
import time

# Playlist priority by substring of the playlist name (_file.m3u.name):
# Liaoning Telecom first, then hrtv, the combined source, Hudie picks,
# and jackTV last (its copies always lose a name-dedupe tie).
PLAYLIST_PRIORITY = ["辽宁电信", "hrtv", "综合源", "蝴蝶精选", "jackTV"]
KEEP_BACKUPS = 3

# Extra providers to keep registered in settings.json (name, source URL).
# Threadfin downloads them like any UI-added playlist; registering here
# means a new source never needs the web UI or manual URL entry
# (added 2026-11-26 for sources whose servers force a file download
# instead of giving a copyable subscription link).
EXTRA_PROVIDERS = [
    ("hrtv", "https://hrtv.118138.xyz/tv_proxy.m3u"),
    # jackTV returns in v3.4, but ONLY via the filtered copy in the repo:
    # live-jacktv.m3u is the jackTV playlist with every entry whose
    # tvg-id also appears in the other playlists stripped out (the CCTV
    # block etc.), so it can never poison the telecom records through
    # Threadfin's merge by tvg-id. NEVER register the original
    # (unfiltered) jackTV URL here again.
    ("jackTV", "https://raw.githubusercontent.com/shaoyesan7/my-epg/main/live-jacktv.m3u"),
]

# Providers to REMOVE from settings.json: remove_providers() deletes
# their registrations and their DB records. Currently EMPTY - the
# v3.3 removal of jackTV was a one-off (its original playlist poisoned
# the telecom CCTV records; v3.4 re-adds it filtered, see above).
REMOVE_PROVIDERS = []

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


def fetch_url_text(url):
    import urllib.request
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=30) as r:
        raw = r.read()
    if raw[:2] == b"\x1f\x8b":
        raw = gzip.decompress(raw)
    return raw.decode("utf-8", errors="replace")


def fetch_epg_channel_ids(url):
    """Channel ids of one XMLTV source (gzip or plain), or None on failure."""
    try:
        text = fetch_url_text(url)
        return set(re.findall(r'<channel id="([^"]+)"', text))
    except Exception as e:
        print("epg fetch failed (%s): %s" % (url, e))
        return None


def fetch_playlist_tvgids(url):
    """{stream_url: (tvg-id, tvg-logo)} from one M3U playlist, or None."""
    try:
        text = fetch_url_text(url)
    except Exception as e:
        print("playlist fetch failed (%s): %s" % (url, e))
        return None
    mapping = {}
    pending = None
    for line in text.splitlines():
        if line.startswith("#EXTINF"):
            m = re.search(r'tvg-id="([^"]*)"', line)
            g = re.search(r'tvg-logo="([^"]*)"', line)
            pending = (m.group(1).strip() if m else "",
                       g.group(1).strip() if g else "")
        elif line and not line.startswith("#"):
            if pending is not None:
                mapping[line.strip()] = pending
            pending = None
    return mapping


def sync_tvg_ids(channels, playlist_maps):
    """Adopt the playlist tvg-id AND tvg-logo for DB channels. Threadfin
    never refreshes either field for existing channels, so playlist-side
    renames and logo additions never reach the DB on their own (the logo
    half of this trap cost a separate fix round on 2026-11-26).
    Returns ([(name, old, new) id syncs], [(name, logo) logo syncs])."""
    by_url = {}
    for pm in playlist_maps:
        if pm:
            by_url.update(pm)
    synced, logos = [], []
    for ch in channels:
        url = (ch.get("url") or "").strip()
        if not url or url not in by_url:
            continue
        new_id, new_logo = by_url[url]
        old = (ch.get("tvg-id") or "").strip()
        if new_id and new_id != old:
            ch["tvg-id"] = new_id
            synced.append((display_name(ch), old, new_id))
        old_logo = (ch.get("tvg-logo") or "").strip()
        if new_logo and new_logo != old_logo:
            ch["tvg-logo"] = new_logo
            logos.append((display_name(ch), new_logo))
    return synced, logos


def repair_mappings(channels, epg_urls):
    """Fill empty x-mapping for active channels whose tvg-id is a known
    XMLTV channel id. Returns the list of (name, tvg-id, file) repairs."""
    healthy_files = {}
    known_ids = {}
    for ch in channels:
        m = (ch.get("x-mapping") or "").strip()
        f = (ch.get("x-xmltv-file") or "").strip()
        if m and m != "-" and f and f != "-":
            healthy_files[f] = healthy_files.get(f, 0) + 1
            known_ids.setdefault(f, set()).add(m)
    file_rank = sorted(healthy_files, key=lambda f: -healthy_files[f])

    epg_id_sets = []
    for url in epg_urls:
        ids = fetch_epg_channel_ids(url)
        epg_id_sets.append(ids if ids is not None else set())

    repairs = []
    for ch in channels:
        if ch.get("x-hide-channel") or not ch.get("x-active"):
            continue
        m = (ch.get("x-mapping") or "").strip()
        if m and m != "-":
            continue
        tvg = (ch.get("tvg-id") or "").strip()
        if not tvg:
            continue
        target = None
        for f in file_rank:
            if tvg in known_ids.get(f, ()):
                target = f
                break
        if target is None:
            for i, ids in enumerate(epg_id_sets):
                if tvg in ids and i < len(file_rank):
                    target = file_rank[i]
                    break
        if target is not None:
            ch["x-xmltv-file"] = target
            ch["x-mapping"] = tvg
            repairs.append((display_name(ch), tvg, target))
    return repairs


def _save_settings(settings_path, settings):
    """Write settings.json back with the same safety as the xepg write:
    timestamped backup (newest KEEP_BACKUPS kept), then temp + rename."""
    stamp = time.strftime("%Y%m%d-%H%M%S")
    shutil.copy2(settings_path, "%s.bak-%s" % (settings_path, stamp))
    folder = os.path.dirname(settings_path) or "."
    siblings = sorted(p for p in os.listdir(folder) if p.startswith("settings.json.bak-"))
    for old in siblings[:-KEEP_BACKUPS]:
        try:
            os.remove(os.path.join(folder, old))
        except OSError:
            pass
    tmp = settings_path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(settings, f, ensure_ascii=False)
    os.replace(tmp, settings_path)


def register_providers(xepg_path):
    """Ensure EXTRA_PROVIDERS exist in settings.json (same folder as
    xepg.json), mimicking a UI-added playlist: clone the most complete
    existing m3u entry so every Threadfin-specific key matches this
    install, then retarget name / source / local file / provider id.
    Idempotent. Prints one provider_registered=<name> line per new
    provider so the wrapper can run a second pass after Threadfin has
    imported it. Never raises: any problem just skips registration."""
    settings_path = os.path.join(os.path.dirname(xepg_path) or ".", "settings.json")
    try:
        with open(settings_path, "r", encoding="utf-8") as f:
            settings = json.load(f)
        m3u_files = settings["files"]["m3u"]
        if not isinstance(m3u_files, dict) or not m3u_files:
            print("provider register skipped: no existing m3u providers")
            return []
    except Exception as e:
        print("provider register skipped: %s" % e)
        return []

    registered = []
    for name, url in EXTRA_PROVIDERS:
        if any((e or {}).get("file.source") == url for e in m3u_files.values()):
            continue
        donor = max(m3u_files.values(), key=lambda e: len(e or {}))
        entry = json.loads(json.dumps(donor))
        new_id = name.upper() + hashlib.md5(url.encode("utf-8")).hexdigest()[:8].upper()
        entry["id.provider"] = new_id
        entry["name"] = name
        entry["file.source"] = url
        for key in list(entry.keys()):
            if key.startswith("file.") and key != "file.source":
                entry[key] = new_id + ".m3u"
        for key in ("counter.error", "counter.download"):
            if key in entry:
                entry[key] = 0
        if "provider.availability" in entry:
            entry["provider.availability"] = 100
        if "last.update" in entry:
            entry["last.update"] = ""
        entry.pop("new", None)
        m3u_files[new_id] = entry
        registered.append(name)

    if not registered:
        return []
    _save_settings(settings_path, settings)
    for name in registered:
        print("provider_registered=%s" % name)
    return registered


def find_telecom_map(playlist_maps):
    """The telecom playlist's {url: (tvg-id, logo)} map, identified by
    content: its stream URLs are PLTV (10.255.128.136). {} if absent."""
    best, best_hits = {}, 0
    for pm in playlist_maps:
        if not pm:
            continue
        hits = sum(1 for u in pm if "10.255.128.136" in u)
        if hits > best_hits:
            best, best_hits = pm, hits
    return best


def remove_providers(xepg_path, db, telecom_tvgids):
    """Remove REMOVE_PROVIDERS: (a) delete their entries from
    settings.json files.m3u (persisted via _save_settings, the same
    write-back registration uses), (b) drop DB records that belong to
    them - EXCEPT records whose tvg-id is a telecom tvg-id: those are
    telecom channels Threadfin merged with the removed provider's
    entries, and the URL restore repairs them instead of dropping them.
    The DB half is skipped when the telecom playlist could not be
    fetched: without its tvg-id set, merge victims cannot be told apart
    from the provider's own channels, and dropping a victim would delete
    the only DB copy of a telecom channel. Prints one
    provider_removed=<name> records_dropped=<n> line per provider.
    Never raises."""
    settings_path = os.path.join(os.path.dirname(xepg_path) or ".", "settings.json")
    try:
        with open(settings_path, "r", encoding="utf-8") as f:
            settings = json.load(f)
        m3u_files = settings.get("files", {}).get("m3u", {})
        if isinstance(m3u_files, dict):
            doomed = [k for k, e in m3u_files.items()
                      if (e or {}).get("name") in REMOVE_PROVIDERS]
            for k in doomed:
                del m3u_files[k]
            if doomed:
                _save_settings(settings_path, settings)
    except Exception as e:
        print("provider remove (settings) skipped: %s" % e)

    dropped = dict((name, 0) for name in REMOVE_PROVIDERS)
    if telecom_tvgids:
        for key in list(db.keys()):
            ch = db[key]
            pname = ch.get("_file.m3u.name") or ""
            if pname not in REMOVE_PROVIDERS:
                continue
            tvg = (ch.get("tvg-id") or "").strip()
            if tvg and tvg in telecom_tvgids:
                continue  # merge victim - kept, URL restored later
            del db[key]
            dropped[pname] += 1
    else:
        print("provider remove (db) skipped: telecom playlist unavailable")
    for name in REMOVE_PROVIDERS:
        print("provider_removed=%s records_dropped=%d" % (name, dropped[name]))
    return dropped


def url_restore_by_tvgid(channels, telecom_url_by_tvgid):
    """Force every DB channel whose tvg-id is a telecom tvg-id back to
    the telecom playlist's stream URL for that tvg-id. Undoes Threadfin's
    cross-playlist merge by tvg-id, which overwrites a telecom record's
    URL with the colliding playlist's URL (v3.3: jackTV's CCTV entries
    vs the telecom CCTV channels). Prints url_restored=<n>."""
    restored = 0
    for ch in channels:
        tvg = (ch.get("tvg-id") or "").strip()
        if not tvg or tvg not in telecom_url_by_tvgid:
            continue
        want = telecom_url_by_tvgid[tvg]
        if (ch.get("url") or "").strip() != want:
            ch["url"] = want
            restored += 1
    print("url_restored=%d" % restored)
    return restored


def main():
    if len(sys.argv) < 2:
        print("usage: threadfin_fixup.py <xepg.json> [epg-url ...]", file=sys.stderr)
        return 2
    path = sys.argv[1]
    extra_urls = sys.argv[2:]

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

    # --- step 0: provider upkeep in settings.json ---
    register_providers(path)

    # --- fetch the extra URLs once: M3U playlists feed the provider
    # removal, the identity sync and the URL restore; XMLTV sources feed
    # the mapping repair (classified by suffix, as before) ---
    epg_urls, playlist_maps = [], []
    for u in extra_urls:
        if u.endswith(".m3u"):
            playlist_maps.append(fetch_playlist_tvgids(u))
        else:
            epg_urls.append(u)
    telecom_map = find_telecom_map(playlist_maps)
    telecom_tvgids = set(t for (t, _logo) in telecom_map.values() if t)
    telecom_url_by_tvgid = {}
    for u, (t, _logo) in telecom_map.items():
        if t and t not in telecom_url_by_tvgid:
            telecom_url_by_tvgid[t] = u

    # --- step 0b: remove retired providers (REMOVE_PROVIDERS, currently
    # empty - the v3.3 jackTV removal was a one-off) ---
    remove_providers(path, db, telecom_tvgids)

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

    # --- step 2: sync tvg-ids from the playlists, restore telecom URLs
    # by tvg-id, then repair mappings ---
    synced, logo_synced = sync_tvg_ids(channels, playlist_maps)
    for name, old, new in synced:
        print("tvg-id synced: %s %s -> %s" % (name, old, new))
    for name, logo in logo_synced:
        print("logo synced: %s" % name)
    url_restore_by_tvgid(channels, telecom_url_by_tvgid)
    repairs = repair_mappings(channels, epg_urls)
    for name, tvg, f in repairs:
        print("mapping repaired: %s -> %s (%s)" % (name, tvg, f))

    # --- step 3: telecom-first dedupe by cleaned display name ---
    # v3.4.1: champions are picked PER CATEGORY, not per name group.
    # jackTV and hrtv each form their own category (matched by
    # _file.m3u.name, same substring style as playlist_rank); every
    # other playlist is the "main" category. The best record of each
    # non-empty category stays active, so a jackTV copy and an hrtv
    # copy of the same channel name no longer eat each other (or the
    # main lists' copy); duplicates WITHIN one category still collapse
    # to a single champion, chosen by the usual priority + tie-breaks.
    def dedupe_category(ch):
        pname = ch.get("_file.m3u.name") or ""
        if "jackTV" in pname:
            return "jackTV"
        if "hrtv" in pname:
            return "hrtv"
        return "main"

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

        by_category = {}
        for ch in members:
            by_category.setdefault(dedupe_category(ch), []).append(ch)
        for cat_members in by_category.values():
            if len(cat_members) < 2:
                continue
            cat_members.sort(key=sort_key)
            for loser in cat_members[1:]:
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
        "total=%d newly_activated=%d tvgid_synced=%d logo_synced=%d mapping_repaired=%d deduped_off=%d active_final=%d"
        % (total, newly_activated, len(synced), len(logo_synced), len(repairs), deduped_off, active_final)
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
