"""Echte Apify-test (alleen via GitHub-secrets). Eén samenvatting met tellingen, geen namen of profielgegevens."""
import collections, os, sys, time
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import apify_linkedin as ap
urls = [u.strip() for u in os.environ.get('BD_TEST_URLS', '').splitlines() if u.strip()]
cache, t0, klaar = {}, time.time(), []
_oud = ap.run_info
def meet(rid):
    d = _oud(rid)
    if d.get('status') == 'SUCCEEDED' and rid not in klaar:
        klaar.append(rid)
    return d
ap.run_info = meet
n = ap.ophalen(urls, cache, log=lambda m: None)
duur = time.time() - t0
prof = [v for k, v in cache.items() if k.startswith('ap|') and not v.get('fout')]
fouten = collections.Counter(v.get('fout') for k, v in cache.items() if k.startswith('ap|') and v.get('fout'))
tel = collections.Counter()
for p in prof:
    for k, v in ap.gegevens(p).items():
        if v: tel[k] += 1
print(f'::notice::Apify: {n} van {len(urls)} profielen in {duur:.0f}s (token {"ok" if ap.sleutel() else "ONTBREEKT"}) | '
      f'fouten {dict(fouten)} | gevuld: ' + ', '.join(f'{k} {v}/{len(prof)}' for k, v in sorted(tel.items())))
sys.exit(0 if n > 0 else 1)
