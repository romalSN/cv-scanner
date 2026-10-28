"""Echte Bright Data-test (alleen via GitHub-secrets). Print ALLEEN tellingen, geen namen of profielgegevens."""
import os, sys, time, collections
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import brightdata as bd
urls = [u.strip() for u in os.environ.get('BD_TEST_URLS', '').splitlines() if u.strip()]
bd.BATCH = int(os.environ.get('BD_BATCH', '15'))
print(f'::notice::{len(urls)} adressen, batch {bd.BATCH}, sleutel {"aanwezig" if bd.sleutel() else "ONTBREEKT"}')
cache, t0 = {}, time.time()
# Meten wat de progress-API teruggeeft (alleen statuswaarden en veldnamen, geen inhoud)
_orig = bd._verzoek
gezien = []
def meet(methode, pad, body=None, timeout=60):
    raw = _orig(methode, pad, body, timeout)
    if pad.startswith('/progress/') or pad.startswith('/trigger'):
        try:
            import json as _j
            d = _j.loads(raw)
            info = (pad.split('/')[1].split('?')[0], d.get('status'), sorted(d.keys()) if isinstance(d, dict) else type(d).__name__)
        except Exception:
            info = (pad.split('/')[1].split('?')[0], 'geen-json', raw[:60])
        if info not in gezien:
            gezien.append(info)
            print(f'::notice::API {info} na {time.time() - t0:.0f}s')
    elif pad.startswith('/snapshot/'):
        print(f'::notice::download: {len(raw)} tekens, begint met {raw[:1]!r}')
    return raw
bd._verzoek = meet
bd.MAX_WACHT = int(os.environ.get('BD_MAX_WACHT', '900'))
n = bd.ophalen(urls, cache, log=lambda m: print('::notice::' + m.split(' (snapshot')[0]))
open_ = cache.get('bd_open') or {}
for sid in open_:
    try:
        d = bd.download(sid)
        print(f'::notice::na wachten toch downloaden: {len(d)} items')
    except Exception as exc:
        print(f'::notice::download na wachten: {type(exc).__name__} {str(exc)[:80]}')
duur = time.time() - t0
prof = [v for k, v in cache.items() if k.startswith('bd|') and not v.get('fout')]
fouten = collections.Counter(v.get('fout') for k, v in cache.items() if k.startswith('bd|') and v.get('fout'))
velden = collections.Counter()
for p in prof:
    g = bd.gegevens(p)
    for k in ('naam', 'werkgever', 'functie', 'werkervaring', 'opleidingen', 'certificeringen', 'vaardigheden', 'over', 'locatie', 'email', 'telefoon'):
        if g.get(k):
            velden[k] += 1
    velden['experience_null'] += p.get('experience') in (None, [], '')
print(f'::notice::opgehaald {n} profielen van {len(set(bd.slug(u) for u in urls))} in {duur:.0f}s | fouten {dict(fouten)}')
print('::notice::gevuld per veld: ' + ', '.join(f'{k} {v}/{len(prof)}' for k, v in sorted(velden.items())))
print('::notice::velden die Bright Data teruggaf: ' + ', '.join(sorted({k for p in prof for k in p})[:60]))
sys.exit(0 if n > 0 else 1)
