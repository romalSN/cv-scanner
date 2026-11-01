"""2000 cv-links in Excel: allemaal geldig, relatief naast de Excel, niets extra gekopieerd.
Excel op de Mac loopt vast als elke rij een file:// naar het origineel (vaak iCloud) is.
De link mag het bestand pas openen bij een klik."""
import os
import sys
import tempfile
import time
import urllib.parse
import zipfile
from pathlib import Path

sys.stdout.reconfigure(encoding='utf-8', errors='replace')
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ['CV_AI'] = '0'
import cv_crawler as c

FOUTEN = []


def check(naam, ok, extra=None):
    print(('OK   ' if ok else 'FOUT ') + naam)
    if not ok:
        FOUTEN.append(naam if extra is None else f'{naam} -> {extra}')


def hyperlinks(xlsx):
    with zipfile.ZipFile(xlsx) as z:
        rels = z.read('xl/worksheets/_rels/sheet1.xml.rels').decode('utf-8')
    targets = []
    for deel in rels.split('Target="')[1:]:
        targets.append(deel.split('"', 1)[0])
    return targets


def main():
    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp) / 'export'
        archief = out / 'cv_archief'
        alle = out / 'Alle_cvs'
        buiten = Path(tmp) / 'origineel_niet_aanraken'
        archief.mkdir(parents=True)
        alle.mkdir()
        buiten.mkdir()
        n = 2000
        for i in range(n):
            (archief / f'{i}.pdf').write_bytes(b'%PDF-1.1')
        (alle / 'lotte.pdf').write_bytes(b'%PDF-1.1')
        los = buiten / 'los.pdf'
        los.write_bytes(b'%PDF-1.1')
        voor = sum(1 for p in out.rglob('*') if p.is_file())
        oud = c.OUT_DIR
        c.OUT_DIR = out
        try:
            mensen = []
            for i in range(n):
                mensen.append(dict(
                    naam=f'Persoon {i}', persoon_id=f'p{i}', kandidaat_id=f'SNK-{i:05d}',
                    cv_id=f'SNCV-{i:05d}', email='', telefoon='', linkedin='',
                    cv_bestand='', cv_pad=str(buiten / f'{i}.pdf'),
                    cv_archief=str(archief / f'{i}.pdf'),
                    huidige_functie='', laatst_bekende_werkgever=''))
            t0 = time.perf_counter()
            rijen, _kol = c.personen_overzicht(mensen)
            xlsx = out / 'links.xlsx'
            c.xlsx_export(xlsx, [('Unieke personen', _kol, rijen)])
            duur = time.perf_counter() - t0
        finally:
            c.OUT_DIR = oud
        na = sum(1 for p in out.rglob('*') if p.is_file())
        # De xlsx zelf is het enige nieuwe bestand. Geen tweede kopie van de 2000 cv's.
        check('2000 links maken kopieert geen cv\'s', na == voor + 1, (voor, na, duur))
        check('origineel buiten de exportmap blijft onaangeroerd', los.read_bytes() == b'%PDF-1.1')
        check('niet-bestaande originelen zijn niet alsnog aangemaakt', not (buiten / '0.pdf').exists())
        doelen = hyperlinks(xlsx)
        check('precies 2000 cv-links', len(doelen) == n, len(doelen))
        check('geen file:// naar een map buiten de Excel', all(not d.startswith('file:') for d in doelen), doelen[:2])
        kapot = []
        for doel in doelen:
            pad = out / urllib.parse.unquote(doel)
            if not pad.is_file():
                kapot.append(doel)
        check('elke cv-link wijst naar een bestaand bestand naast de Excel', not kapot, kapot[:3])
        c.OUT_DIR = out
        try:
            naast = c.excel_cv_link(str(alle / 'lotte.pdf'), '')
            los_link = c.excel_cv_link(str(los), '')
        finally:
            c.OUT_DIR = oud
        check('cv in Alle_cvs blijft een relatieve link', naast == 'Alle_cvs/lotte.pdf', naast)
        check('bestand buiten de exportmap blijft een file-link, zonder kopie',
              los_link.startswith('file:') and 'los.pdf' in urllib.parse.unquote(los_link)
              and not (out / 'los.pdf').exists(), los_link)


main()
print('EXCEL-LINKS OK' if not FOUTEN else f'{len(FOUTEN)} FOUT(EN): ' + ', '.join(FOUTEN))
sys.exit(1 if FOUTEN else 0)
