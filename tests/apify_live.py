"""Echte Apify-test (alleen via GitHub-secrets). Eén samenvatting met tellingen, geen namen of profielgegevens."""
import collections, os, sys, time
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import apify_linkedin as ap
urls = [u.strip() for u in os.environ.get('BD_TEST_URLS', '').splitlines() if u.strip()][:int(os.environ.get('APIFY_TEST_N', '10'))]
ap.BATCH = 10
cache, t0, klaar = {}, time.time(), []
_oud = ap.run_info
def meet(rid):
    d = _oud(rid)
    if d.get('status') == 'SUCCEEDED' and rid not in klaar:
        klaar.append(rid)
    return d
ap.run_info = meet
_items = ap.items
def meet_items(ds):
    d = _items(ds)
    lijst = d if isinstance(d, list) else []
    sleutels = sorted({k for x in lijst if isinstance(x, dict) for k in x})[:40]
    st = collections.Counter(str(x.get('status')) for x in lijst if isinstance(x, dict))
    url_ok = sum(1 for x in lijst if isinstance(x, dict) and ap.slug(x.get('linkedinUrl') or ''))
    pid = sum(1 for x in lijst if isinstance(x, dict) and x.get('publicIdentifier'))
    err = collections.Counter(str(x.get('error') or x.get('message') or '')[:60] for x in lijst if isinstance(x, dict) and (x.get('error') or x.get('message')))
    oq = collections.Counter(type(x.get('originalQuery')).__name__ + ':' + (','.join(sorted(x['originalQuery'])) if isinstance(x.get('originalQuery'), dict) else '') for x in lijst if isinstance(x, dict))
    print(f'::notice::originalQuery-vorm: {dict(oq)}')
    print(f'::notice::dataset: {len(lijst)} items | status {dict(st)} | linkedinUrl bruikbaar {url_ok} | publicIdentifier {pid} | '
          f'fouten {dict(err)} | velden: {", ".join(sleutels)}')
    return d
ap.items = meet_items
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
