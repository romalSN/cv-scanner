"""AI als hulp bij twijfelgevallen: is dit document een cv? Gemeten: geen-cv ten onrechte 'cv' (gevaarlijk),
cv ten onrechte 'geen cv' (gemist), en of de naam klopt."""
import json, os, sys, time
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ['CV_AI_MODEL'] = sys.argv[1]
naam_model = sys.argv[2]
import ai_rechter  # noqa: E402
import cv_crawler as c  # noqa: E402
gevallen = json.loads((Path(__file__).parent / 'twijfel_gevallen.json').read_text(encoding='utf-8'))
fout_cv, gemist, goed, naam_fout, tijden = [], [], [], [], []
ant = {}
for g in gevallen:
    r = ai_rechter.classify(g['tekst'])
    tijden.append(r['sec'])
    soort = r['soort']
    ant[g['id']] = soort[:3]
    if soort == 'cv' and g['waar'] != 'cv':
        fout_cv.append(g['id'])
    elif g['waar'] == 'cv' and soort != 'cv':
        gemist.append(g['id'])
    else:
        goed.append(g['id'])
    if g['waar'] == 'cv' and soort == 'cv':
        n = c.tidy_name(c.clean_person_name(r['naam'].title()))
        if c.normalized_name(n) != c.normalized_name(g['naam']):
            naam_fout.append(f"{g['id']}:{r['naam']}")
    print(naam_model, g['id'], g['waar'], soort, r)
print(f"::notice::{naam_model} | TWIJFEL: {len(goed)}/{len(gevallen)} goed, FOUT 'cv' {fout_cv}, gemist {gemist}, "
      f"naam fout {naam_fout}, {sum(tijden)/len(tijden):.1f}s per document")
print(f"::notice::{naam_model} | antwoorden " + ' '.join(f'{k}:{v}' for k, v in ant.items()))
