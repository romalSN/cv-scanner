"""Opent het venster 'Kandidaten' echt (Windows/Mac op GitHub) en controleert wat je ziet."""
import json
import subprocess
import sys
import tempfile
import traceback
from pathlib import Path
import tkinter as tk
from tkinter import ttk

here = Path(__file__).resolve().parent
root_dir = here.parent
sys.path.insert(0, str(root_dir))
import gui

sys.stdout.reconfigure(encoding="utf-8", errors="replace")


def find(w, kind):
    if isinstance(w, kind):
        return w
    for c in w.winfo_children():
        f = find(c, kind)
        if f is not None:
            return f


def main():
    with tempfile.TemporaryDirectory() as out:
        subprocess.run([sys.executable, str(root_dir / 'cv_crawler.py'), '--root', str(here / 'voorbeeld'),
                        '--output', out], capture_output=True, check=True)
        data = json.loads((Path(out) / 'kandidaten.json').read_text(encoding='utf-8'))
        pad = data['rijen'][0]['open_cv']['pad']
        assert Path(pad).exists(), f'Open cv wijst naar niet-bestaand bestand: {pad}'

        root = tk.Tk()
        app = gui.ScannerApp(root)
        app.output = Path(out)
        opened = []
        app.open_path = lambda p: opened.append(p)
        app.open_candidates()
        root.update()
        win = [w for w in root.winfo_children() if isinstance(w, tk.Toplevel)][-1]
        tree = find(win, ttk.Treeview)
        rows = tree.get_children()
        assert len(rows) == 1, f'{len(rows)} rijen i.p.v. 1'
        heads = [tree.heading(c)['text'] for c in tree['columns']]
        assert not any('ID' in h.upper().split() or '-ID' in h.upper() for h in heads), f'ID-kolom zichtbaar: {heads}'
        vals = [str(v) for v in tree.item(rows[0])['values']]
        assert 'Lotte Verbeek' in vals, vals
        detail = find(win, tk.Text).get('1.0', 'end')
        assert 'SNK-' in detail and 'SNCV-' in detail, f'ID niet in detailvak: {detail[:200]}'
        buttons = []
        def collect(w):
            if isinstance(w, tk.Button):
                buttons.append(w)
            for c in w.winfo_children():
                collect(c)
        collect(win)
        [b for b in buttons if b.cget('text') == 'Open cv'][0].invoke()
        root.update()
        assert opened == [pad], f'Open cv opende {opened}'
        root.destroy()
        print('KANDIDATEN OK  kolommen:', heads)


try:
    main()
except Exception:
    tb = traceback.format_exc().strip().splitlines()
    print(traceback.format_exc())
    print('::error::Kandidaten-venster: ' + ' | '.join(tb[-3:]))
    sys.exit(1)
