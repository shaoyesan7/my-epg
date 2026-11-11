// Douban -> MacCMS (provide/vod) translator.
// Runtime: any fetch-handler platform (Cloudflare Workers / Deno Deploy).
// Endpoints used (all public, no login, no key):
//   list/search categories : https://movie.douban.com/j/search_subjects
//   keyword search         : https://movie.douban.com/j/subject_suggest
//   subject detail         : https://m.douban.com/rexxar/api/v2/{movie|tv}/{id}
// Posters are proxied (/img) because doubanio.com blocks hotlinking (HTTP 418
// without a Douban Referer).

const UA =
  "Mozilla/5.0 (Linux; Android 13; Redmi K60) AppleWebKit/537.36 Chrome/120.0 Mobile Safari/537.36";

const CLASSES = [
  { type_id: 1, type_name: "热门电影", dtype: "movie", tag: "热门" },
  { type_id: 2, type_name: "热门剧集", dtype: "tv", tag: "热门" },
  { type_id: 3, type_name: "高分电影", dtype: "movie", tag: "豆瓣高分" },
  { type_id: 4, type_name: "国产剧", dtype: "tv", tag: "国产剧" },
  { type_id: 5, type_name: "经典电影", dtype: "movie", tag: "经典" },
];
const PAGE_SIZE = 20;

const cache = new Map(); // url -> {at, text}
const CACHE_MS = 5 * 60 * 1000;

async function fetchText(url, referer, bypassCache = false) {
  if (!bypassCache) {
    const hit = cache.get(url);
    if (hit && Date.now() - hit.at < CACHE_MS) return hit.text;
  }
  const r = await fetch(url, {
    headers: { "User-Agent": UA, Referer: referer },
  });
  if (!r.ok) throw new Error("upstream " + r.status);
  const text = await r.text();
  cache.set(url, { at: Date.now(), text });
  return text;
}

// Douban occasionally answers bursts with a non-JSON login/rate-limit page;
// one spaced retry clears most of those.
async function fetchJson(url, referer) {
  for (let attempt = 0; attempt < 2; attempt++) {
    try {
      const text = await fetchText(url, referer, attempt > 0);
      return JSON.parse(text);
    } catch (e) {
      if (attempt === 1) throw e;
      await new Promise((r) => setTimeout(r, 900));
    }
  }
}

function json(obj) {
  return new Response(JSON.stringify(obj), {
    headers: { "Content-Type": "application/json; charset=utf-8" },
  });
}

function pic(base, raw) {
  if (!raw) return "";
  return base + "/img?u=" + encodeURIComponent(raw);
}

function listItem(base, s, cls) {
  const isTv = cls ? cls.dtype === "tv" : s.type === "tv";
  return {
    vod_id: (isTv ? "t_" : "m_") + s.id,
    vod_name: s.title || "",
    vod_pic: pic(base, s.cover || s.img || ""),
    vod_score: s.rate || s.rating || "",
    vod_year: s.year || "",
    vod_remarks: s.episodes_info || (s.is_new ? "新片" : s.rate ? "豆瓣 " + s.rate : ""),
    type_id: cls ? cls.type_id : 0,
    type_name: cls ? cls.type_name : isTv ? "剧集" : "电影",
  };
}

async function handleList(base, cls, pg) {
  const start = (pg - 1) * PAGE_SIZE;
  const url =
    "https://movie.douban.com/j/search_subjects?type=" +
    cls.dtype +
    "&tag=" +
    encodeURIComponent(cls.tag) +
    "&sort=recommend&page_limit=" +
    PAGE_SIZE +
    "&page_start=" +
    start;
  const data = await fetchJson(url, "https://movie.douban.com/");
  const subjects = data.subjects || [];
  const full = subjects.length === PAGE_SIZE;
  return json({
    code: 1,
    msg: "ok",
    page: pg,
    pagecount: full ? pg + 1 : pg,
    limit: String(PAGE_SIZE),
    total: start + subjects.length + (full ? PAGE_SIZE : 0),
    class: CLASSES.map((c) => ({ type_id: c.type_id, type_name: c.type_name })),
    list: subjects.map((s) => listItem(base, s, cls)),
  });
}

async function handleSearch(base, wd) {
  // subject_suggest is a prefix matcher and sometimes rejects long queries
  // with a login page; fall back to progressively shorter prefixes.
  const tries = [...new Set([wd, wd.slice(0, 6), wd.slice(0, 4), wd.slice(0, 2)])];
  let items = [];
  for (const q of tries) {
    if (!q) continue;
    try {
      const url =
        "https://movie.douban.com/j/subject_suggest?q=" + encodeURIComponent(q);
      const data = await fetchJson(url, "https://movie.douban.com/");
      if (Array.isArray(data) && data.length) {
        items = data;
        break;
      }
    } catch {
      // try the next shorter prefix
    }
  }
  return json({
    code: 1,
    msg: "ok",
    page: 1,
    pagecount: 1,
    limit: "20",
    total: items.length,
    class: CLASSES.map((c) => ({ type_id: c.type_id, type_name: c.type_name })),
    list: items.map((s) => listItem(base, s, null)),
  });
}

async function handleDetail(base, idParam) {
  const m = /^(m|t)_(\d+)$/.exec(idParam || "");
  if (!m) return json({ code: 0, msg: "bad id", list: [] });
  const kind = m[1] === "t" ? "tv" : "movie";
  const url = "https://m.douban.com/rexxar/api/v2/" + kind + "/" + m[2];
  const d = await fetchJson(url, "https://m.douban.com/");
  const names = (arr) => (arr || []).map((x) => x.name).filter(Boolean).join(",");
  let playFrom = "";
  let playUrl = "";
  const trailers = (d.trailers || []).filter((t) => t.video_url);
  if (trailers.length) {
    playFrom = "豆瓣预告";
    playUrl = trailers
      .map((t, i) => (t.title || "预告片" + (i + 1)) + "$" + t.video_url)
      .join("#");
  }
  const vod = {
    vod_id: idParam,
    vod_name: d.title || "",
    vod_sub: d.original_title || "",
    vod_pic: pic(base, (d.pic && (d.pic.large || d.pic.normal)) || d.cover_url || ""),
    vod_score: d.rating && d.rating.value ? String(d.rating.value) : "",
    vod_year: d.year || "",
    vod_area: (d.countries || []).join(","),
    vod_lang: (d.languages || []).join(","),
    vod_class: (d.genres || []).join(","),
    vod_actor: names(d.actors),
    vod_director: names(d.directors),
    vod_content: (d.intro || "").trim(),
    vod_remarks: d.card_subtitle || d.episodes_info || "",
    vod_pubdate: (d.pubdate || [])[0] || d.release_date || "",
    vod_duration: (d.durations || []).join(","),
    type_name: kind === "tv" ? "剧集" : "电影",
    vod_play_from: playFrom,
    vod_play_url: playUrl,
    vod_status: 1,
  };
  return json({ code: 1, msg: "ok", page: 1, pagecount: 1, total: 1, list: [vod] });
}

async function handleImg(u) {
  let target;
  try {
    target = new URL(u);
  } catch {
    return new Response("bad url", { status: 400 });
  }
  if (!/(^|\.)doubanio\.com$/.test(target.hostname)) {
    return new Response("forbidden", { status: 403 });
  }
  const r = await fetch(target.toString(), {
    headers: { "User-Agent": UA, Referer: "https://movie.douban.com/" },
  });
  if (!r.ok) return new Response("upstream " + r.status, { status: 502 });
  return new Response(r.body, {
    headers: {
      "Content-Type": r.headers.get("Content-Type") || "image/jpeg",
      "Cache-Control": "public, max-age=86400",
    },
  });
}

export default {
  async fetch(request) {
    const url = new URL(request.url);
    const base = url.origin;
    try {
      if (url.pathname === "/img") return await handleImg(url.searchParams.get("u"));
      const ac = url.searchParams.get("ac") || "";
      const wd = url.searchParams.get("wd") || "";
      if (ac === "detail") {
        return await handleDetail(base, url.searchParams.get("ids") || "");
      }
      if (wd) return await handleSearch(base, wd);
      const tid = parseInt(url.searchParams.get("t") || "1", 10);
      const pg = Math.max(1, parseInt(url.searchParams.get("pg") || "1", 10));
      const cls = CLASSES.find((c) => c.type_id === tid) || CLASSES[0];
      return await handleList(base, cls, pg);
    } catch (e) {
      return json({ code: 0, msg: String(e && e.message || e), list: [] });
    }
  },
};
