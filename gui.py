#!/usr/bin/env python3
"""Gebruiksvriendelijk Windows-venster voor de lokale CV-crawler.

Tkinter-interface. De scan draait als afzonderlijk proces, zodat het venster
responsief blijft. Geen CV-data wordt online verzonden bij normaal gebruik.
"""
from __future__ import annotations

import ast
import datetime
import json
import os
from pathlib import Path
import queue
import re
import sqlite3
import unicodedata
import zipfile
import shutil
import signal
import subprocess
import sys
import threading
import tkinter as tk
from tkinter import filedialog, messagebox, simpledialog, ttk

APP_TITLE = 'CV Scanner — SolutionNow'
FROZEN = getattr(sys, 'frozen', False)   # draait als .exe of .app
HOME = Path.home()
OUTPUT = HOME / 'CV_Crawler_Export'
RUNTIME = HOME / '.cv-scanner-windows'
RESOURCES = Path(getattr(sys, '_MEIPASS', Path(__file__).resolve().parent))
BACKEND = RESOURCES / 'cv_crawler.py'

BACKGROUND = '#ffffff'
PANEL = '#ffffff'
# Huisstijl SolutionNow.nl: wit, lichtblauw (#70d3f0 / #00aeef), donkerblauw (#023a51).
TEXT = '#023a51'
MUTED = '#5b6b73'
ACCENT = '#00aeef'
ACCENT_HOVER = '#0096cf'
NAVY = '#023a51'
SKY = '#70d3f0'
LINE = '#e3edf2'
CARD = '#f2fafd'
OK = '#1a7f37'
WARN = '#b54708'
ERR = '#b42318'
INFO = '#1f5fa8'
FONT = 'Helvetica Neue' if sys.platform == 'darwin' else 'Segoe UI'
MONO = 'Menlo' if sys.platform == 'darwin' else 'Consolas'

HELP_DEEP = ('Zoekt ook in verborgen en systeemachtige mappen, zoals AppData en mappen '
             'die met een punt beginnen (.cache, .vscode).\n\n'
             'Zet aan als je cv\'s via Outlook, WhatsApp Desktop of Teams hebt ontvangen: '
             'bijlagen die je daar opende, worden vaak in AppData bewaard.\n\n'
             'Let op: de scan duurt dan flink langer.')
HELP_CLOUD = ('Cv\'s die alleen online in OneDrive of iCloud staan (wolkje-icoon), worden '
              'tijdens de scan gedownload en gelezen. Internet nodig; de scan duurt langer.\n\n'
              'Uit: die bestanden worden overgeslagen en in het log genoemd.\n\n'
              'Bij Cv\'s opschonen kies je zelf: in de cloud laten staan (alleen een kopie) of weghalen.')
HELP_USB = ('Zoekt ook op aangesloten USB-sticks, externe harde schijven en netwerkschijven.\n\n'
            'Zet aan als je cv\'s op een stick of gedeelde schijf bewaart. '
            'De schijf moet tijdens de scan aangesloten zijn.')
HELP_OCR = ('Leest ook ingescande PDF\'s: een foto of scan van papier, zonder tekst die je kunt selecteren.\n\n'
            'Zet aan als kandidaten hun cv als scan hebben gestuurd.\n\n'
            'Gebruikt de ingebouwde OCR van Windows (of macOS): je hoeft niets te installeren. '
            'Alleen pdf\'s zonder tekst worden zo gelezen, max. 5 pagina\'s per pdf. De scan wordt er trager van.')


HELP_ONLINE = ('Vult ontbrekende LinkedIn, e-mail en telefoon aan. Alleen voor wie iets mist.\n\n'
               '1. Gratis: eigen website, portfolio of GitHub die in het cv staat → contactpagina uitlezen.\n'
               '2. LinkedIn zoeken op naam + functie uit het cv (1 credit).\n'
               '3. Eigen/ZZP-site, GitHub en freelanceprofielen zoeken als e-mail of telefoon ontbreekt (1 credit).\n'
               'Facebook, Instagram, TikTok en X worden nooit doorzocht.\n\n'
               'Alleen zekere treffers komen in de kolommen online_…; twijfelgevallen staan in tabblad '
               '"Online gevonden". Iemand die al is opgezocht kost geen credits meer.')
HELP_CONTACT = ('Zoekt bij online aanvullen ook apart naar telefoon en e-mail als één van de twee ontbreekt.\n\n'
                'Kost 1 extra credit per persoon. Telefoonnummers van gewone kandidaten staan zelden online '
                '(LinkedIn toont ze bijna nooit), dus verwacht weinig resultaat.\n\n'
                'Uit: er wordt alleen naar contactgegevens gezocht als e-mail ÉN telefoon allebei ontbreken.')
KEY_FILE = RUNTIME / 'tavily_sleutel.txt'


def load_key():
    try:
        return KEY_FILE.read_text(encoding='utf-8').strip()
    except OSError:
        return ''


SERPER_FILE = RUNTIME / 'serper_sleutel.txt'
SERPAPI_FILE = RUNTIME / 'serpapi_sleutel.txt'
APIFY_FILE = RUNTIME / 'apify_sleutel.txt'


def load_apify():
    try:
        return APIFY_FILE.read_text(encoding='utf-8').strip()
    except OSError:
        return ''


def heeft_zoeksleutel():
    """Online zoeken kan met Apify, Serper, SerpApi of Tavily (minstens één)."""
    return any(f() for f in (load_apify, load_serper, load_serpapi, load_key))


def load_serpapi():
    try:
        return SERPAPI_FILE.read_text(encoding='utf-8').strip()
    except OSError:
        return ''


def load_serper():
    try:
        return SERPER_FILE.read_text(encoding='utf-8').strip()
    except OSError:
        return ''


def save_serper(key):
    RUNTIME.mkdir(parents=True, exist_ok=True)
    SERPER_FILE.write_text(key.strip(), encoding='utf-8')
    try:
        os.chmod(SERPER_FILE, 0o600)
    except OSError:
        pass


def save_key(key):
    RUNTIME.mkdir(parents=True, exist_ok=True)
    KEY_FILE.write_text(key.strip(), encoding='utf-8')
    try:
        os.chmod(KEY_FILE, 0o600)
    except OSError:
        pass


SLEUTELBESTAND = 'cv-scanner-sleutels.txt'
SLEUTEL_SOORTEN = {'tavily': KEY_FILE, 'serper': SERPER_FILE, 'serpapi': SERPAPI_FILE, 'apify': APIFY_FILE}


def _sleutelbestand_plekken():
    """Waar kan het sleutelbestand staan? Naast de app (in de uitgepakte map), in Downloads of op het Bureaublad
    (ook één map dieper, bv. de uitgepakte zip-map)."""
    plekken = []
    exe = Path(sys.executable).resolve()
    for p in [exe.parent, *exe.parents[:4]]:
        plekken.append(p)
    for basis in (HOME / 'Downloads', HOME / 'Desktop', HOME / 'Bureaublad'):
        plekken.append(basis)
        try:
            plekken += [d for d in basis.iterdir() if d.is_dir() and 'scanner' in d.name.lower()]
        except OSError:
            pass
    return plekken


def sleutels_importeren():
    """Staat er een 'cv-scanner-sleutels.txt' (tavily=…, serper=…, serpapi=…)? Dan de sleutels op de vaste plek
    zetten (dezelfde map als wanneer je ze zelf invult) en het bestand weghalen. Geeft de namen van de
    geïnstalleerde sleutels terug (nooit de sleutels zelf)."""
    for map_ in _sleutelbestand_plekken():
        f = map_ / SLEUTELBESTAND
        try:
            if not f.is_file():
                continue
            regels = f.read_text(encoding='utf-8-sig', errors='replace').splitlines()
        except OSError:
            continue
        gezet = []
        for r in regels:
            if '=' not in r:
                continue
            soort, waarde = (x.strip() for x in r.split('=', 1))
            soort = soort.lower()
            if soort in SLEUTEL_SOORTEN and waarde:
                RUNTIME.mkdir(parents=True, exist_ok=True)
                SLEUTEL_SOORTEN[soort].write_text(waarde, encoding='utf-8')
                try:
                    os.chmod(SLEUTEL_SOORTEN[soort], 0o600)
                except OSError:
                    pass
                gezet.append(soort)
        if gezet:
            try:
                f.unlink()          # niet laten slingeren in Downloads
            except OSError:
                pass
            return gezet
    return []


class Tooltip:
    """Uitlegvakje dat verschijnt als je met de muis over een element gaat."""
    def __init__(self, widget, text):
        self.widget, self.text, self.tip = widget, text, None
        widget.bind('<Enter>', self.show, add='+')
        widget.bind('<Leave>', self.hide, add='+')

    def show(self, _event=None):
        if self.tip:
            return
        x = self.widget.winfo_rootx() + 10
        y = self.widget.winfo_rooty() + self.widget.winfo_height() + 6
        self.tip = tk.Toplevel(self.widget)
        self.tip.wm_overrideredirect(True)
        self.tip.wm_geometry(f'+{x}+{y}')
        tk.Label(self.tip, text=self.text, justify='left', wraplength=380,
                 bg=NAVY, fg='#ffffff', font=(FONT, 9), padx=12, pady=9).pack()
        # Binnen het venster/scherm houden.
        self.tip.update_idletasks()
        right = min(self.widget.winfo_toplevel().winfo_rootx() + self.widget.winfo_toplevel().winfo_width(),
                    self.widget.winfo_screenwidth())
        x = max(0, min(x, right - self.tip.winfo_width() - 10))
        self.tip.wm_geometry(f'+{x}+{y}')

    def hide(self, _event=None):
        if self.tip:
            self.tip.destroy()
            self.tip = None

def list_twijfel(output: Path) -> list[dict]:
    """Zelfde selectie als het venster Twijfelgevallen: unieke sha, huidige herkenning, laatste scan."""
    dbp = output / 'cv_index.sqlite'
    if not dbp.exists():
        return []
    con = sqlite3.connect(str(dbp))
    con.row_factory = sqlite3.Row
    try:
        ver = con.execute('SELECT MAX(classifier_version) FROM docs').fetchone()[0] or 0
        try:
            start = json.loads((output / 'laatste_scan.json').read_text(encoding='utf-8'))['start_ns']
        except (OSError, ValueError, KeyError):
            start = 0
        cols_now = {c[1] for c in con.execute('PRAGMA table_info(docs)')}
        seen_col = 'gezien_ns' if 'gezien_ns' in cols_now else '0 AS gezien_ns'
        rows = con.execute(f"SELECT path, name, reason, note, sha, archive, status, {seen_col} FROM docs WHERE status='review' "
                           "AND classifier_version=? ORDER BY path", (ver,)).fetchall()
    finally:
        con.close()
    seen, items = set(), []
    for r in rows:
        if start and (r['gezien_ns'] or 0) < start:
            continue
        if r['sha'] and r['sha'] not in seen:
            seen.add(r['sha'])
            items.append(dict(r))
    return items


def parse_progress(line: str) -> dict[str, str]:
    """Bepaalt de status uit de voortgangsregels van de crawler."""
    output: dict[str, str] = {}
    progress = re.search(r'(\d+) documenten gezien\s*\|\s*(\d+) CV.s\s*\|\s*(\d+) twijfel\s*\|\s*(\d+) fouten', line)
    if progress:
        output.update(zip(('documenten', 'cvs', 'twijfel'), progress.groups()[:3]))
    exported = re.search(r'EXPORT:\s*(\d+) unieke persoonsrecords,\s*(\d+) CV-bestandslocaties,\s*(\d+) controlepunten', line)
    if exported:
        output.update(zip(('personen', 'cvs', 'controles'), exported.groups()))
    total = re.match(r'TOTAAL:\s*(\d+)', line)
    if total:
        output['totaal'] = total.group(1)
    control = re.match(r'EXPORT-CONTROLE:\s*(.*)', line)
    if control:
        output['fouten'] = str(sum(int(n) for n in re.findall(r'(\d+) [A-Za-z]', control.group(1))))
    twijfel_end = re.search(r'TWIJFEL-OVERZICHT \((\d+) documenten', line)
    if twijfel_end:
        output['twijfel'] = twijfel_end.group(1)
    if line.startswith('Scan afgerond:'):
        try:
            summary = ast.literal_eval(line.split(':', 1)[1].strip())
            if isinstance(summary, dict):
                for src, dst in (('gevonden_ondersteund', 'documenten'),
                                 ('review', 'twijfel')):
                    if src in summary:
                        output[dst] = str(summary[src])
        except (ValueError, SyntaxError):
            pass
    return output


def choose_python() -> str:
    """Gebruik de base interpreter die de GUI draait voor de eigen venv."""
    return sys.executable


def prepare_runtime(post, stopping: threading.Event) -> Path:
    """Gebruik lokaal meegeleverde PDF-/RTF-lezer; geen pip of internet nodig."""
    if stopping.is_set():
        raise InterruptedError('Geannuleerd vóór de start.')
    if not FROZEN and (not (RESOURCES / 'pypdf' / '__init__.py').exists() or not (RESOURCES / 'reader_worker.py').exists()):
        raise RuntimeError('Het pakket is onvolledig uitgepakt. Pak het volledige ZIP-bestand uit en probeer opnieuw.')
    post('log', 'PDF- en RTF-lezers zitten al in de download. Geen installatie of internet nodig.')
    return Path(sys.executable)


EXCEL_READY_MARKER = 'excel_export_geldig.json'


def geldige_excel_export(output):
    """Open alleen een aantoonbaar volledig gemaakte Excel uit de huidige export.
    Geen fallback naar oude xlsx-bestanden of verouderde laatste_excel.txt.
    """
    try:
        out = Path(output).resolve()
        meta = json.loads((out / EXCEL_READY_MARKER).read_text(encoding='utf-8'))
        if meta.get('schema') != 1:
            return None
        name = meta.get('filename')
        if not isinstance(name, str) or not re.fullmatch(
                r'CV_Crawler_Resultaten(?:_\d{4}-\d{2}-\d{2}_\d{6})?\.xlsx', name):
            return None
        p = out / name
        if not p.is_file() or p.is_symlink() or p.resolve().parent != out:
            return None
        stat = p.stat()
        if stat.st_size < 300 or stat.st_size != meta.get('bytes') or stat.st_mtime_ns != meta.get('mtime_ns'):
            return None
        # Een oud/afgebroken bestand kan een .xlsx-extensie hebben maar geen Excel zijn.
        with zipfile.ZipFile(p) as z:
            if 'xl/workbook.xml' not in z.namelist() or z.testzip() is not None:
                return None
        return p
    except (OSError, ValueError, KeyError, TypeError, zipfile.BadZipFile):
        return None


class ScannerApp:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.root.title(APP_TITLE)
        self.root.geometry('1000x900')
        self.root.minsize(900, 760)
        self.root.configure(bg=BACKGROUND)
        self.events: queue.Queue[tuple[str, object]] = queue.Queue()
        self.stopping = threading.Event()
        self.open_when_ready = False
        self.open_list_when_ready = False
        self.running = False
        self.blocked = []
        self.process: subprocess.Popen | None = None
        self.output = OUTPUT
        self.custom_root: Path | None = None
        self.deep = tk.BooleanVar(value=False)
        self.volumes = tk.BooleanVar(value=False)
        self.cloud = tk.BooleanVar(value=False)
        self.ocr = tk.BooleanVar(value=False)
        self.online_fill = tk.BooleanVar(value=bool(load_key()))
        self.online_contact = tk.BooleanVar(value=False)
        self.status = tk.StringVar(value='Klaar om je ' + ('Mac' if sys.platform == 'darwin' else 'pc') + ' te doorzoeken')
        self.source_label = tk.StringVar(value='Alle toegankelijke interne schijven, inclusief OneDrive')
        self.metrics = {
            'documenten': tk.StringVar(value='0'),
            'cvs': tk.StringVar(value='0'),
            'personen': tk.StringVar(value='—'),
            'twijfel': tk.StringVar(value='0'),
            'fouten': tk.StringVar(value='—'),
        }
        self.current = tk.StringVar(value='')
        self.badge_text = tk.StringVar(value='PDF-lezer wordt gecontroleerd…')
        self.search_text = tk.StringVar()
        self.total = 0
        self.done = 0
        self.link_count = 0
        self.configure_styles()
        self.render()
        self.root.protocol('WM_DELETE_WINDOW', self.close)
        self.root.after(120, self.drain_events)
        threading.Thread(target=self.check_pdf_reader, daemon=True).start()

    def check_pdf_reader(self):
        """Toon bovenaan of de volledige PDF-lezer (cryptography) werkt."""
        if FROZEN:
            self.emit('badge', True)
            return
        try:
            probe = subprocess.run([sys.executable, '-c',
                'from cryptography.hazmat.primitives.ciphers import Cipher'],
                capture_output=True, timeout=60,
                creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
            ok = probe.returncode in (0, 1)
        except (OSError, subprocess.TimeoutExpired):
            ok = False
        self.emit('badge', ok)

    def configure_styles(self):
        style = ttk.Style(self.root)
        try:
            style.theme_use('clam')
        except tk.TclError:
            pass
        style.configure('Scanner.Horizontal.TProgressbar', troughcolor='#e3e9ef', background=ACCENT,
                        bordercolor='#e3e9ef', lightcolor=ACCENT, darkcolor=ACCENT, thickness=12)

    def panel(self, parent, title, pady=(0, 12)):
        frame = tk.Frame(parent, bg=PANEL, highlightbackground=LINE, highlightthickness=1, padx=20, pady=16)
        frame.pack(fill='x', pady=pady)
        tk.Label(frame, text=title, font=(FONT, 13, 'bold'), fg=TEXT, bg=PANEL).pack(anchor='w')
        return frame

    def render(self):
        # Menubalk bovenaan: altijd zichtbaar, ook als het venster te klein is voor de links onderaan.
        menubar = tk.Menu(self.root)
        settings = tk.Menu(menubar, tearoff=0)
        settings.add_command(label='Tavily-sleutel instellen…', command=self.set_key)
        settings.add_command(label='Zoeksleutels (Apify/Serper/SerpApi)…', command=self.set_serper)
        settings.add_separator()
        settings.add_command(label='Nu online aanvullen', command=self.online)
        settings.add_command(label='Online gevonden controleren…', command=self.open_online_check)
        settings.add_command(label='Toegang en privacy', command=self.permissions)
        menubar.add_cascade(label='Instellingen', menu=settings)
        self.root.config(menu=menubar)
        header = tk.Frame(self.root, bg='#ffffff', padx=26, pady=14)
        header.pack(fill='x')
        tk.Frame(self.root, bg=SKY, height=3).pack(fill='x')
        left = tk.Frame(header, bg='#ffffff')
        left.pack(side='left')
        brand = tk.Frame(left, bg='#ffffff')
        brand.pack(anchor='w')
        import tkinter.font as tkfont
        f1, f2 = tkfont.Font(family=FONT, size=22), tkfont.Font(family=FONT, size=22, weight='bold')
        w1, w2, w3 = f1.measure('Solution'), f2.measure('Now'), f2.measure('   CV Scanner')
        logo = tk.Canvas(brand, width=w1 + w2 + w3 + 4, height=f1.metrics('linespace') + 4,
                         bg='#ffffff', highlightthickness=0, bd=0)
        logo.pack(side='left')
        logo.create_text(0, 2, text='Solution', font=f1, fill='#7d8b91', anchor='nw')
        logo.create_text(w1, 2, text='Now', font=f2, fill=ACCENT, anchor='nw')          # direct tegen 'Solution'
        logo.create_text(w1 + w2, 2, text='   CV Scanner', font=f2, fill=NAVY, anchor='nw')
        tk.Label(left, text='Vind verspreide cv’s · herken dubbele personen · één Excel-overzicht',
                 font=(FONT, 10), fg=MUTED, bg='#ffffff').pack(anchor='w')
        self.badge = tk.Label(header, textvariable=self.badge_text, font=(FONT, 9, 'bold'),
                              fg='#ffffff', bg='#5f7487', padx=12, pady=5)
        self.badge.pack(side='right')

        outer = tk.Frame(self.root, bg=BACKGROUND, padx=26, pady=16)
        outer.pack(fill='both', expand=True)

        top = self.panel(outer, '1   Scannen')
        tk.Label(top, textvariable=self.source_label, wraplength=880, justify='left',
                 font=(FONT, 10), fg=MUTED, bg=PANEL).pack(anchor='w', pady=(2, 10))
        row = tk.Frame(top, bg=PANEL)
        row.pack(fill='x')
        self.all_button = self.button(row, '▶  Scan hele pc', self.scan_all, primary=True)
        self.all_button.pack(side='left', padx=(0, 8))
        self.folder_button = self.button(row, 'Kies één map', self.scan_folder)
        self.folder_button.pack(side='left', padx=(0, 18))
        for text, var, help_text in (('Verborgen mappen ⓘ', self.deep, HELP_DEEP),
                                     ('USB/netwerk ⓘ', self.volumes, HELP_USB),
                                     ('Ook cv\'s die alleen in OneDrive/iCloud staan ⓘ', self.cloud, HELP_CLOUD),
                                     ('OCR ⓘ', self.ocr, HELP_OCR),
                                     ('Online aanvullen ⓘ', self.online_fill, HELP_ONLINE)):
            box = tk.Checkbutton(row, text=text, variable=var, font=(FONT, 10), bg=PANEL, fg=TEXT,
                                 activebackground=PANEL, selectcolor='#ffffff', cursor='hand2',
                                 highlightthickness=0, bd=0)
            box.pack(side='left', padx=(0, 12))
            extra = ''
            if var is self.ocr:
                extra = ('\n\nOp deze pc: Tesseract gevonden (wordt gebruikt).' if shutil.which('tesseract')
                         else '\n\nOp deze pc: ingebouwde Windows-OCR wordt gebruikt.' if sys.platform == 'win32'
                         else '\n\nOp deze Mac: ingebouwde macOS-OCR wordt gebruikt.')
            if var is self.online_fill:
                box.configure(command=self.toggle_online)
            Tooltip(box, help_text + extra)

        prog = self.panel(outer, '2   Voortgang')
        tk.Label(prog, textvariable=self.status, font=(FONT, 10, 'bold'), fg=TEXT, bg=PANEL,
                 anchor='w').pack(fill='x', pady=(4, 0))
        tk.Label(prog, textvariable=self.current, font=(FONT, 9), fg=MUTED, bg=PANEL,
                 anchor='w').pack(fill='x', pady=(0, 6))
        self.progress = ttk.Progressbar(prog, mode='determinate', style='Scanner.Horizontal.TProgressbar')
        self.progress.pack(fill='x', pady=(0, 12))
        cards = tk.Frame(prog, bg=PANEL)
        cards.pack(fill='x')
        for j, (key, label, color) in enumerate((
                ('documenten', 'Bestanden gezien', TEXT), ('cvs', 'Cv-locaties', ACCENT),
                ('personen', 'Unieke personen', TEXT), ('twijfel', 'Twijfel', WARN),
                ('fouten', 'Niet gelezen', ERR))):
            holder = tk.Frame(cards, bg=ACCENT)
            holder.grid(row=0, column=j, padx=(0 if j == 0 else 6, 0), sticky='nsew')
            cards.grid_columnconfigure(j, weight=1)
            card = tk.Frame(holder, bg=CARD, padx=12, pady=10)
            card.pack(fill='both', expand=True, padx=(4, 0))
            tk.Label(card, textvariable=self.metrics[key], bg=CARD, fg=color,
                     font=(FONT, 20, 'bold')).pack(anchor='w')
            tk.Label(card, text=label, bg=CARD, fg=MUTED, font=(FONT, 9)).pack(anchor='w')
        actions = tk.Frame(prog, bg=PANEL)
        actions.pack(fill='x', pady=(14, 0))
        self.excel_button = self.button(actions, 'Open Excel', self.open_excel, primary=True)
        self.excel_button.pack(side='left', padx=(0, 8))
        self.list_button = self.button(actions, '▦  Kandidaten', self.open_candidates)
        self.list_button.pack(side='left', padx=(0, 8))
        Tooltip(self.list_button, 'Overzicht van alle kandidaten in de scanner zelf. '
                                  'Dubbelklik op iemand om het cv te openen (zonder Excel).')
        self.result_button = self.button(actions, 'Resultatenmap', self.open_results)
        self.result_button.pack(side='left', padx=(0, 8))
        self.refresh_button = self.button(actions, '⟳  Excel bijwerken', self.refresh_export)
        self.refresh_button.pack(side='left', padx=(0, 8))
        self.doubt_button = self.button(actions, '?  Twijfelgevallen', self.open_doubts)
        self.doubt_button.pack(side='left', padx=(0, 8))
        self.confirm_button = self.button(actions, '✓  Online controleren', self.open_online_check)
        self.confirm_button.pack(side='left', padx=(0, 8))
        self.clean_button = self.button(actions, '▤  Cv\'s opschonen', self.start_cleanup)
        self.clean_button.pack(side='left', padx=(0, 8))
        self.undo_button = self.button(actions, '↩  Terugzetten', self.undo_cleanup)
        self.undo_button.pack(side='left', padx=(0, 8))
        self.stop_button = self.button(actions, '■  Stop', self.stop, enabled=False)
        self.stop_button.pack(side='right')

        logs = tk.Frame(outer, bg=PANEL, highlightbackground=LINE, highlightthickness=1, padx=20, pady=14)
        logs.pack(fill='both', expand=True)
        tk.Label(logs, text='Activiteit', font=(FONT, 13, 'bold'), fg=TEXT, bg=PANEL).pack(anchor='w', pady=(0, 6))
        search = tk.Frame(logs, bg=PANEL)
        search.pack(side='bottom', fill='x', pady=(10, 0))
        entry = tk.Entry(search, textvariable=self.search_text, font=(FONT, 11), relief='flat',
                         bg=CARD, fg=TEXT, insertbackground=TEXT, highlightthickness=1,
                         highlightbackground=LINE, highlightcolor=ACCENT)
        entry.pack(side='left', fill='x', expand=True, ipady=7, padx=(0, 8))
        entry.bind('<Return>', lambda _e: self.search())
        Tooltip(entry, 'Zoek in je gevonden cv\'s op naam, e-mail, telefoon, woonplaats of vaardigheid '
                       '(bijv. "python" of "Haarlem"). Alles blijft op je eigen pc.')
        self.search_button = self.button(search, 'Zoek in cv\'s', self.search)
        self.search_button.pack(side='left')
        body = tk.Frame(logs, bg=PANEL)
        body.pack(fill='both', expand=True)
        self.logbox = tk.Text(body, height=10, borderwidth=0, bg='#f8fafc', fg=TEXT,
                              font=(MONO, 10), wrap='word', relief='flat', padx=10, pady=8)
        scroll = ttk.Scrollbar(body, command=self.logbox.yview)
        self.logbox.configure(yscrollcommand=scroll.set)
        scroll.pack(side='right', fill='y')
        self.logbox.pack(side='left', fill='both', expand=True)
        for tag, color in (('ok', OK), ('warn', WARN), ('err', ERR), ('info', INFO), ('muted', MUTED)):
            self.logbox.tag_configure(tag, foreground=color)
        self.logbox.tag_configure('cv', foreground=OK, font=(MONO, 10, 'bold'))
        self.logbox.configure(state='disabled')

        foot = tk.Frame(outer, bg=BACKGROUND)
        foot.pack(fill='x', pady=(8, 0))
        for text, cmd in (('Toegang en privacy', self.permissions), ('Tavily-sleutel instellen', self.set_key),
                          ('Zoeksleutels (Apify/Serper/SerpApi)', self.set_serper),
                          ('Nu online aanvullen', self.online)):
            link = tk.Label(foot, text=text, font=(FONT, 9, 'underline'), fg=MUTED, bg=BACKGROUND, cursor='hand2')
            link.pack(side='left', padx=(0, 16))
            link.bind('<Button-1>', lambda _e, c=cmd: (None if self.running else c()))
        tk.Label(foot, text='Originele bestanden blijven onaangeroerd · volledig lokaal',
                 font=(FONT, 9), fg=MUTED, bg=BACKGROUND).pack(side='right')
        self.write_log('Klaar om te scannen. Kies ‘Scan hele pc’ of ‘Kies één map’.', 'muted')

    def button(self, parent, title, command, primary=False, enabled=True):
        return tk.Button(parent, text=title, command=command,
                         font=(FONT, 10, 'bold' if primary else 'normal'),
                         bg=ACCENT if primary else '#e8f7fd',
                         fg='#ffffff' if primary else NAVY,
                         activeforeground='#ffffff' if primary else NAVY,
                         activebackground=ACCENT_HOVER if primary else '#cdeefa',
                         disabledforeground='#9fb0c0',
                         highlightthickness=0 if primary else 1, highlightbackground=SKY,
                         padx=14, pady=7, relief='flat', bd=0, cursor='hand2',
                         state='normal' if enabled else 'disabled')

    @staticmethod
    def log_tag(msg):
        if msg.startswith('ONLINE GEVONDEN') and 'controleren' in msg:
            return 'warn'
        if msg.startswith(('CV GEVONDEN', 'ONLINE GEVONDEN')):
            return 'cv'
        if msg.startswith(('ONLINE FOUT', 'ONLINE zoekfout')):
            return 'err'
        if msg.startswith('ONLINE'):
            return 'info'
        if msg.startswith(('AI-MODEL', 'AI-CONTROLE', 'AI:')):
            return 'info'
        if ('WERKT NIET' in msg or 'LEESFOUT' in msg
                or msg.startswith(('FOUT', 'Traceback', 'SCAN ONDERBROKEN', 'PermissionError', 'File "'))):
            return 'err'
        if 'WERKT' in msg or msg.startswith(('Scan afgerond', 'EXPORT:', 'Excel:', 'Klaar')):
            return 'ok'
        if msg.startswith(('LET OP', 'Tip', 'WAARSCHUWING', 'EXPORT-CONTROLE', 'Bijgewerkt', 'Opgeruimd',
                           'OPGERUIMD', 'Bijwerken', 'Opnieuw gelezen', 'OCR', 'Herstel')):
            return 'warn'
        if msg.startswith('»'):
            return 'info'
        return ''

    def write_log(self, msg, tag=None, link=None):
        msg = str(msg).rstrip()
        tags = [tag if tag is not None else self.log_tag(msg.strip())]
        if link:
            self.link_count += 1
            name = f'link{self.link_count}'
            tags.append(name)
            self.logbox.tag_bind(name, '<Button-1>', lambda _e, p=link: self.open_path(p))
            self.logbox.tag_bind(name, '<Enter>', lambda _e: self.logbox.configure(cursor='hand2'))
            self.logbox.tag_bind(name, '<Leave>', lambda _e: self.logbox.configure(cursor=''))
        self.logbox.configure(state='normal')
        self.logbox.insert('end', msg + '\n', tuple(t for t in tags if t))
        self.logbox.see('end')
        self.logbox.configure(state='disabled')

    def open_path(self, path_str):
        """Open een gevonden bestand; bij een cv in een zip: open de zip (alleen bekijken)."""
        real = Path(str(path_str).split(' › ', 1)[0])
        if not real.exists():
            messagebox.showinfo('Niet gevonden', f'Dit bestand staat niet meer op die plek:\n{real}', parent=self.root)
        elif sys.platform == 'win32':
            os.startfile(str(real))
        elif sys.platform == 'darwin':
            subprocess.Popen(['open', str(real)])
        else:
            messagebox.showinfo('Bestand', str(real), parent=self.root)

    def search(self):
        query = self.search_text.get().strip()
        db_path = self.output / 'cv_index.sqlite'
        if not query or self.running:
            return
        if not db_path.exists():
            self.write_log('» Nog geen resultaten om in te zoeken. Scan eerst.', 'info')
            return
        words = [w for w in re.findall(r'[\w@.+-]+', query.lower()) if w]
        digits = re.sub(r'\D', '', query)
        try:
            con = sqlite3.connect(f'file:{db_path.as_posix()}?mode=ro', uri=True)
            con.row_factory = sqlite3.Row
            cols = {c[1] for c in con.execute('PRAGMA table_info(docs)')}
            rec = 'received_ns' if 'received_ns' in cols else '0 AS received_ns'
            rows = con.execute(f"SELECT path, name, status, emails, phones, snippet, year, mtime_ns, {rec} FROM docs "
                               "WHERE status IN ('cv','review') ORDER BY status, name").fetchall()
            con.close()
        except sqlite3.Error as exc:
            self.write_log(f'» Zoeken lukte niet: {exc}', 'err')
            return

        this_year = datetime.date.today().year
        # Kopie van hetzelfde bestand maakt een versie niet nieuwer: per inhoud het vroegste 'op de pc gekomen'.
        first_seen = {}
        for r in rows:
            key = (r['snippet'] or '')[:400]
            if r['received_ns'] and '/Alle_cvs/' not in r['path'].replace('\\', '/'):
                first_seen[key] = min(first_seen.get(key, r['received_ns']), r['received_ns'])

        def newness(r):
            """Nieuwste versie = laatst gewijzigd OF laatst op de pc gekomen (de recentste van de twee).
            Bij gelijke datum: hoogste jaartal in het cv, dan jaartal in de bestandsnaam."""
            years = [int(y) for y in re.findall(r'(?<!\d)(19[5-9]\d|20[0-3]\d)(?!\d)', r['snippet'] or '')
                     if int(y) <= this_year]
            return (max(r['mtime_ns'] or 0, first_seen.get((r['snippet'] or '')[:400], 0)), max(years) if years else 0,
                    r['year'] or '', ' › ' not in r['path'])

        def person_key(r):
            return (r['name'] or r['path']).lower()

        everyone = {}
        for r in rows:
            everyone.setdefault(person_key(r), []).append(r)
        def norm(t):
            t = unicodedata.normalize('NFKD', str(t or '').lower())
            return ' '.join(re.findall(r'[a-z0-9@.+]+', ''.join(c for c in t if not unicodedata.combining(c))))

        phrase = norm(query)
        patterns = [re.compile(r'(?<![a-z0-9])' + re.escape(norm(w)) + r'(?![a-z0-9])') for w in words if norm(w)]
        hits = []
        for r in rows:
            hay = ' ' + norm(' '.join(str(r[k] or '') for k in ('name', 'emails', 'snippet', 'path'))) + ' '
            phone_hit = len(digits) >= 6 and digits[-9:] in re.sub(r'\D', '', r['phones'] or '')
            # Hele woorden ('ict' vindt dus niet 'fictief'); meerdere woorden = allemaal aanwezig.
            if phone_hit or (patterns and all(p.search(hay) for p in patterns)):
                hits.append(r)
        if any(r['status'] == 'cv' for r in hits):
            hits = [r for r in hits if r['status'] == 'cv']   # twijfelgevallen alleen als er geen echte cv's zijn
        exact = [r for r in hits if phrase and phrase in norm(r['snippet'])]
        if exact:
            hits = exact   # precies de zin (bv. 'ict vaardigheden') gaat voor losse woorden
        people = {}
        for r in hits:
            people.setdefault(person_key(r), []).append(r)
        self.write_log('')
        self.write_log(f'» Zoeken naar “{query}”: {len(people)} persoon/personen', 'info')
        for key, matched in list(people.items())[:25]:
            versions = [r for r in everyone[key] if r['status'] == 'cv'] or everyone[key]
            newest = max(versions, key=newness)
            label = 'cv' if newest['status'] == 'cv' else 'twijfel'
            n_files = len({r['path'] for r in everyone[key]})
            self.write_log(f'   {newest["name"] or "(naam onbekend)"}  [{label}, nieuwste versie]  —  {newest["path"]}'
                           + (f'  ({n_files} bestanden)' if n_files > 1 else ''), 'info', link=newest['path'])
            if newest['path'] not in {r['path'] for r in matched}:
                old = max(matched, key=newness)
                self.write_log(f'      “{query}” staat alleen in een oudere versie: {old["path"]}', 'muted',
                               link=old['path'])
        if len(people) > 25:
            self.write_log(f'   … en nog {len(people) - 25}. Maak je zoekterm specifieker.', 'muted')
        elif hits:
            self.write_log('   (klik op een regel om het cv te openen)', 'muted')

    def open_errors(self):
        target = self.output / 'fouten_overzicht.csv'
        if not target.exists():
            messagebox.showinfo('Geen leesfouten', 'Er is nog geen foutenoverzicht. Scan eerst.', parent=self.root)
        elif sys.platform == 'win32':
            os.startfile(str(target))
        else:
            messagebox.showinfo('Leesfouten', str(target), parent=self.root)

    def emit(self, kind, payload):
        self.events.put((kind, payload))

    def set_running(self, active):
        self.running = active
        self.all_button.configure(state='disabled' if active else 'normal')
        self.folder_button.configure(state='disabled' if active else 'normal')
        self.stop_button.configure(state='normal' if active else 'disabled')
        self.refresh_button.configure(state='disabled' if active else 'normal')
        self.excel_button.configure(state='disabled' if active else 'normal')
        for b in ('clean_button', 'undo_button', 'doubt_button', 'confirm_button'):
            if hasattr(self, b):
                getattr(self, b).configure(state='disabled' if active else 'normal')
        self.search_button.configure(state='disabled' if active else 'normal')
        if active:
            self.total = self.done = 0
            self.progress.configure(mode='indeterminate')
            self.progress.start(15)
        else:
            self.progress.stop()
            self.progress.configure(mode='determinate', maximum=100, value=100 if self.done else 0)
            self.current.set('')

    def scan_all(self):
        self.custom_root = None
        self.source_label.set('Alle toegankelijke interne schijven + OneDrive' +
                              (' + USB-/netwerkschijven' if self.volumes.get() else ''))
        self.launch(mode='scan')

    def scan_folder(self):
        folder = filedialog.askdirectory(parent=self.root,
                                        title='Kies de map met documenten (submappen worden ook bekeken)')
        if folder:
            self.custom_root = Path(folder)
            self.source_label.set(str(self.custom_root))
            self.launch(mode='scan')

    def launch(self, *, mode='scan', online_key=''):
        scan = mode == 'scan'
        if self.running:
            return
        self.online_fresh = False
        if mode == 'online' or (scan and self.online_fill.get()):
            if (self.output / 'online_cache.json').exists():
                answer = messagebox.askyesnocancel('Online zoeken',
                    'Er zijn al eerder zoekresultaten opgeslagen.\n\n'
                    'Iedereen OPNIEUW opzoeken? Dan zie je de actuele LinkedIn-gegevens.\n\n'
                    'Ja = opnieuw zoeken (kost credits)\n'
                    'Nee = opgeslagen resultaten gebruiken (gratis, maar mogelijk verouderd)\n'
                    'Annuleren = niets doen', icon='question', parent=self.root)
                if answer is None:
                    return
                self.online_fresh = bool(answer)
        self.stopping.clear()
        # Vanaf het begin van een nieuwe export is een eerdere Excel NIET meer actueel.
        # Bij een crash blijft de oude XLSX op schijf voor herstel, maar Open Excel toont hem niet.
        if mode in ('scan', 'export', 'online', 'opschonen', 'terugzetten'):
            try:
                (self.output / EXCEL_READY_MARKER).unlink(missing_ok=True)
            except OSError as exc:
                messagebox.showerror('Excel-uitvoer', f'De vorige Excel-status kan niet worden gewist: {exc}',
                                     parent=self.root)
                return
        self.last_mode = mode
        if scan:
            for key, var in self.metrics.items():
                var.set('—' if key in ('personen','fouten') else '0')
            self.status.set('Voorbereiden…')
        else:
            self.status.set('Excel opnieuw opbouwen…' if mode == 'export' else 'Online suggesties opzoeken…')
        self.set_running(True)
        options = {'root': str(self.custom_root) if self.custom_root else '',
                   'volumes': self.volumes.get(), 'deep': self.deep.get(), 'cloud': self.cloud.get(),
                   'ocr': self.ocr.get(), 'online': self.online_fill.get(),
                   'contact': self.online_contact.get()}
        if scan and self.online_fill.get() and not online_key:
            online_key = load_key() or ('-' if heeft_zoeksleutel() else '')
        worker = threading.Thread(target=self.worker, args=(mode, online_key, options), daemon=True)
        worker.start()

    def worker(self, mode, online_key, options):
        scan = mode == 'scan'
        try:
            python = prepare_runtime(self.emit, self.stopping)
            if self.stopping.is_set():
                raise InterruptedError('Geadviseerd om te stoppen.')
            cmd = ([str(python), '--backend'] if FROZEN else [str(python), '-u', str(BACKEND)]) + ['--output', str(self.output)]
            if scan:
                if options['root']:
                    cmd += ['--root', options['root']]
                elif options['volumes']:
                    cmd.append('--include-volumes')
                if options['deep']:
                    cmd.append('--deep')
                if options.get('cloud'):
                    cmd.append('--cloud-download')
                if not options['ocr']:
                    cmd.append('--no-ocr')
                if options.get('online') and online_key:
                    cmd += ['--online', '--online-limit', '0'] + (['--online-vers'] if self.online_fresh else [])
            elif mode in ('plan', 'opschonen', 'terugzetten'):
                cmd += [{'plan': '--opschonen-plan', 'opschonen': '--opschonen',
                         'terugzetten': '--terugzetten'}[mode]]
                if mode == 'opschonen' and getattr(self, 'cloud_move', False):
                    cmd.append('--cloud-verplaatsen')
            else:
                cmd += ['--export-only']
                if mode == 'online':
                    cmd += ['--online', '--online-limit', '0'] + (['--online-vers'] if self.online_fresh else [])
            env = os.environ.copy()
            env['PYTHONUNBUFFERED'] = '1'
            # Windows gebruikt soms cp1252; bestanden met emoji/Unicode crashten dan de scan.
            env['PYTHONIOENCODING'] = 'utf-8:backslashreplace'
            env['PYTHONUTF8'] = '1'
            if load_serper():
                env['SERPER_API_KEY'] = load_serper()
            if load_serpapi():
                env['SERPAPI_API_KEY'] = load_serpapi()
            if load_apify():
                env['APIFY_TOKEN'] = load_apify()
            if online_key and online_key != '-':
                env['TAVILY_API_KEY'] = online_key
            if online_key:
                env['CV_ONLINE_CONTACT'] = '1' if options.get('contact') else '0'
            self.emit('status', 'Documenten scannen…' if scan else 'Zoeken naar openbare contactgegevens…' if mode == 'online' else 'Excel opnieuw opbouwen…')
            self.emit('log', 'Scan gestart: ' + ('alle toegankelijke interne schijven' if scan and not options['root']
                                              else options['root'] if scan else 'alleen online suggesties' if mode == 'online' else 'Excel-export opnieuw maken'))
            self.output.mkdir(parents=True, exist_ok=True)
            logpath = self.output / 'scan_log.txt'
            with logpath.open('a', encoding='utf-8') as logfile:
                logfile.write('\n---- CV Scanner V12: ' + datetime.datetime.now().isoformat(timespec='seconds')
                              + ' (' + mode + ') ----\n')
                logfile.flush()
                self._launch_scanner(cmd, env, logfile, mode)
        except InterruptedError:
            self.emit('finished', (130, True, False))
        except Exception as exc:
            self.emit('error', str(exc))
        finally:
            self.process = None

    def _stream_process(self, cmd, env, logfile, allow_cancel=True):
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                encoding='utf-8', errors='replace', env=env, bufsize=1,
                                start_new_session=(sys.platform != 'win32'))
        self.process = proc
        if allow_cancel and self.stopping.is_set():
            try:
                if sys.platform == 'win32':
                    proc.terminate()
                else:
                    os.killpg(proc.pid, signal.SIGINT)
            except (ProcessLookupError, PermissionError, OSError):
                pass
        assert proc.stdout is not None
        for line in proc.stdout:
            line = line.rstrip()
            if line.startswith('TOTAAL:'):
                self.emit('total', int(line.split(':', 1)[1]))
                continue
            if line.startswith('BEZIG: '):
                self.emit('busy', line[7:])
                continue
            if line:
                logfile.write(line + '\n')
                logfile.flush()
                self.emit('log', line)
                changes = parse_progress(line)
                if changes:
                    self.emit('metrics', changes)
        code = proc.wait()
        self.process = None
        logfile.write('Process exit code: ' + str(code) + '\n')
        logfile.flush()
        return code

    def _launch_scanner(self, cmd, env, logfile, mode):
        code = self._stream_process(cmd, env, logfile)
        recovered = False
        # When Word, PDF, or the console itself crashes, the index should still
        # generate an Excel using what was saved every 25 files.
        if mode == 'scan' and (code != 0 or self.stopping.is_set()) and (self.output / 'cv_index.sqlite').exists():
            self.emit('status', 'Scan onderbroken: Excel uit bewaarde gegevens maken…')
            self.emit('log', 'Herstel: Excel opnieuw opbouwen uit eerder opgeslagen documenten.')
            export_cmd = (([str(cmd[0]), '--backend'] if FROZEN else [str(cmd[0]), '-u', str(BACKEND)]) +
                          ['--output', str(self.output), '--export-only', '--no-refresh'])
            try:
                export_code = self._stream_process(export_cmd, env, logfile, allow_cancel=False)
                recovered = export_code == 0 and self.latest_excel() is not None
            except Exception as exc:
                self.emit('log', f'Exportherstel mislukt: {type(exc).__name__}: {exc}')
        self.emit('finished', (code, self.stopping.is_set(), recovered))

    def drain_events(self):
        try:
            for _ in range(80):
                kind, payload = self.events.get_nowait()
                if kind == 'log':
                    if payload.startswith('OPSCHOON-PLAN:'):
                        self.cleanup_text = payload.split(':', 1)[1].strip()
                    if 'documenten gezien |' in payload or payload.startswith(('  herlezen:', 'Bestanden tellen')):
                        continue   # staat al in de balk en tellers
                    if payload.startswith('TOEGANG GEWEIGERD:'):
                        self.blocked.append(payload.split(':', 1)[1].strip())
                        self.root.after(400, self.show_mac_access)
                    self.write_log(payload)
                elif kind == 'status':
                    self.status.set(payload)
                elif kind == 'badge':
                    if payload:
                        self.badge_text.set('✓  PDF-lezer volledig')
                        self.badge.configure(bg=OK)
                    else:
                        self.badge_text.set('!  PDF-lezer beperkt (cryptography werkt niet)')
                        self.badge.configure(bg=ERR)
                elif kind == 'total':
                    self.total = max(1, payload)
                    self.progress.stop()
                    self.progress.configure(mode='determinate', maximum=self.total, value=0)
                elif kind == 'busy':
                    self.done += 1
                    if self.total:
                        self.progress.configure(value=min(self.done, self.total))
                    short = payload if len(payload) <= 95 else '…' + payload[-94:]
                    self.current.set(f'Bezig met: {short}   ·   {self.done} van {self.total or "?"}')
                    self.metrics['documenten'].set(str(self.done))
                elif kind == 'metrics':
                    for key, value in payload.items():
                        if key in self.metrics:
                            self.metrics[key].set(value)
                elif kind == 'finished':
                    self.set_running(False)
                    try:
                        self.metrics['twijfel'].set(str(len(list_twijfel(self.output))))
                    except Exception:
                        pass
                    rc, stopped, recovered = payload
                    if getattr(self, 'last_mode', '') == 'plan':
                        self.status.set('Klaar om op te schonen.')
                        if rc == 0 and not stopped:
                            self.root.after(50, self.confirm_cleanup)
                        continue
                    excel_exists = self.latest_excel() is not None
                    if stopped:
                        self.status.set('Scan gestopt. Excel met bewaarde gegevens gemaakt.' if recovered else
                                        'Scan gestopt. Eerder opgeslagen werk blijft behouden.')
                    elif rc == 0:
                        self.status.set('Klaar! De NIEUWE Excel is gevalideerd en staat klaar.' if excel_exists
                                        else 'Export niet geldig: geen nieuwe Excel beschikbaar.')
                        if not excel_exists:
                            self.write_log('FOUT: exportproces klaar zonder geldig nieuw Excel-overzicht; oude Excel niet openen.')
                    else:
                        self.status.set('Scan onderbroken, maar Excel met bewaarde gegevens gemaakt.' if recovered else
                                        'Scan onverwacht gestopt. Bekijk de laatste regels bij Activiteit.'
                                        if (rc & 0xffffffff) == 0xC0000005 else
                                        f'Niet gelukt (foutcode {rc}). Bekijk het activiteitenvenster.')
                    self.write_log(('Je kunt op ‘Resultatenmap’ klikken om de map in Finder te openen.' if sys.platform == 'darwin' else 'Je kunt op ‘Resultatenmap’ klikken om de map te openen.') if excel_exists else
                                   'Geen Excel-export aangetroffen. Probeer Excel bijwerken.')
                    auto_open = getattr(self, 'last_mode', '') == 'scan' and not stopped
                    if self.open_list_when_ready:
                        self.open_list_when_ready = False
                        if rc == 0:
                            self.open_candidates()
                    elif self.open_when_ready or auto_open:
                        self.open_when_ready = False
                        if excel_exists and rc == 0 and sys.platform == 'darwin' and auto_open:
                            # Mac: Excel kan vastlopen op cv-links; de lijst in de scanner opent cv's zelf.
                            self.write_log('Kandidatenlijst wordt geopend (de Excel staat in de Resultatenmap)…', 'ok')
                            self.open_candidates()
                        elif excel_exists and rc == 0:
                            self.write_log('Excel wordt automatisch geopend…', 'ok')
                            self.open_excel()
                elif kind == 'error':
                    self.set_running(False)
                    self.status.set('Er ging iets mis tijdens voorbereiden of scannen.')
                    self.write_log('FOUT: ' + payload)
                    messagebox.showerror('CV Scanner — fout', payload, parent=self.root)
        except queue.Empty:
            pass
        self.root.after(120, self.drain_events)

    def stop(self):
        if not self.running:
            return
        self.stopping.set()
        self.status.set('Stoppen… reeds gevonden documenten blijven bewaard.')
        if self.process and self.process.poll() is None:
            try:
                self.process.terminate() if sys.platform == 'win32' else os.killpg(self.process.pid, signal.SIGINT)
            except (ProcessLookupError, PermissionError, OSError):
                pass

    def latest_excel(self):
        """Alleen de laatst BEWEZEN geslaagde export, nooit een oude XLSX-fallback."""
        return geldige_excel_export(self.output)

    def open_results(self):
        self.output.mkdir(parents=True, exist_ok=True)
        if sys.platform == 'win32':
            os.startfile(str(self.output))
        elif sys.platform == 'darwin':
            subprocess.Popen(['open', str(self.output)])
        else:
            messagebox.showinfo('Resultaten', str(self.output), parent=self.root)

    def open_excel(self):
        if self.running:
            messagebox.showinfo('Excel wordt bijgewerkt',
                                'Er wordt nog een nieuw Excel-overzicht gemaakt. Oude exports worden niet geopend.',
                                parent=self.root)
            return
        excel = self.latest_excel()
        if excel is None:
            messagebox.showinfo('Geen actuele Excel',
                'Er is geen nieuwe, geslaagde Excel-export beschikbaar.\n\n'
                'Klik op ‘Excel bijwerken’ om een nieuw overzicht te maken. '
                'Bij een fout blijft de oude Excel gesloten; zie ‘Activiteit’ voor de oorzaak.',
                parent=self.root)
            return
        try:
            if sys.platform == 'win32':
                os.startfile(str(excel))
            elif sys.platform == 'darwin':
                subprocess.Popen(['open', str(excel)])
            else:
                messagebox.showinfo('Excel', str(excel), parent=self.root)
        except OSError as exc:
            messagebox.showerror('Excel openen mislukt', str(exc), parent=self.root)

    def open_candidates(self):
        """Kandidaten als nette tabel in de scanner. Alleen de belangrijke kolommen zichtbaar;
        ID's en bron-kolommen blijven bewaard en staan in het detailvak. Dubbelklik opent het cv."""
        import webbrowser
        import tkinter.font as tkfont
        src = self.output / 'kandidaten.json'
        if not src.exists():
            if (self.output / 'cv_index.sqlite').exists() and not self.running:
                self.write_log('Kandidatenlijst wordt gemaakt (Excel bijwerken)…', 'info')
                self.open_list_when_ready = True
                self.launch(mode='export')
            else:
                messagebox.showinfo('Nog geen kandidaten',
                    'Er is nog geen lijst. Scan eerst, of wacht tot de scan klaar is.', parent=self.root)
            return
        try:
            data = json.loads(src.read_text(encoding='utf-8'))
            cols, rows = data['kolommen'], data['rijen']
        except (OSError, ValueError, KeyError) as exc:
            messagebox.showerror('Kandidaten', f'De lijst kon niet gelezen worden ({exc}). Klik op Excel bijwerken.',
                                 parent=self.root)
            return

        def text(v):
            return v.get('tekst', '') if isinstance(v, dict) else str(v or '')

        def flat(v):
            return ' '.join(text(v).split())

        def status(r):
            z = text(r.get('gezocht_zekerheid', ''))
            if 'LinkedIn: ✓ zeker' in z:
                return '✓ LinkedIn zeker'
            if 'LinkedIn: controleren' in z:
                return '? controleren'
            if 'naamgenoten' in z:
                return 'alleen naamgenoten'
            if 'niet gevonden' in z:
                return 'niet gevonden'
            return '✓ uit cv' if text(r.get('linkedin', '')) else ''

        def linkedin_short(r):
            t = text(r.get('linkedin', '')).strip()
            if '/in/' in t:
                return 'in/' + t.split('/in/', 1)[1].strip('/').split('?')[0]
            return t

        # Zichtbaar: (kop, breedte, waarde). De rest (ID's, bron, waarom) staat in het detailvak.
        visible = [('Naam', 200, lambda r: flat(r.get('naam', ''))),
                   ('E-mail', 220, lambda r: flat(r.get('email', ''))),
                   ('Telefoon', 125, lambda r: flat(r.get('telefoon', ''))),
                   ('LinkedIn', 210, linkedin_short),
                   ('Online', 140, status),
                   ('Functie', 180, lambda r: flat(r.get('huidige_functie', ''))),
                   ('Werkgever', 170, lambda r: flat(r.get('laatst_bekende_werkgever', '')))]
        if not any(r.get('huidige_functie') or r.get('laatst_bekende_werkgever') for r in rows):
            visible = visible[:5]
        names = {'kandidaat_id': 'Kandidaat-ID', 'cv_id': 'Cv-ID', 'email_bron': 'E-mail komt uit',
                 'telefoon_bron': 'Telefoon komt uit', 'linkedin_bron': 'LinkedIn komt uit',
                 'gezocht_zekerheid': 'Online zekerheid', 'gezocht_waarom': 'Waarom (wat klopt)',
                 'huidige_functie': 'Functie', 'laatst_bekende_werkgever': 'Werkgever',
                 'bedrijf': 'Bedrijf (bovenaan cv)', 'bedrijf_soort': 'Soort bedrijf'}

        win = tk.Toplevel(self.root)
        win.title(f'Kandidaten ({len(rows)})')
        win.configure(bg=PANEL)
        win.geometry('1200x720')
        style = ttk.Style(win)
        rowfont = tkfont.Font(family=FONT, size=11)
        style.configure('Kand.Treeview', font=rowfont, rowheight=int(rowfont.metrics('linespace') * 1.9),
                        background=CARD, fieldbackground=CARD, foreground=TEXT, borderwidth=0)
        style.configure('Kand.Treeview.Heading', font=(FONT, 10, 'bold'), padding=(6, 6))
        style.map('Kand.Treeview', background=[('selected', ACCENT)], foreground=[('selected', '#ffffff')])

        top = tk.Frame(win, bg=PANEL)
        top.pack(fill='x', padx=16, pady=(14, 4))
        tk.Label(top, text='Zoek:', font=(FONT, 11), fg=TEXT, bg=PANEL).pack(side='left')
        query = tk.StringVar()
        entry = tk.Entry(top, textvariable=query, font=(FONT, 11), relief='flat', bg=CARD, fg=TEXT,
                         insertbackground=TEXT, highlightthickness=1, highlightbackground=LINE, highlightcolor=ACCENT)
        entry.pack(side='left', fill='x', expand=True, ipady=6, padx=(8, 12))
        count = tk.StringVar()
        tk.Label(top, textvariable=count, font=(FONT, 10), fg=MUTED, bg=PANEL).pack(side='left')
        tk.Label(win, text='Dubbelklik = cv openen  ·  klik op een kolomkop = sorteren  ·  '
                 'zoeken werkt op alles, ook op ID', font=(FONT, 10), fg=MUTED, bg=PANEL
                 ).pack(anchor='w', padx=16, pady=(0, 8))

        pane = tk.PanedWindow(win, orient='vertical', bg=PANEL, sashwidth=6, bd=0)
        pane.pack(fill='both', expand=True, padx=16)
        body = tk.Frame(pane, bg=PANEL)
        heads = [h for h, _w, _f in visible]
        tree = ttk.Treeview(body, columns=heads, show='headings', style='Kand.Treeview', selectmode='browse')
        ys = ttk.Scrollbar(body, orient='vertical', command=tree.yview)
        xs = ttk.Scrollbar(body, orient='horizontal', command=tree.xview)
        tree.configure(yscrollcommand=ys.set, xscrollcommand=xs.set)
        ys.pack(side='right', fill='y')
        xs.pack(side='bottom', fill='x')
        tree.pack(side='left', fill='both', expand=True)
        for h, w, _f in visible:
            tree.column(h, width=w, minwidth=70, stretch=(h == 'Naam'), anchor='w')
        tree.tag_configure('odd', background='#f4f7f9')
        tree.tag_configure('even', background=CARD)
        detail = tk.Text(pane, height=9, wrap='word', font=(FONT, 10), bg=CARD, fg=TEXT, relief='flat',
                         padx=12, pady=10, highlightthickness=1, highlightbackground=LINE)
        detail.tag_configure('h', font=(FONT, 12, 'bold'))
        detail.tag_configure('k', foreground=MUTED)
        detail.configure(state='disabled')
        pane.add(body, stretch='always')
        pane.add(detail, stretch='never')

        order = {'col': 'Naam', 'rev': False}
        shown = {}
        getter = {h: f for h, _w, f in visible}
        search_text = [' '.join(flat(r.get(c, '')) for c in cols).lower() for r in rows]

        def fill(*_a):
            words = query.get().lower().split()
            items = [i for i in range(len(rows)) if all(w in search_text[i] for w in words)]
            g = getter[order['col']]
            items.sort(key=lambda i: (g(rows[i]) == '', g(rows[i]).lower()), reverse=order['rev'])
            tree.delete(*tree.get_children())
            shown.clear()
            for n, i in enumerate(items):
                iid = tree.insert('', 'end', values=[f(rows[i]) for _h, _w, f in visible],
                                  tags=('odd' if n % 2 else 'even',))
                shown[iid] = rows[i]
            count.set(f'{len(items)} van {len(rows)} kandidaten')
            for h in heads:
                arrow = ('  ▼' if order['rev'] else '  ▲') if h == order['col'] else ''
                tree.heading(h, text=h + arrow, anchor='w', command=lambda h=h: sort_by(h))
            kids = tree.get_children()
            if kids:
                tree.selection_set(kids[0])

        def sort_by(h):
            order['rev'] = (not order['rev']) if order['col'] == h else False
            order['col'] = h
            fill()

        def current():
            sel = tree.selection()
            return shown.get(sel[0]) if sel else None

        def show_detail(_e=None):
            r = current()
            detail.configure(state='normal')
            detail.delete('1.0', 'end')
            if r:
                detail.insert('end', flat(r.get('naam', '')) + '\n', 'h')
                for c in cols:
                    t = text(r.get(c, '')).strip()
                    if not t or c in ('naam', 'open_cv', 'zelf_zoeken_google'):
                        continue
                    label = names.get(c, c.replace('_', ' ').capitalize())
                    detail.insert('end', f'{label}: ', 'k')
                    detail.insert('end', ('\n' + t if '\n' in t else t) + '\n')
            detail.configure(state='disabled')

        def open_cv():
            r = current()
            v = r.get('open_cv') if r else None
            if isinstance(v, dict) and v.get('pad'):
                self.open_path(v['pad'])
            elif r:
                messagebox.showinfo('Geen cv', 'Voor deze persoon is geen cv-bestand bekend.', parent=win)

        def open_url(col):
            r = current()
            v = r.get(col) if r else None
            url = v.get('url', '') if isinstance(v, dict) else ''
            if not url and col == 'linkedin':
                t = text(v).strip()
                url = t if t.startswith('http') else ('https://' + t if 'linkedin.com' in t else '')
            if url:
                webbrowser.open(url)
            elif r:
                messagebox.showinfo('Geen link', 'Voor deze persoon is er geen link.', parent=win)

        def copy(col, label):
            r = current()
            t = text(r.get(col, '')).strip() if r else ''
            if t:
                win.clipboard_clear()
                win.clipboard_append(t)
                self.write_log(f'{label} gekopieerd: {t}', 'ok')

        def double(event):
            row_id = tree.identify_row(event.y)
            if not row_id:
                return
            tree.selection_set(row_id)
            col_id = tree.identify_column(event.x)
            try:
                col = heads[int(col_id.lstrip('#')) - 1]
            except (ValueError, IndexError):
                col = ''
            if col == 'LinkedIn' and text(shown[row_id].get('linkedin', '')):
                open_url('linkedin')
            else:
                open_cv()

        tree.bind('<<TreeviewSelect>>', show_detail)
        tree.bind('<Double-1>', double)
        tree.bind('<Return>', lambda _e: open_cv())
        query.trace_add('write', fill)
        bar = tk.Frame(win, bg=PANEL)
        bar.pack(fill='x', padx=16, pady=12)
        self.button(bar, 'Open cv', open_cv, primary=True).pack(side='left', padx=(0, 8))
        self.button(bar, 'Open LinkedIn', lambda: open_url('linkedin')).pack(side='left', padx=(0, 8))
        self.button(bar, 'Zoek zelf in Google', lambda: open_url('zelf_zoeken_google')).pack(side='left', padx=(0, 8))
        self.button(bar, 'Kopieer e-mail', lambda: copy('email', 'E-mail')).pack(side='left', padx=(0, 8))
        self.button(bar, 'Kopieer telefoon', lambda: copy('telefoon', 'Telefoon')).pack(side='left', padx=(0, 8))
        self.button(bar, 'Sluiten', win.destroy).pack(side='right')
        fill()
        entry.focus_set()

    def show_mac_access(self):
        if not self.blocked or getattr(self, '_access_shown', False):
            return
        self._access_shown = True
        if messagebox.askyesno('Mac geeft geen toegang',
                'macOS laat de scanner deze mappen niet lezen:\n\n' + '\n'.join(self.blocked) +
                '\n\nDaar worden dus geen cv\'s gevonden. Oplossing:\n'
                '1. Open Systeeminstellingen > Privacy en beveiliging > Volledige schijftoegang\n'
                '2. Zet "CV Scanner" aan (staat hij er niet: klik + en kies de app)\n'
                '3. Sluit de scanner en start hem opnieuw\n\n'
                'Nu de instellingen openen?', parent=self.root):
            subprocess.Popen(['open', 'x-apple.systempreferences:com.apple.preference.security?Privacy_AllFiles'])

    def permissions(self):
        mac = sys.platform == 'darwin'
        messagebox.showinfo('Toegang en privacy',
            'Het programma leest alleen documenten waarvoor jouw ' + ('Mac' if mac else 'Windows') + '-account '
            'toegang heeft. Het kan geen bestandsbeveiliging omzeilen.\n\n' +
            ('Geef de app op de Mac "Volledige schijftoegang" (Systeeminstellingen > Privacy en beveiliging), '
             'anders kan hij Bureaublad, Documenten en Downloads niet lezen.\n\n'
             'Cv\'s die alleen in iCloud/OneDrive/Google Drive staan, worden tijdens de scan automatisch '
             'gedownload (internet nodig).\n\n'
             if mac else
             'Documenten die alleen in OneDrive staan, worden tijdens de scan automatisch '
             'gedownload (internet nodig).\n\n') +
            'De scan is lokaal. De optionele functie Online zoeken verstuurt '
            'alleen namen/werkgevers naar een zoekdienst, na jouw toestemming.\n\n'
            'Gebruik de gegevens volgens de privacywetgeving (AVG).', parent=self.root)

    def ask_key(self):
        key = simpledialog.askstring('Tavily-sleutel',
            'Plak je Tavily API-sleutel (begint met "tvly-").\n'
            'Gratis aan te maken op tavily.com. De sleutel wordt alleen op deze pc bewaard.',
            parent=self.root, show='*')
        if key and key.strip():
            save_key(key)
            self.write_log('Tavily-sleutel opgeslagen op deze pc.', 'ok')
            return key.strip()
        return ''

    def set_serper(self):
        if self.running:
            return
        for title, path, info in (
                ('Apify (eerste keuze)', APIFY_FILE,
                 'Plak je Apify API-token (Settings > API & Integrations). Wordt als EERSTE gebruikt om openbare '
                 'LinkedIn-profielen op te halen, met werkervaring en jaren ($4 per 1.000, $5 gratis per maand).'),
                ('Google-sleutel 1: Serper', SERPER_FILE,
                 'Plak je API-sleutel van serper.dev (2.500 gratis, eenmalig). Wordt EERST gebruikt.'),
                ('Google-sleutel 2: SerpApi', SERPAPI_FILE,
                 'Plak je API-sleutel van serpapi.com (250 gratis per maand). Wordt gebruikt als Serper op is.')):
            current = 'ingesteld' if path.exists() else 'niet ingesteld'
            key = simpledialog.askstring(title, info + f'\n\nNu: {current}.\n'
                                         'Leeg laten en OK = verwijderen · Annuleren = niets veranderen.\n'
                                         'Alleen gebruikt als een LinkedIn-profiel zeker klopt en mail/telefoon ontbreekt.',
                                         parent=self.root, show='*')
            if key is None:
                continue
            if key.strip():
                RUNTIME.mkdir(parents=True, exist_ok=True)
                path.write_text(key.strip(), encoding='utf-8')
                try:
                    os.chmod(path, 0o600)
                except OSError:
                    pass
                self.write_log(f'{title} opgeslagen op deze pc.', 'ok')
            else:
                try:
                    path.unlink()
                except OSError:
                    pass
                self.write_log(f'{title} verwijderd.', 'muted')

    def set_key(self):
        if not self.running and self.ask_key():
            self.online_fill.set(True)

    def toggle_online(self):
        if self.online_fill.get() and not heeft_zoeksleutel() and not self.ask_key():
            self.online_fill.set(False)

    def online(self):
        if self.running:
            return
        if not (self.output / 'cv_index.sqlite').exists():
            messagebox.showinfo('Eerst scannen', 'Start eerst een lokale scan.', parent=self.root)
            return
        key = load_key() or ('-' if heeft_zoeksleutel() else '') or self.ask_key()
        if key:
            self.launch(mode='online', online_key=key)

    # ---------- Twijfelgevallen zelf beoordelen ----------
    def open_doubts(self):
        if self.running:
            return
        items = list_twijfel(self.output)
        if not items and not (self.output / 'cv_index.sqlite').exists():
            messagebox.showinfo('Eerst scannen', 'Start eerst een scan.', parent=self.root)
            return
        if not items:
            messagebox.showinfo('Twijfelgevallen', 'Er zijn geen twijfelgevallen.', parent=self.root)
            return
        dec_file = self.output / 'twijfel_beslissingen.json'
        try:
            decisions = json.loads(dec_file.read_text(encoding='utf-8'))
        except (OSError, ValueError):
            decisions = {}
        win = tk.Toplevel(self.root)
        win.title('Twijfelgevallen beoordelen')
        win.configure(bg=PANEL)
        win.geometry('980x460')
        tk.Label(win, text='Open een bestand, kies daarna ‘Wel cv’ of ‘Geen cv’. Je keuze blijft bewaard, ook bij een nieuwe scan.',
                 font=(FONT, 10), fg=MUTED, bg=PANEL, wraplength=940, justify='left').pack(anchor='w', padx=16, pady=(12, 6))
        cols = ('bestand', 'map', 'naam', 'reden', 'keuze')
        tree = ttk.Treeview(win, columns=cols, show='headings', height=14)
        for c, w in zip(cols, (260, 300, 150, 300, 80)):
            tree.heading(c, text=c.capitalize())
            tree.column(c, width=w, anchor='w')
        tree.pack(fill='both', expand=True, padx=16)
        label = {'cv': 'Wel cv', 'geen': 'Geen cv'}

        def friendly(it):
            note = (it['note'] or '') + ' ' + (it['reason'] or '')
            if 'OCR mislukt' in note:
                return 'Gescande pdf – OCR mislukte (wordt bij volgende scan opnieuw geprobeerd)'
            if 'geen leesbare tekst' in note:
                return 'Gescande pdf – nog geen tekst gelezen'
            return (it['reason'] or it['note'] or '').split(';')[0][:120]

        def split_path(pth):
            pth = str(pth).replace(' › ', '\\')
            parts = re.split(r'[\\/]', pth)
            return parts[-1], '\\'.join(parts[-4:-1]) if len(parts) > 1 else ''

        for i, it in enumerate(items):
            fname, folder = split_path(it['path'])
            tree.insert('', 'end', iid=str(i), values=(fname, '…\\' + folder if folder else '', it['name'] or '—',
                                                         friendly(it),
                                                         label.get(decisions.get(it['sha'], {}).get('status'), '')))
        detail = tk.Text(win, height=3, wrap='word', font=(FONT, 10), bg=CARD, fg=TEXT, relief='flat',
                         padx=10, pady=8, highlightthickness=1, highlightbackground=LINE)
        detail.pack(fill='x', padx=16, pady=(8, 0))

        def show_detail(_e=None):
            sel = tree.selection()
            if not sel:
                return
            it = items[int(sel[0])]
            detail.configure(state='normal')
            detail.delete('1.0', 'end')
            detail.insert('end', f"{it['path']}\n{friendly(it)}")
            detail.configure(state='disabled')
        tree.bind('<<TreeviewSelect>>', show_detail)
        changed = {'n': 0}

        def current():
            sel = tree.selection()
            if not sel:
                messagebox.showinfo('Kies een bestand', 'Klik eerst op een regel.', parent=win)
                return None, None
            return sel[0], items[int(sel[0])]

        def open_file():
            _i, it = current()
            if not it:
                return
            path = it['path']
            target = it['archive'] if ' › ' in path and it['archive'] else path
            if ' › ' in target:
                messagebox.showinfo('In zip', 'Dit bestand zit in een zip:\n' + path, parent=win)
                return
            try:
                if sys.platform == 'win32':
                    os.startfile(target)
                else:
                    subprocess.Popen(['open', target])
            except OSError as exc:
                messagebox.showerror('Openen mislukt', str(exc), parent=win)

        def decide(status):
            i, it = current()
            if not it:
                return
            dec = {'status': status, 'pad': it['path']}
            if status == 'cv':
                naam = it['name'] or simpledialog.askstring('Naam', 'Welke naam hoort bij dit cv?', parent=win) or ''
                if not naam.strip():
                    messagebox.showinfo('Naam nodig', 'Zonder naam kan het cv niet aan een kandidaat gekoppeld worden.', parent=win)
                    return
                dec['naam'] = naam.strip()
            decisions[it['sha']] = dec
            vals = list(tree.item(i, 'values'))
            vals[4] = label[status]
            tree.item(i, values=vals)
            changed['n'] += 1
            nxt = tree.next(i)
            if nxt:
                tree.selection_set(nxt)

        def close():
            if changed['n']:
                dec_file.write_text(json.dumps(decisions, indent=1, ensure_ascii=False), encoding='utf-8')
                self.write_log(f'{changed["n"]} twijfelgeval(len) beoordeeld; Excel wordt bijgewerkt.', 'ok')
                win.destroy()
                self.refresh_export()
            else:
                win.destroy()

        bar = tk.Frame(win, bg=PANEL)
        bar.pack(fill='x', padx=16, pady=12)
        self.button(bar, 'Open bestand', open_file).pack(side='left', padx=(0, 8))
        self.button(bar, '✓  Wel cv', lambda: decide('cv'), primary=True).pack(side='left', padx=(0, 8))
        self.button(bar, '✗  Geen cv', lambda: decide('geen')).pack(side='left', padx=(0, 8))
        self.button(bar, 'Opslaan en sluiten', close).pack(side='right')
        tree.bind('<Double-1>', lambda _e: open_file())
        win.protocol('WM_DELETE_WINDOW', close)
        tree.selection_set('0')

    # ---------- 'Niet zeker' zelf controleren ----------
    def open_online_check(self):
        """Per kandidaat: eerst wat de scanner aanraadt, daarna (ingeklapt) de andere gevonden profielen.
        Wat al zeker is, staat er niet in."""
        if self.running:
            return
        import csv as _csv, webbrowser
        src = self.output / 'online_suggesties.csv'
        if not src.exists():
            messagebox.showinfo('Online controleren', 'Er is nog niets online gezocht.', parent=self.root)
            return
        with src.open(encoding='utf-8-sig', newline='') as f:
            allrows = list(_csv.DictReader(f, delimiter=';'))
        dec_file = self.output / 'online_beslissingen.json'
        try:
            decisions = json.loads(dec_file.read_text(encoding='utf-8'))
        except (OSError, ValueError):
            decisions = {}

        def norm(t):
            t = unicodedata.normalize('NFKD', t or '').encode('ascii', 'ignore').decode().lower()
            return ' '.join(re.findall(r'[a-z0-9]+', t))

        def key(r):
            return f"{r.get('persoon_id') or norm(r['naam'])}|{r['type']}|{(r['gevonden_waarde'] or '').lstrip(chr(39)).strip().lower()}"

        def ticks(r):
            return [x.strip() for x in (r.get('overeenkomst') or '').split('|') if '✓' in x]

        # Groepen per kandidaat + soort; al zekere (of al door jou bevestigde) groepen overslaan.
        groups = {}
        for r in allrows:
            if not (r.get('gevonden_waarde') or '').strip():
                continue          # 'niet gevonden': niets om te controleren
            groups.setdefault((r['naam'], r['type'], r.get('persoon_id', '')), []).append(r)
        todo = []
        for (naam, typ, _pid), rs in sorted(groups.items(), key=lambda kv: (kv[0][0].lower(), kv[0][1] != 'linkedin')):
            if any((x.get('zekerheid') or '').startswith('waarschijnlijk') for x in rs):
                continue
            if any(decisions.get(key(x)) == 'ja' for x in rs):
                continue
            rs.sort(key=lambda x: (x.get('keuze') != 'beste', -len(ticks(x))))
            todo.append(((naam, typ), rs))
        if not todo:
            messagebox.showinfo('Online controleren', 'Niets te controleren: alles is zeker of al door jou beoordeeld.',
                                parent=self.root)
            return
        win = tk.Toplevel(self.root)
        win.title('Online gevonden controleren')
        win.configure(bg=PANEL)
        win.geometry('1150x560')
        info = tk.StringVar()
        tk.Label(win, text='Per kandidaat staat bovenaan wat de scanner aanraadt (★). Klik op ▸ om de andere gevonden '
                 'profielen te zien. Open de link en kies ‘Klopt’ of ‘Klopt niet’. Al zekere kandidaten staan hier niet in.',
                 font=(FONT, 10), fg=MUTED, bg=PANEL, wraplength=1110, justify='left').pack(anchor='w', padx=16, pady=(12, 2))
        tk.Label(win, textvariable=info, font=(FONT, 10, 'bold'), fg=TEXT, bg=PANEL).pack(anchor='w', padx=16, pady=(0, 6))
        cols = ('gevonden', 'overeenkomst', 'keuze')
        tree = ttk.Treeview(win, columns=cols, show='tree headings', height=16)
        tree.heading('#0', text='Kandidaat')
        tree.column('#0', width=260, anchor='w')
        for c, w in zip(cols, (330, 430, 100)):
            tree.heading(c, text=c.capitalize())
            tree.column(c, width=w, anchor='w')
        tree.tag_configure('rec', font=(FONT, 10, 'bold'))
        tree.tag_configure('done', foreground='#8a9aa3')
        tree.pack(fill='both', expand=True, padx=16)
        # Volledige overeenkomsten van de gekozen regel (de kolom zelf is te smal voor lange lijsten).
        detail = tk.Text(win, height=5, wrap='word', font=(FONT, 10), bg=CARD, fg=TEXT, relief='flat',
                         padx=10, pady=8, highlightthickness=1, highlightbackground=LINE)
        detail.pack(fill='x', padx=16, pady=(8, 0))
        def show_detail(_e=None):
            sel = tree.selection()
            if not sel or sel[0] not in item_row:
                return
            r = item_row[sel[0]][1]
            lines = ticks(r) or ['geen overeenkomsten gevonden']
            detail.configure(state='normal')
            detail.delete('1.0', 'end')
            detail.insert('end', (r['gevonden_waarde'] or '').lstrip("'") + '\n' + '\n'.join(lines))
            detail.configure(state='disabled')
        tree.bind('<<TreeviewSelect>>', show_detail)
        label = {'ja': 'Klopt', 'nee': 'Klopt niet'}
        soort = {'linkedin': 'LinkedIn', 'email': 'E-mail', 'telefoon': 'Telefoon'}
        item_row = {}
        for gi, ((naam, typ), rs) in enumerate(todo):
            rec = rs[0]
            gid = f'g{gi}'
            t = ticks(rec)
            tree.insert('', 'end', iid=gid, open=False, tags=('rec',),
                        text=f'{naam} — {soort.get(typ, typ)}',
                        values=('★ ' + (rec['gevonden_waarde'] or '').lstrip("'"),
                                (' | '.join(t) or 'geen overeenkomsten'),
                                label.get(decisions.get(key(rec)), '')))
            item_row[gid] = (gid, rec)
            if len(rs) > 1:
                for j, r in enumerate(rs[1:]):
                    cid = f'{gid}_{j}'
                    tree.insert(gid, 'end', iid=cid, text=f'   andere gevonden ({j + 1} van {len(rs) - 1})',
                                values=((r['gevonden_waarde'] or '').lstrip("'"),
                                        (' | '.join(ticks(r)) or 'geen overeenkomsten'),
                                        label.get(decisions.get(key(r)), '')))
                    item_row[cid] = (gid, r)
                tree.item(gid, text=f'{naam} — {soort.get(typ, typ)}   ({len(rs)} gevonden)')

        def refresh_info():
            open_groups = sum(1 for gi, ((n, t), rs) in enumerate(todo)
                              if not any(decisions.get(key(x)) == 'ja' for x in rs)
                              and not all(decisions.get(key(x)) == 'nee' for x in rs))
            info.set(f'Nog te controleren: {open_groups} van {len(todo)} kandidaten')
        refresh_info()
        changed = {'n': 0}

        def current():
            sel = tree.selection()
            return (sel[0], item_row.get(sel[0])) if sel else (None, None)

        def open_link():
            _i, pair = current()
            if not pair:
                return
            r = pair[1]
            val = (r['gevonden_waarde'] or '').lstrip("'")
            url = val if val.startswith('http') else (r.get('bron_url') or '')
            if url:
                webbrowser.open(url)

        def decide(v):
            iid, pair = current()
            if not pair:
                messagebox.showinfo('Kies een regel', 'Klik eerst op een regel.', parent=win)
                return
            gid, r = pair
            decisions[key(r)] = v
            tree.set(iid, 'keuze', label[v])
            if v == 'ja':
                # Iemand heeft één LinkedIn/nummer: de andere suggesties bij DEZE kandidaat kloppen dan niet.
                for other_iid, (g2, o) in item_row.items():
                    if g2 == gid and o is not r:
                        decisions[key(o)] = 'nee'
                        tree.set(other_iid, 'keuze', label['nee'])
                for x in [gid] + list(tree.get_children(gid)):
                    tree.item(x, tags=('done',))
                tree.item(gid, open=False)
                nxt = tree.next(gid)
                if nxt:
                    tree.selection_set(nxt)
                    tree.see(nxt)
            changed['n'] += 1
            refresh_info()

        def close():
            if changed['n']:
                dec_file.write_text(json.dumps(decisions, indent=1, ensure_ascii=False), encoding='utf-8')
                self.write_log(f'{changed["n"]} online gevonden gegeven(s) beoordeeld; Excel wordt bijgewerkt.', 'ok')
                win.destroy()
                self.refresh_export()
            else:
                win.destroy()

        bar = tk.Frame(win, bg=PANEL)
        bar.pack(fill='x', padx=16, pady=12)
        self.button(bar, 'Open link', open_link).pack(side='left', padx=(0, 8))
        self.button(bar, '✓  Klopt', lambda: decide('ja'), primary=True).pack(side='left', padx=(0, 8))
        self.button(bar, '✗  Klopt niet', lambda: decide('nee')).pack(side='left', padx=(0, 8))
        self.button(bar, 'Opslaan en sluiten', close).pack(side='right')
        tree.bind('<Double-1>', lambda _e: open_link())
        win.protocol('WM_DELETE_WINDOW', close)
        tree.selection_set('g0')

    def start_cleanup(self):
        if self.running:
            return
        if not (self.output / 'cv_index.sqlite').exists():
            messagebox.showinfo('Eerst scannen', 'Start eerst een scan.', parent=self.root)
            return
        cfg = self.output / 'cv_map.txt'
        try:
            current = cfg.read_text(encoding='utf-8').strip()
        except OSError:
            current = ''
        log_file = self.output / 'opschonen_logboek.csv'
        already = log_file.exists() and len(log_file.read_text(encoding='utf-8-sig').splitlines()) > 1
        if already:
            where = Path(current) / 'Alle_cvs' if current else self.output / 'Alle_cvs'
            messagebox.showinfo('Cv\'s opschonen', f'Er is al opgeschoond naar:\n{where}\n\nNieuwe cv\'s komen in dezelfde map. '
                                'Wil je een andere plek, klik dan eerst op ‘Terugzetten’.', parent=self.root)
        else:
            chosen = filedialog.askdirectory(parent=self.root, title='Waar wil je de map Alle_cvs neerzetten?',
                                             initialdir=current or str(Path.home() / 'Documents'))
            if not chosen:
                return
            cfg.write_text(chosen, encoding='utf-8')
            self.write_log(f'Cv\'s komen in: {Path(chosen) / "Alle_cvs"}', 'muted')
        self.cleanup_text = ''
        self.launch(mode='plan')

    def confirm_cleanup(self):
        text = getattr(self, 'cleanup_text', '') or 'geen gegevens'
        if text.startswith("0 nieuwste") and ' 0 oudere' in text and ' 0 dubbele' in text and " 0 cv's uit zips" in text \
                and " 0 cv's uit OneDrive" in text:
            messagebox.showinfo('Opschonen', 'Er staan geen losse cv\'s meer buiten de map Alle_cvs.\n\n' + text,
                                parent=self.root)
            return
        m = re.search(r"(\d+) cv's uit zips", text)
        if m and int(m.group(1)) > 0:
            if not messagebox.askokcancel('Let op: zip-bestanden',
                    f'{m.group(1)} cv\'s zitten in zip-bestanden. Die worden UIT de zip gehaald:\n\n'
                    '• Het cv komt los in Alle_cvs en zit daarna niet meer in de zip.\n'
                    '• Zitten er nog andere bestanden in de zip, dan blijft de zip met die bestanden staan.\n'
                    '• Zaten er alleen cv\'s in, dan wordt de zip weggehaald.\n\n'
                    'Van elke zip wordt eerst een backup gemaakt; ‘Terugzetten’ zet de oude zip precies terug.',
                    icon='warning', parent=self.root):
                return
        self.cloud_move = False
        c = re.search(r"(\d+) cv's uit OneDrive/iCloud", text)
        if c and int(c.group(1)) > 0:
            answer = messagebox.askyesnocancel('Cv\'s in OneDrive/iCloud',
                f'{c.group(1)} cv\'s staan in OneDrive of iCloud.\n\n'
                'Wil je ze uit je cloud WEGHALEN (verplaatsen naar Alle_cvs)?\n\n'
                'Ja = weghalen uit de cloud (verplaatsen)\n'
                'Nee = in de cloud laten staan, alleen een kopie in Alle_cvs (aanbevolen)\n'
                'Annuleren = niets doen', icon='question', parent=self.root)
            if answer is None:
                return
            self.cloud_move = bool(answer)
        cloud_line = ('Cv\'s uit OneDrive/iCloud worden ook VERPLAATST (weg uit de cloud).\n' if self.cloud_move else
                      'Cv\'s in OneDrive/iCloud worden alleen GEKOPIEERD: het origineel blijft in je cloud.\n')
        if messagebox.askyesno('Cv\'s opschonen',
                               'Dit gaat er gebeuren:\n\n' + text + '\n\n'
                               'Per kandidaat komt alleen het NIEUWSTE cv in Alle_cvs (ook uit zips).\n'
                               'Oudere versies en dubbele kopieën worden weggehaald (er blijft een kopie in cv_archief).\n'
                               + cloud_line +
                               'Terugzetten kan altijd met de knop ‘Terugzetten’.\n\nDoorgaan?',
                               icon='warning', parent=self.root):
            self.launch(mode='opschonen')

    def undo_cleanup(self):
        if self.running:
            return
        if not (self.output / 'opschonen_logboek.csv').exists():
            messagebox.showinfo('Terugzetten', 'Er is nog niets opgeschoond.', parent=self.root)
            return
        if messagebox.askyesno('Terugzetten', 'Alle opgeschoonde cv\'s terugzetten naar hun oude plek?',
                               parent=self.root):
            self.launch(mode='terugzetten')

    def refresh_export(self):
        if not (self.output / 'cv_index.sqlite').exists():
            messagebox.showinfo('Eerst scannen', 'Start eerst een lokale scan.', parent=self.root)
            return
        self.launch(mode='export')

    def close(self):
        if self.running:
            messagebox.showinfo('Scan actief',
                'Klik eerst op ‘Stop scan’ voordat je het programma sluit.', parent=self.root)
            return
        self.root.destroy()


def main():
    if sys.version_info < (3,10):
        raise RuntimeError('Python 3.10 of nieuwer vereist.')
    root = tk.Tk()
    try:
        gezet = sleutels_importeren()
    except Exception:
        gezet = []
    ScannerApp(root)
    if gezet:
        namen = {'tavily': 'Tavily', 'serper': 'Serper', 'serpapi': 'SerpApi', 'apify': 'Apify'}
        root.after(800, lambda: messagebox.showinfo(
            'Sleutels geïnstalleerd', 'Zoeksleutels geïnstalleerd: ' + ', '.join(namen[g] for g in gezet) +
            '.\nJe hoeft ze niet meer in te vullen.', parent=root))
    root.mainloop()


if __name__ == '__main__':
    main()
