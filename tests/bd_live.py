"""Echte Bright Data-test (alleen via GitHub-secrets). Print ALLEEN tellingen, geen namen of profielgegevens."""
import os, sys, time, collections
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import brightdata as bd
urls = [u.strip() for u in os.environ.get('BD_TEST_URLS', '').splitlines() if u.strip()]
bd.BATCH = int(os.environ.get('BD_BATCH', '15'))
print(f'::notice::{len(urls)} adressen, batch {bd.BATCH}, sleutel {"aanwezig" if bd.sleutel() else "ONTBREEKT"}')
cache, t0 = {}, time.time()
n = bd.ophalen(urls, cache, log=lambda m: print(m.split(' (snapshot')[0]))
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
