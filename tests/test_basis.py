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
print('BASIS OK')
