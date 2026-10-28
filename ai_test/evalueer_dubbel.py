"""AI bij dubbele cv's, zoals in de scanner: Python beslist eerst (1500 verzonnen paren); de AI krijgt alleen de
twijfelgevallen + een stel lastige naamgenoten (moeten apart blijven). Gevaarlijk = 'zelfde' bij twee personen."""
import json, os, sys, time
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))
os.environ['CV_AI_MODEL'] = sys.argv[1]
os.environ['CV_AI'] = '1'
naam = sys.argv[2]
import ai_rechter  # noqa: E402
import cv_crawler as c  # noqa: E402
from dubbel_paren import paren  # noqa: E402


class Row(dict):
    def keys(self):
        return super().keys()


def doc(t, i):
    em = c.emails_in(t)
    ph = sorted(set(filter(None, (c.norm_phone(x) for x in c.phones_in(t)))))
    return Row(name=c.guess_name(t, f'cv{i}', em), emails=json.dumps(em), phones=json.dumps(ph), links='[]', sha=str(i),
               path=f'/u/cv{i}.pdf', snippet=c.normalized_snippet(t), tokens=json.dumps(c.signature(t)),
               werkgevers=c.werkgevers_from(t), opleidingen=c.opleidingen_from(t))


twijfel, lastig = [], []
for k, (waar, soort, ta, tb) in enumerate(paren(1500)):
    a, b = doc(ta, 2 * k), doc(tb, 2 * k + 1)
    auto, review, _r = c.safe_pair(a, b)
    if auto:
        continue
    if review and not c.harde_blokkade(a, b) and len(c._bewijs_stukken(a, b)) >= 2:
        twijfel.append((waar, soort, a, b))
    elif waar == 'anders' and soort.startswith('naamgenoot') and len(lastig) < 14:
        lastig.append((waar, soort, a, b))
G = twijfel[:24] + lastig
print(f"::notice::{naam} | {len(twijfel)} twijfelparen na Python (AI krijgt er {min(24, len(twijfel))}) + {len(lastig)} lastige naamgenoten")
fout, goed_samen, t0, regel = [], 0, time.time(), []
for i, (waar, soort, a, b) in enumerate(G, 1):
    r = ai_rechter.zelfde_persoon(c.cv_samenvatting(a), c.cv_samenvatting(b)) or {'oordeel': 'geen-ai'}
    if r['oordeel'] == 'zelfde' and waar == 'anders':
        fout.append(f'{i}:{soort}')
    if r['oordeel'] == 'zelfde' and waar == 'zelfde':
        goed_samen += 1
    regel.append(f"{i}:{waar[:3]}/{r['oordeel'][:3]}{'/F' if r.get('fout') else ''}")
n_zelfde = sum(1 for g in G if g[0] == 'zelfde')
print(f"::notice::{naam} | DUBBEL: FOUT SAMEN {len(fout)} {fout} | echte dubbelen door AI herkend {goed_samen}/{n_zelfde} | "
      f"{(time.time() - t0) / max(1, len(G)):.1f}s per paar")
print(f"::notice::{naam} | " + ' '.join(regel))
