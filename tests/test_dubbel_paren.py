"""Duizenden verzonnen cv-paren (ai_test/dubbel_paren.py, 18 soorten): echte dubbelen moeten samen, verschillende
personen nooit. Zelfde beslissing als de scanner: safe_pair, plus stap A van de dubbelcontrole (twijfelpaar met
gedeeld persoonlijk contact én dezelfde werkgever+jaren = samen). Aantal paren: DUBBEL_N (standaard 5000)."""
import collections, json, os, sys
from pathlib import Path
os.environ.setdefault('CV_AI', '0')
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT)); sys.path.insert(0, str(ROOT / 'ai_test'))
import cv_crawler as c  # noqa: E402
from dubbel_paren import paren  # noqa: E402


class Row(dict):
    def keys(self): return super().keys()


def doc(t, i):
    em = c.emails_in(t); ph = sorted(set(filter(None, (c.norm_phone(x) for x in c.phones_in(t)))))
    return Row(name=c.guess_name(t, f'cv{i}', em), emails=json.dumps(em), phones=json.dumps(ph), links='[]', sha=str(i),
               path=f'/u/cv{i}.pdf', snippet=c.normalized_snippet(t), tokens=json.dumps(c.signature(t)),
               werkgevers=c.werkgevers_from(t), opleidingen=c.opleidingen_from(t))


N = int(os.environ.get('DUBBEL_N', '5000'))
P = paren(N)
per = collections.defaultdict(collections.Counter); fout_samen, gemist = [], []
for k, (waar, soort, ta, tb) in enumerate(P):
    a, b = doc(ta, 2 * k), doc(tb, 2 * k + 1)
    auto, review, _r = c.safe_pair(a, b)
    if not auto and review:
        st = c._bewijs_stukken(a, b)
        contact = any(x.startswith(('zelfde persoonlijk', 'zelfde telefoon', 'zelfde LinkedIn')) for x in st)
        auto = bool(not c.harde_blokkade(a, b) and contact and c.zelfde_werk(a, b))
    uitkomst = 'samen' if auto else ('twijfel' if review else 'apart')
    per[soort][uitkomst] += 1
    if waar == 'anders' and auto:
        fout_samen.append((soort, a['name'], b['name']))
    if waar == 'zelfde' and not auto:
        gemist.append((soort, a['name'], b['name']))
for s_, cnt in sorted(per.items()):
    print(f'{s_:42s} {dict(cnt)}')
zelfde = sum(1 for p in P if p[0] == 'zelfde')
print(f'\n{len(P)} paren: ONTERECHT SAMENGEVOEGD {len(fout_samen)} | echte dubbelen samen {zelfde - len(gemist)}/{zelfde}')
for f in fout_samen[:10]:
    print('FOUT samengevoegd:', f)
for f in gemist[:10]:
    print('FOUT gemist:', f)
print('DUBBEL-PAREN OK' if not fout_samen and not gemist else 'FOUT'); sys.exit(1 if fout_samen or gemist else 0)
