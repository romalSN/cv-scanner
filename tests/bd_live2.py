"""Bright Data: eerdere snapshots bekijken + synchroon /scrape testen. Alleen tellingen/status in de log."""
import json, os, sys, time, urllib.request, urllib.error
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import brightdata as bd
urls = [u.strip() for u in os.environ.get('BD_TEST_URLS', '').splitlines() if u.strip()]
def req(method, pad, body=None, timeout=120):
    r = urllib.request.Request(bd.API + pad, method=method, data=json.dumps(body).encode() if body is not None else None,
                               headers={'Authorization': f'Bearer {bd.sleutel()}', 'Content-Type': 'application/json'})
    with urllib.request.urlopen(r, timeout=timeout) as x:
        return x.status, x.read().decode('utf-8', 'replace')
# 1. eerdere snapshots
try:
    st, raw = req('GET', f'/snapshots?dataset_id={bd.dataset_id()}')
    d = json.loads(raw)
    rij = [(x.get('status'), x.get('dataset_size') or x.get('records') or x.get('size')) for x in (d if isinstance(d, list) else d.get('snapshots', []))][:10]
    print(f'::notice::eerdere snapshots (status, aantal): {rij}')
except Exception as exc:
    print(f'::notice::snapshots-lijst: {type(exc).__name__} {str(exc)[:100]}')
if os.environ.get('BD_BATCH') == 'lijst':
    sys.exit(0)
# 2. synchroon: 3 profielen
t0 = time.time()
try:
    st, raw = req('POST', f'/scrape?dataset_id={bd.dataset_id()}&include_errors=true', [{'url': u} for u in urls[:3]], timeout=300)
    d = json.loads(raw) if raw.strip().startswith(('[', '{')) else None
    if isinstance(d, list):
        ok = [p for p in d if isinstance(p, dict) and not p.get('error')]
        velden = sorted({k for p in ok for k in p})
        print(f'::notice::/scrape: HTTP {st}, {len(ok)}/{len(d)} profielen in {time.time() - t0:.0f}s; '
              f'experience null bij {sum(1 for p in ok if not p.get("experience"))}; velden: {", ".join(velden[:50])}')
    else:
        print(f'::notice::/scrape: HTTP {st} in {time.time() - t0:.0f}s, antwoord-velden: {sorted(d.keys()) if isinstance(d, dict) else raw[:60]}')
except urllib.error.HTTPError as exc:
    print(f'::notice::/scrape: HTTP {exc.code} {exc.read().decode()[:150]}')
except Exception as exc:
    print(f'::notice::/scrape: {type(exc).__name__} {str(exc)[:100]} na {time.time() - t0:.0f}s')
