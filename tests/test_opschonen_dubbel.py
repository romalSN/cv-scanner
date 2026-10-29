"""Samenvoegen en opschonen, van begin tot eind (verzonnen cv's):
- oud cv (alleen bachelor, '2010 - heden' bij Rivierdata) en nieuw cv (master, Rivierdata afgesloten, nieuwe baan),
  zelfde mailadres, ander telefoonnummer -> 1 kandidaat;
- het nieuwste cv (recentste loopbaan) wordt getoond, ook als het oude bestand later op de pc kwam;
- 'Opschonen': alleen het nieuwste cv in Alle_cvs; oudere versie en exacte kopie weg (kopie in cv_archief);
- 'Terugzetten' zet alles terug."""
import csv, os, shutil, sys, tempfile, time
from pathlib import Path
os.environ.setdefault('CV_AI', '0')
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import cv_crawler as c  # noqa: E402
fout = []
def check(n, ok, info=''):
    print(('OK   ' if ok else 'FOUT ') + n + ('' if ok else f'  -> {info}'))
    if not ok: fout.append(n)
OUD = ('Curriculum Vitae\nSevda Kaya\nsevda.kaya@gmail.com\n06 11223344\nWoonplaats: Utrecht\nWerkervaring\n'
       '2010 - heden Data analist bij Rivierdata. Verantwoordelijk voor rapportages.\n'
       'Opleiding\n2006 - 2010 HBO Bedrijfskunde, Hogeschool Utrecht')
NIEUW = ('Curriculum Vitae\nSevda Kaya\nsevda.kaya@gmail.com\n06 99887766\nWoonplaats: Utrecht\nWerkervaring\n'
         '2016 - heden Data scientist bij Polderlogic. Verantwoordelijk voor modellen.\n'
         '2010 - 2016 Data analist bij Rivierdata. Verantwoordelijk voor rapportages.\n'
         'Opleiding\n2010 - 2012 Master Data Science, Universiteit Utrecht')
src, uit = Path(tempfile.mkdtemp()), Path(tempfile.mkdtemp())
(src / 'nieuw').mkdir(); (src / 'oud').mkdir(); (src / 'kopie').mkdir()
(src / 'nieuw' / 'CV Sevda Kaya 2024.txt').write_text(NIEUW, encoding='utf-8')
(src / 'kopie' / 'CV Sevda Kaya 2024.txt').write_text(NIEUW, encoding='utf-8')      # exact dezelfde inhoud
time.sleep(1.1)
(src / 'oud' / 'CV Sevda Kaya.txt').write_text(OUD, encoding='utf-8')               # oud cv, maar later op de pc
c.main(['--root', str(src), '--output', str(uit)])
P = list(csv.DictReader(open(uit / 'unieke_personen.csv', encoding='utf-8-sig'), delimiter=';'))
sevda = [p for p in P if 'Sevda' in p['naam']]
check('1 kandidaat (oud + nieuw cv samengevoegd)', len(sevda) == 1, [p['naam'] for p in sevda])
B = list(csv.DictReader(open(uit / 'cv_bestanden.csv', encoding='utf-8-sig'), delimiter=';'))
cv_nieuw = {b['cv_id'] for b in B if '2024' in b['oorspronkelijk_bestand']}
check('getoond cv = het nieuwste (recentste loopbaan)', sevda and sevda[0]['cv_id'] in cv_nieuw, (sevda, cv_nieuw))
c.main(['--output', str(uit), '--opschonen'])
alle = [p for p in (uit / 'Alle_cvs').rglob('*') if p.is_file()]
check('Alle_cvs: alleen het nieuwste cv', len(alle) == 1 and alle[0].read_text(encoding='utf-8') == NIEUW,
      [str(p.relative_to(uit)) for p in alle])
check('oude versie weg van de oude plek', not (src / 'oud' / 'CV Sevda Kaya.txt').exists())
check('exacte kopie weg', not (src / 'kopie' / 'CV Sevda Kaya 2024.txt').exists() or not (src / 'nieuw' / 'CV Sevda Kaya 2024.txt').exists())
check('oude versie bewaard in cv_archief', any(p.read_text(encoding='utf-8') == OUD for p in (uit / 'cv_archief').glob('*.txt')))
c.main(['--output', str(uit), '--terugzetten'])
check('terugzetten: oude versie weer terug', (src / 'oud' / 'CV Sevda Kaya.txt').exists())
check('terugzetten: beide kopieën weer terug', (src / 'kopie' / 'CV Sevda Kaya 2024.txt').exists()
      and (src / 'nieuw' / 'CV Sevda Kaya 2024.txt').exists())
shutil.rmtree(src, ignore_errors=True); shutil.rmtree(uit, ignore_errors=True)
print('OPSCHONEN-DUBBEL OK' if not fout else f'{len(fout)} FOUT(EN)'); sys.exit(1 if fout else 0)
