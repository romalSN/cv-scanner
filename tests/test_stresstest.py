"""Volledige stresstest tegen tests/stresstest.

Drukt na afloop af hoeveel verkeerde samenvoegingen, gemiste cv's en
twijfelgevallen er zijn. De bouw faalt als Daan of Sasha toch één kandidaat
worden, of als Robin of Wende uit elkaar valt.
"""
import csv
import os
import re
import subprocess
import sys
import tempfile
import unicodedata
from collections import defaultdict
from pathlib import Path

os.environ.setdefault('CV_AI', '0')
ROOT = Path(__file__).resolve().parent.parent
SET = Path(__file__).resolve().parent / 'stresstest'
VERWACHT = SET / 'verwachte_uitkomsten.csv'
SAMEN = {'GROEP_ROBIN', 'GROEP_WENDE'}


def open_csv(path):
    with Path(path).open(encoding='utf-8-sig', newline='') as handle:
        sample = handle.read(4096)
        handle.seek(0)
        delim = ';' if sample.count(';') > sample.count(',') else ','
        return list(csv.DictReader(handle, delimiter=delim))


def key(value):
    return str(value or '').replace('\\', '/').lower()


def main():
    if not VERWACHT.exists():
        print('::error::stresstest-bestanden ontbreken')
        return 1
    out = Path(tempfile.mkdtemp(prefix='cv-stresstest-'))
    env = os.environ.copy()
    env['CV_AI'] = '0'
    cmd = [sys.executable, str(ROOT / 'cv_crawler.py'),
           '--root', str(SET / 'Testbestanden'),
           '--output', str(out),
           '--no-ocr', '--force', '--no-refresh']
    print('STRESSTEST scant', SET / 'Testbestanden', flush=True)
    done = subprocess.run(cmd, cwd=str(ROOT), env=env)
    export = out / 'cv_bestanden.csv'
    if not export.exists():
        print(f'::error::geen cv_bestanden.csv (scanner-code {done.returncode})')
        return 1
    expected = open_csv(VERWACHT)
    found = open_csv(export)
    mapped = {}
    for row in found:
        pad = key(row.get('oorspronkelijk_bestand') or row.get('bestand') or row.get('path'))
        for item in expected:
            rel = item['bestand'].split('Testbestanden/', 1)[-1].lower()
            if pad.endswith('/' + rel) or pad.endswith(rel):
                mapped.setdefault(item['test_id'], row)
    def norm(value):
        tekst = unicodedata.normalize('NFKD', str(value or '')).encode('ascii', 'ignore').decode().lower()
        return re.sub(r'[^a-z0-9]+', ' ', tekst).strip()

    verboden = {'dezelfde_naam_maar_ander_persoon', 'zelfde_naam_conflict'}
    meta = {}
    for item in expected:
        groep = (item.get('verwacht_persoon_groep') or '').strip()
        if not groep:
            continue
        info = meta.setdefault(groep, {'namen': set(), 'cats': set()})
        if (item.get('verwacht_naam') or '').strip():
            info['namen'].add(norm(item['verwacht_naam']))
        info['cats'].add((item.get('categorie') or '').strip())
    ids = defaultdict(set)
    gemist = gemist_ocr = twijfel = 0
    for item in expected:
        row = mapped.get(item['test_id'])
        want = (item.get('verwacht_status') or '').strip().lower()
        got = ((row.get('status') if row else '') or '').strip().lower()
        if want == 'cv' and got != 'cv':
            if (item.get('ocr_nodig') or '').strip().lower() == 'ja':
                gemist_ocr += 1
            elif (item.get('categorie') or '').strip() == 'cv_naam_onbekend' and got == 'review':
                pass
            else:
                gemist += 1
        if got == 'review':
            twijfel += 1
        groep = (item.get('verwacht_persoon_groep') or '').strip()
        if groep and got == 'cv' and row and row.get('persoon_id'):
            ids[groep].add(row['persoon_id'])
    omgekeerd = defaultdict(set)
    for groep, pids in ids.items():
        for pid in pids:
            omgekeerd[pid].add(groep)
    verkeerd = []
    zelfde_persoon = 0
    for pid, groepen in omgekeerd.items():
        if len(groepen) < 2:
            continue
        cats = set().union(*(meta[g]['cats'] for g in groepen))
        namen = set().union(*(meta[g]['namen'] for g in groepen))
        if cats & verboden or len(namen) > 1:
            verkeerd.append((pid, sorted(groepen)))
        else:
            zelfde_persoon += 1
    print(f'VERKEERDE SAMENVOEGINGEN: {len(verkeerd)}')
    for pid, groepen in verkeerd:
        print(f'  {pid}: {", ".join(groepen)}')
    print(f'ZELFDE PERSOON OVER TESTMAPPEN: {zelfde_persoon}')
    print(f'GEMISTE CVS: {gemist}')
    print(f'OCR-SCANS NIET GELEZEN: {gemist_ocr}')
    print(f'TWIJFELGEVALLEN: {twijfel}')
    print(f"::notice::Stresstest: verkeerde samenvoegingen={len(verkeerd)}, "
          f"gemiste cv's={gemist}, twijfelgevallen={twijfel}, ocr niet gelezen={gemist_ocr}")
    fouten = []
    for pid, groepen in verkeerd:
        fouten.append(f'onterecht samengevoegd {pid}: {", ".join(groepen)}')
    if gemist:
        fouten.append(f'{gemist} cv(s) niet herkend')
    for links, rechts in (('DAAN_A', 'DAAN_B'), ('SASHA_A', 'SASHA_B')):
        a, b = ids.get(links, set()), ids.get(rechts, set())
        if not a or not b:
            fouten.append(f'{links}/{rechts} niet allebei als cv herkend ({a or "geen"} / {b or "geen"})')
        elif a & b:
            fouten.append(f'{links} en {rechts} hebben hetzelfde kandidaat-id: {sorted(a & b)}')
    for groep in sorted(SAMEN):
        pids = ids.get(groep, set())
        if len(pids) != 1:
            fouten.append(f'{groep} hoort één kandidaat te zijn, kreeg {sorted(pids) or "geen"}')
    if fouten:
        for regel in fouten:
            print(f'::error::{regel}')
        return 1
    print('STRESSTEST: Daan en Sasha apart, Robin en Wende bij elkaar')
    return 0


if __name__ == '__main__':
    sys.exit(main())
