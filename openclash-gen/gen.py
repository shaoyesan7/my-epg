import sys, re, urllib.request, yaml

src = sys.argv[1] if len(sys.argv) > 1 else 'sub-raw.yaml'
with open(src, encoding='utf-8') as f:
    sub = yaml.safe_load(f)
proxies = (sub or {}).get('proxies') or []
if len(proxies) < 10:
    raise SystemExit(f'ABORT: only {len(proxies)} proxies in fetched subscription, refusing to overwrite')

# --- free nodes: ermaozi public aggregator (self-updating), merged + sanitized ---
FREE_URL = 'https://raw.githubusercontent.com/ermaozi/get_subscribe/main/subscribe/clash.yml'
SS_OK = {'aes-128-gcm', 'aes-192-gcm', 'aes-256-gcm', 'chacha20-ietf-poly1305', 'xchacha20-ietf-poly1305',
         '2022-blake3-aes-128-gcm', '2022-blake3-aes-256-gcm', '2022-blake3-chacha20-poly1305',
         'aes-128-cfb', 'aes-192-cfb', 'aes-256-cfb', 'aes-128-ctr', 'aes-192-ctr', 'aes-256-ctr',
         'rc4-md5', 'chacha20-ietf', 'xchacha20', 'none'}
UUID_RE = re.compile(r'^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$')

def free_ok(p):
    if not isinstance(p, dict) or not p.get('name') or not p.get('server') or not p.get('type'):
        return False
    try:
        port = int(p.get('port') or 0)
        if not (1 <= port <= 65535):
            return False
    except Exception:
        return False
    ro = p.get('reality-opts')
    if ro:
        sid = str(ro.get('short-id', ''))
        if sid and (len(sid) > 16 or len(sid) % 2 or not re.fullmatch(r'[0-9a-fA-F]+', sid)):
            return False
        if not ro.get('public-key'):
            return False
    u = p.get('uuid')
    if u is not None and not UUID_RE.match(str(u)):
        return False
    if p['type'] == 'ss' and p.get('cipher') not in SS_OK:
        return False
    if p['type'] in ('vmess', 'vless') and not u:
        return False
    return True

try:
    req = urllib.request.Request(FREE_URL, headers={'User-Agent': 'clash-meta/1.0'})
    with urllib.request.urlopen(req, timeout=30) as r:
        free = yaml.safe_load(r.read().decode('utf-8'))
    fp = (free or {}).get('proxies') or []
    names = {p.get('name') for p in proxies if isinstance(p, dict)}
    added = 0
    for p in fp:
        if free_ok(p) and p['name'] not in names:
            names.add(p['name'])
            proxies.append(p)
            added += 1
    print(f'free nodes merged: {added} of {len(fp)}')
except Exception as e:
    print('free nodes fetch failed, continuing without them:', e)

with open('openclash-gen/skeleton.yaml', encoding='utf-8') as f:
    cfg = yaml.safe_load(f)
cfg['proxies'] = proxies
with open('openclash.yaml', 'w', encoding='utf-8') as f:
    yaml.dump(cfg, f, allow_unicode=True, default_flow_style=False, sort_keys=False, width=4096)
print(f'OK: generated openclash.yaml with {len(proxies)} proxies')
