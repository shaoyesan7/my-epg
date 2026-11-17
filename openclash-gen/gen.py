import sys, yaml

src = sys.argv[1] if len(sys.argv) > 1 else 'sub-raw.yaml'
with open(src, encoding='utf-8') as f:
    sub = yaml.safe_load(f)
proxies = (sub or {}).get('proxies') or []
if len(proxies) < 10:
    raise SystemExit(f'ABORT: only {len(proxies)} proxies in fetched subscription, refusing to overwrite')

with open('openclash-gen/skeleton.yaml', encoding='utf-8') as f:
    cfg = yaml.safe_load(f)
cfg['proxies'] = proxies
with open('openclash.yaml', 'w', encoding='utf-8') as f:
    yaml.dump(cfg, f, allow_unicode=True, default_flow_style=False, sort_keys=False, width=4096)
print(f'OK: generated openclash.yaml with {len(proxies)} proxies')
