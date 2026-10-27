"""Mac: oude Word-bestanden met een verkeerde extensie (.docx/.pdf) moeten toch gelezen worden."""
import shutil, subprocess, sys, tempfile
from pathlib import Path

root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(root))
import cv_crawler as c

if sys.platform != 'darwin':
    print('Mac-leestest overgeslagen'); sys.exit(0)
fout = []
with tempfile.TemporaryDirectory() as tmp:
    txt = Path(tmp) / 'cv.txt'
    txt.write_text('Curriculum Vitae\nHenk Winters\nhenk.winters@voorbeeldmail.nl\nWerkervaring\n2018 - heden Ontwikkelaar', encoding='utf-8')
    subprocess.run(['textutil', '-convert', 'doc', str(txt), '-output', str(Path(tmp) / 'cv.doc')], check=True)
    for naam in ('cv.doc', 'nep.docx', 'nep.pdf'):
        target = Path(tmp) / naam
        if naam != 'cv.doc':
            shutil.copyfile(Path(tmp) / 'cv.doc', target)
        try:
            text, note, _ = c.read_document(target)
            ok = 'Henk Winters' in text
        except Exception as exc:
            text, note, ok = '', f'{type(exc).__name__}: {exc}', False
        print(naam, ok, note)
        if not ok:
            fout.append(f'{naam}: {note[:200]}')
if fout:
    print('::error::Mac lezen: ' + ' | '.join(fout)); sys.exit(1)
print('MAC LEZEN OK')
