"""Snelle test op de bouwmachine: leest een tekst-cv en maakt een Excel."""
import subprocess, sys, tempfile
from pathlib import Path

here = Path(__file__).resolve().parent
root = here.parent
sys.path.insert(0, str(root))
import os
os.environ['CV_AI'] = '0'
import cv_crawler as c

text, note, _ = c.read_document(here / 'voorbeeld' / 'CV Lotte Verbeek.txt')
assert 'Lotte Verbeek' in text, text[:200]
assert c.phrase_like('software engineering', 'sofware engineer')
assert not c.phrase_like('consultant', 'consulent')

with tempfile.TemporaryDirectory() as out:
    proc = subprocess.run([sys.executable, str(root / 'cv_crawler.py'), '--root', str(here / 'voorbeeld'),
                           '--output', out], capture_output=True, text=True, encoding='utf-8', errors='replace')
    print(proc.stdout[-2000:], proc.stderr[-2000:])
    assert 'CV GEVONDEN' in proc.stdout
    assert list(Path(out).glob('*.xlsx'))
    assert (Path(out) / 'kandidaten.json').exists()
    duplicate_log = Path(out) / 'dubbele_kandidaten_controle.csv'
    assert duplicate_log.is_file()
    assert 'kandidaat_id_1' in duplicate_log.read_text(encoding='utf-8-sig')

    # GUI toont nooit een oude Excel als een nieuwe export mislukt.
    import json
    import gui
    outdir = Path(out)
    excel = gui.geldige_excel_export(outdir)
    assert excel is not None and excel.name == 'CV_Crawler_Resultaten.xlsx'
    assert (outdir / gui.EXCEL_READY_MARKER).exists()
    marker = outdir / gui.EXCEL_READY_MARKER
    marker.unlink()
    assert gui.geldige_excel_export(outdir) is None
    # Oude Excel en oude tekstpointer bestaan nog, maar mogen niet openen.
    (outdir / 'laatste_excel.txt').write_text(str(excel), encoding='utf-8')
    assert excel.exists() and gui.geldige_excel_export(outdir) is None
    # Een valse stempel met verkeerd formaat/vingerafdruk wordt afgewezen.
    marker.write_text(json.dumps(dict(schema=1, filename=excel.name, bytes=0,
                                      mtime_ns=0)), encoding='utf-8')
    assert gui.geldige_excel_export(outdir) is None
    # Een opnieuw geslaagde export zet de stempel terug.
    process = subprocess.run([sys.executable, str(root / 'cv_crawler.py'), '--export-only',
                              '--output', out], capture_output=True, text=True,
                             encoding='utf-8', errors='replace')
    assert process.returncode == 0, process.stdout[-2000:] + process.stderr[-2000:]
    assert gui.geldige_excel_export(outdir) is not None
    # Verwijderde/verplaatste bestanden mogen nooit meer als nieuwe Excel openen.
    marker.unlink()
    assert gui.geldige_excel_export(outdir) is None
print('BASIS OK')
