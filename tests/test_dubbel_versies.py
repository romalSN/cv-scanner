"""Volledige scan van verzonnen cv's: 8 personen met elk 2 versies van hun cv, waarvan 1 ZONDER mail/telefoon,
in een andere opmaak (3 soorten) en 3x als bureau-cv (bureaucontact bovenaan, naam achternaam-eerst in de
bestandsnaam). Plus per persoon een naamgenoot (andere loopbaan) en 2 naamgenoten-collega's die 1 werkgever+jaar
delen. Eis: beide versies samen, alle naamgenoten apart. Alles verzonnen (geen echte kandidaten)."""
import csv, json, os, random, shutil, subprocess, sys, tempfile
from pathlib import Path
ROOT = Path(__file__).resolve().parent.parent
r = random.Random(7)
NAMEN = ['Nadia el Amrani', 'Bas Kooiman', 'Gerrit van der Wal', 'Ilse Brouwer', 'Kevin Doornbos', 'Priya Ramdin',
         'Thijs Ooms', 'Lotte de Hermans']
BEDR = ['Datafabriek Noord', 'Codewerk', 'Rivierdata', 'Polderlogic', 'Kanaalsoft', 'Duinsoft', 'Brugwerk ICT',
        'Spaarnedata', 'Molenaar Software', 'Veenstra Consult', 'Tulpnet', 'Grachtwerk', 'Dijkstra Systems', 'Havenlogic']
FUNC = ['Java ontwikkelaar', 'Projectmanager', 'Contractmanager', 'Azure consultant', 'Testcoördinator', 'Scrum master',
        'Functioneel beheerder', 'Servicedesk medewerker']
SCHOOL = ['Hogeschool Utrecht', 'Hogeschool van Amsterdam', 'Fontys Hogescholen', 'Avans Hogeschool', 'Hanzehogeschool']
PLAATS = ['Utrecht', 'Amersfoort', 'Haarlem', 'Leiden', 'Zwolle', 'Breda']
MAAND = ['jan', 'feb', 'mrt', 'apr', 'mei', 'jun', 'jul', 'aug', 'sep', 'okt', 'nov', 'dec']
BUREAU = ['JobBureau Test', 'aanmelden@bureau-test.nl', '0224 000000']
fout = []
def check(n, ok, info=''):
    print(('OK   ' if ok else 'FOUT ') + n + ('' if ok else f'  -> {info}'))
    if not ok: fout.append(n)
def loopbaan(start):
    y = start; out = []
    for b in r.sample(BEDR, 4):
        l = r.randint(2, 4); out.append([y, y + l, r.choice(FUNC), b, r.randint(0, 11)]); y += l
    out[-1][1] = 'heden'
    return out[::-1]
tmp = Path(tempfile.mkdtemp()); src = tmp / 'set'; uit = tmp / 'uit'
verwacht = {}
def schrijf(rel, regels, groep):
    p = src / rel; p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text('\n'.join(regels), encoding='utf-8'); verwacht[str(p.resolve())] = groep
for i, n in enumerate(NAMEN):
    voor, achter = n.split()[0], n.split()[-1]
    lb = loopbaan(r.randint(2006, 2013)); school = r.choice(SCHOOL); pl = r.choice(PLAATS)
    a = ['Curriculum Vitae', n, f'{voor.lower()}.{achter.lower()}{i}@voorbeeldmail.nl', f'06 1{i}23 45{i}7',
         f'Woonplaats: {pl}', '', 'Werkervaring']
    a += [f'{b} - {e} {f} bij {w}. Verantwoordelijk voor projecten, planning en contact met klanten.' for b, e, f, w, _m in lb]
    a += ['', 'Opleiding', f'HBO Informatica, {school}', '', 'Vaardigheden', 'Java, SQL, Scrum, communicatie']
    b_ = [f'CV – {n}', '', 'Profiel', f'Ervaren {lb[0][2].lower()} met een passie voor kwaliteit.', '', 'Loopbaan']
    for b, e, f, w, m in (lb[:-1] if i % 2 else lb):
        per = f'{MAAND[m]} {b} – {e if e == "heden" else MAAND[m] + " " + str(e)}'
        b_ += ([per, f'{w} – {f}'] if i % 3 == 0 else [f'{f}, {w} | {per}'] if i % 3 == 1
               else [f'{w}, {f} ({per.replace("heden", "present")})'])
        b_.append('Werkzaamheden: analyse, uitvoering en rapportage.')
    b_ += ['', 'Opleidingen', f'{school} – HBO Informatica', '', f'Woonplaats {pl}']
    if i in (1, 3, 4):
        b_ = BUREAU + ['Curriculum Vitae', n] + b_
        bnaam = f'{achter.title()} {voor} {700 + i} (U).txt'
    else:
        bnaam = f'CV {n} recent.txt'
    lc = loopbaan(r.randint(1998, 2016))
    c_ = ['Curriculum Vitae', n, f'Woonplaats: {r.choice([p for p in PLAATS if p != pl])}', '', 'Werkervaring']
    c_ += [f'{b} - {e} {f} bij {w}. Verantwoordelijk voor beheer en ondersteuning.' for b, e, f, w, _m in lc]
    c_ += ['', 'Opleiding', 'MBO ICT, ROC Midden Nederland', '', 'Vaardigheden', 'Office, ITIL']
    schrijf(f'p{i}/CV {n}.txt', a, f'P{i}'); schrijf(f'p{i}/{bnaam}', b_, f'P{i}')
    schrijf(f'p{i}/cv_{n.replace(" ", "_")}_2.txt', c_, f'N{i}')
    if i < 2:   # collega-naamgenoot: 1 zelfde werkgever + beginjaar, verder andere loopbaan
        b0, e0, _f0, w0, _m0 = lb[0]
        col = ['Curriculum Vitae', n, '', 'Werkervaring', f'{b0} - heden Servicedesk medewerker bij {w0}. Support.',
               f'{b0 - 6} - {b0} Beheerder bij Havenlogic Zuid. Beheer.', '', 'Opleiding', 'MBO ICT, ROC Midden Nederland']
        schrijf(f'p{i}/{n} collega.txt', col, f'C{i}')
for j, n in enumerate(['Pieter Zandvoort', 'Karin Molenbeek']):
    schrijf(f'bureau{j}.txt', BUREAU + ['Curriculum Vitae', n, 'Werkervaring', f'2015 - heden Tester bij Kanaalsoft {j}.',
                                        'Opleiding', 'HBO Informatica, Avans Hogeschool'], f'B{j}')
env = dict(os.environ, CV_AI='0')
res = subprocess.run([sys.executable, str(ROOT / 'cv_crawler.py'), '--root', str(src), '--output', str(uit)],
                     env=env, capture_output=True, text=True, timeout=900)
rows = list(csv.DictReader(open(uit / 'cv_bestanden.csv', encoding='utf-8-sig'), delimiter=';'))
kid = {str(Path(x['oorspronkelijk_bestand']).resolve()): x['persoon_id'] for x in rows}
check('alle testbestanden in de uitvoer', all(p in kid for p in verwacht), [p for p in verwacht if p not in kid][:3] or res.stderr[-300:])
for i, n in enumerate(NAMEN):
    pv = {kid.get(p) for p, g in verwacht.items() if g == f'P{i}'}
    nv = {kid.get(p) for p, g in verwacht.items() if g in (f'N{i}', f'C{i}')}
    soort = 'bureau-cv' if i in (1, 3, 4) else ('andere opmaak ' + str(i % 3))
    check(f'{n}: beide versies samen ({soort}, 1 zonder contact)', len(pv) == 1 and None not in pv, pv)
    check(f'{n}: naamgeno(o)t(en) apart', not (nv & pv) and None not in nv, (pv, nv))
shutil.rmtree(tmp, ignore_errors=True)
print('DUBBEL-VERSIES OK' if not fout else f'{len(fout)} FOUT(EN)'); sys.exit(1 if fout else 0)
