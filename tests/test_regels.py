"""Controleert alle afgesproken regels van de scanner, zonder internet (zoekdiensten nagebootst).
Draait op Windows, Mac (M) en Mac (Intel) bij elke build."""
import csv
import io
import os
import shutil
import sys
import tempfile
import zipfile
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace', line_buffering=True)
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', errors='replace', line_buffering=True)
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ['TAVILY_API_KEY'] = 'test'
os.environ['SERPER_API_KEY'] = 'test'
os.environ['CV_AI'] = '0'          # zonder echt AI-model testen; AI-gedrag wordt hieronder nagebootst
import cv_crawler as c  # noqa: E402
import traceback
def _hook(t, v, tb):
    print('::error::CRASH ' + ''.join(traceback.format_exception(t, v, tb)).replace(chr(10), ' | ')[-900:])
    sys.__excepthook__(t, v, tb)
sys.excepthook = _hook

FOUTEN = []


def check(naam, ok, info=''):
    print(('OK   ' if ok else 'FOUT ') + naam + ('' if ok else f'  -> {info}'))
    if not ok:
        print(f'::error::{naam} -> ' + str(info).replace(chr(10), ' ')[:600])
    if not ok:
        FOUTEN.append(naam)


def online(naam, cv_tekst, werkgever, woonplaats, tavily, google=None, functie='', opleidingen='', stage=''):
    """Eén persoon online laten opzoeken met nagebootste Tavily/Google. Geeft de rijen terug."""
    out = Path(tempfile.mkdtemp())
    c.tavily_search = lambda *a, **k: tavily
    c.google_search = google or (lambda q: ([], 'Serper'))
    db = c.db_open(out)
    row = dict(path=str(out / 'cv.docx'), name=naam, status='cv', snippet=c.normalized_snippet(cv_tekst),
               employer=werkgever, woonplaats=woonplaats, opleidingen=opleidingen, stage=stage, sha=naam,
               classifier_version=c.CLASSIFIER_VERSION)
    db.execute(f"insert into docs({','.join(row)}) values({','.join('?' * len(row))})", list(row.values()))
    db.commit()
    person = dict(naam=naam, persoon_id='p1', email='x@voorbeeldmail.nl', telefoon='+31600000000', linkedin='',
                  huidige_functie=functie, laatst_bekende_werkgever=werkgever, woonplaats=woonplaats)
    c.online_suggestions([person], out, db=db, delay=0)
    rows = list(csv.DictReader(open(out / 'online_suggesties.csv', encoding='utf-8-sig'), delimiter=';'))
    c.merge_online_suggestions([person], out)
    excel, _cols = c.personen_overzicht([dict(person, kandidaat_id='SNK', cv_id='SNCV', cv_bestand='',
                                             cv_pad='', cv_archief='')])
    shutil.rmtree(out, ignore_errors=True)
    best = next((r for r in rows if r['type'] == 'linkedin' and r['keuze'] in ('beste', 'niets')), {})
    return best, excel[0]


def li(slug, title, content):
    return {'url': f'https://nl.linkedin.com/in/{slug}', 'title': title, 'content': content}


# 1. Enige profiel + 1 echt kenmerk (ook grote werkgever) = zeker, met uitleg
b, x = online('Bram Hoekstra', 'Teamleider bij Coolblue', 'Coolblue', '',
              [li('bramhoekstra', 'Bram Hoekstra - Teamleider at Coolblue', 'Teamleider at Coolblue')])
check('enige profiel + Coolblue = zeker', b.get('zekerheid') == 'waarschijnlijk', b)
check('uitleg "enige LinkedIn-profiel"', 'enige LinkedIn-profiel' in b.get('overeenkomst', ''), b)
check('Excel: vinkje bij zeker', '✓ zeker' in x['gezocht_zekerheid'], x)

# 2. Enige profiel + alleen 1 plaats = controleren
b, _ = online('Kees Leeg', 'Bakker bij Bakkerij Fictief', 'Bakkerij Fictief', 'amsterdam',
              [li('keesleeg', 'Kees Leeg - Student', 'Student. Amsterdam.')])
check('enige profiel + 1 plaats = controleren', b.get('zekerheid') == 'controleren', b)

# 3. Enige profiel + 2 plaatsen = zeker (Sander van Rijswijk)
b, _ = online('Sander van Rijswijk', 'Analist bij ING Erasmus Universiteit Rotterdam Amsterdam', 'ING', 'amsterdam',
              [li('sandervanrijswijk', 'Sander van Rijswijk - Analist', 'Amsterdam. Studied in Rotterdam.')])
check('enige profiel + 2 plaatsen = zeker', b.get('zekerheid') == 'waarschijnlijk', b)
check('geen "rijswijk" als plaats uit de achternaam', 'rijswijk' not in b.get('overeenkomst', '').replace('van rijswijk', ''), b)

# 4. Alleen naamgenoten, niets klopt -> niet invullen, duidelijke uitleg
b, x = online('Mark de Jong', 'Planner bij Transportbedrijf Noordkaap', 'Transportbedrijf Noordkaap', 'zwolle',
              [li('markdejong1', 'Mark de Jong - Sales', 'Sales bij Philips. Eindhoven.'),
               li('markdejong2', 'Mark de Jong - Docent', 'Docent. Groningen.')], functie='Planner')
check('naamgenoten zonder overeenkomst: geen LinkedIn', x['linkedin'] == '', x)
check('naamgenoten: duidelijke bron-tekst', 'niets uit het cv klopt' in x['linkedin_bron'], x)
check('naamgenoten: niet "controleren"', 'controleren' not in x['gezocht_zekerheid'], x)

# 5. Er klopt wel iets (Monteur) bij meerdere profielen -> controleren
b, _ = online('Kevin de Vries', 'Monteur bij Installatietechniek Dijkzicht', 'Installatietechniek Dijkzicht', 'leeuwarden',
              [li('kevin1', 'Kevin de Vries - Monteur', 'Monteur. Utrecht.'), li('kevin2', 'Kevin de Vries - Kok', 'Kok.')],
              functie='Monteur')
check('iets klopt (functie) = controleren', b.get('zekerheid') == 'controleren', b)

# 6. Planner is geen typfout van 'plannen'
b, _ = online('Mark de Jong', 'Planner bij Fictief', 'Fictief Transport', 'zwolle',
              [li('mdj', 'Mark de Jong', 'Ik hou van plannen. Groningen.'), li('mdj2', 'Mark de Jong', 'Leraar')], functie='Planner')
check('planner ≠ plannen', 'plannen' not in (b.get('overeenkomst') or ''), b)

# 7. QTB: korte bedrijfsnaam via Google, profiel dat Tavily miste
def g_qtb(q):
    if '"qtb"' in q.lower():
        return [li('eva-kramer-qtb', 'Eva Kramer - QTB | LinkedIn', 'Medewerker bij QTB.')], 'Serper'
    return [], 'Serper'
b, _ = online('Eva Kramer', 'Medewerker bij QTB Opleidingen en Trainingen', 'QTB Opleidingen en Trainingen', '',
              [li('eva-kramer-4f1a2c9', 'Eva Kramer - Docent', 'Docent. Zwolle.')], google=g_qtb, functie='Medewerker')
check('QTB via Google (korte naam) = zeker', b.get('zekerheid') == 'waarschijnlijk' and 'qtb' in b.get('gevonden_waarde', ''), b)

# 8. Organisatie met 'opleiding' in de naam telt ook onder Opleiding op het profiel
b, _ = online('Eva Kramer', 'Medewerker bij QTB Opleidingen en Trainingen', 'QTB Opleidingen en Trainingen', '',
              [li('eva-kramer-yx', 'Eva Kramer - YER', 'Opleiding: QTB Opleidingen en Trainingen, Fictief Marketing Instituut'),
               li('eva-kramer-2', 'Eva Kramer - Docent', 'Docent')], functie='Medewerker')
check('QTB onder opleiding telt', b.get('zekerheid') == 'waarschijnlijk' and 'eva-kramer-yx' in b.get('gevonden_waarde', ''), b)

# 9. Losse woorden 'stage' en cv-woorden tellen niet
b, _ = online('Romal Testman', 'Stagiair AI-toepassing bij Bedrijfje Fictief', 'IKEA', 'haarlem',
              [li('romal-t', 'Romal Testman - Intern / Stagiair AI-toepassing', 'Stagiair. Heemstede.'),
               li('romal-t2', 'Romal Testman', 'Kok')])
check('woord "stage"/"toepassing" telt niet', 'stage' not in (b.get('overeenkomst') or '').lower()
      and 'cv-woord' not in (b.get('overeenkomst') or ''), b)

# 10. Zoekfout = niet 'niet gevonden'
def kapot(*a, **k):
    raise c.urllib.error.URLError('CERTIFICATE_VERIFY_FAILED')
out = Path(tempfile.mkdtemp())
c.tavily_search = kapot
c.google_search = lambda q: ([], 'Serper')
db = c.db_open(out)
db.execute("insert into docs(path,name,status,snippet,employer,sha,classifier_version) values('a','Jan Fout','cv','x','Y','j',?)",
           (c.CLASSIFIER_VERSION,))
db.commit()
c.online_suggestions([dict(naam='Jan Fout', persoon_id='p', email='a@b.nl', telefoon='+316', linkedin='',
                           huidige_functie='', laatst_bekende_werkgever='Y', woonplaats='')], out, db=db, delay=0)
p = out / 'online_suggesties.csv'
rows = list(csv.DictReader(open(p, encoding='utf-8-sig'), delimiter=';')) if p.exists() else []
check('zoekfout wordt geen "niet gevonden"', not any(r['zekerheid'] == 'niet gevonden' for r in rows), rows)

# 11. Woonplaats: adresregel, geen schoolnaam, geen achternaam
check('woonplaats uit adres', c.woonplaats_from('Romal Omid\nHogeschool van Amsterdam\nAdres X 6, 2037 GJ Haarlem') == 'haarlem')
check('woonplaats niet uit achternaam', c.woonplaats_from('Curriculum Vitae\nSander van Rijswijk\nAmsterdam') == 'amsterdam')

# 12. Typfouten
for a, bb, want in [('software engineering', 'sofware engineer', True), ('codewerk', 'codwerk', True),
                    ('datafabriek', 'datafabreik', True), ('planner', 'plannen', False),
                    ('consultant', 'consulent', False), ('developer', 'designer', False)]:
    check(f'typfout {a} ~ {bb} = {want}', c.phrase_like(a, bb) == want)

# 13. Eigen API-sleutels nooit scannen
tmp = Path(tempfile.mkdtemp())
(tmp / '.cv-scanner-windows').mkdir()
(tmp / '.cv-scanner-windows' / 'tavily_sleutel.txt').write_text('tvly-geheim')
(tmp / 'serper_sleutel.txt').write_text('geheim')
(tmp / 'cv.txt').write_text('cv')
found = [p.name for p in c.iter_files([tmp], tmp / 'uit', deep=True)]
check('sleutelbestanden worden niet gescand', found == ['cv.txt'], found)

# 14. Cv in OneDrive: standaard kopiëren, origineel blijft staan
src = Path(tempfile.mkdtemp())
(src / 'OneDrive - Test').mkdir()
shutil.copy(ROOT / 'tests' / 'voorbeeld' / 'CV Lotte Verbeek.txt', src / 'OneDrive - Test')
uit = Path(tempfile.mkdtemp())
c.main(['--root', str(src), '--output', str(uit)])
c.main(['--output', str(uit), '--opschonen'])
check('cloud-cv blijft in OneDrive staan', (src / 'OneDrive - Test' / 'CV Lotte Verbeek.txt').exists())
check('kopie staat in Alle_cvs', any((uit / 'Alle_cvs').glob('*Lotte*')))

# 15. Pages-bestand (als map) lezen
pg = Path(tempfile.mkdtemp()) / 'Test.pages'
pg.mkdir()
(pg / 'index.xml').write_text('<sl:document xmlns:sl="x" xmlns:sf="y"><sf:p>Curriculum Vitae Jan Pages</sf:p></sl:document>')
txt, _n, _p = c.read_document(pg)
check('Pages-map lezen', 'Jan Pages' in txt, txt[:80])

# 16. Twijfel verminderen: leeg formulier = geen cv, Engels cv = cv
form = 'Intakeformulier\nNaam: Eva de Groot\nWerkervaring\nHuidige functie: ________\nWerkgever: ________\nOpleiding\nHoogste opleiding: ________\nGewenste uren: ____'
check('leeg formulier = sjabloon', c.is_template(form))
eng = ('Resume\nTom Hendriks\nExperience\n2019 - present  Product Owner at Fintech\n2015 - 2019  Business Analyst at Consultancy\n'
       'Education\n2011 - 2015  Business Administration, University of Amsterdam')
h, *_ = c.cv_signals(eng, 'Resume_Tom_Hendriks.pdf')
check('Engelse kopjes herkend', {'werkervaring', 'opleiding'} <= h, h)
check('onleesbare scan = geen cv', c.decide_status('', 'cv_scan.pdf', '', [], [], [], 0) == 'other')

# --- Versie 20: namen opschonen (uit echte export van de M-Mac, namen verzonnen) ---
for ruw, goed in [('Naam Henk Winters', 'Henk Winters'), ('Persoonlijke Gegevens', ''), ('Algemene Gegevens', ''),
                  ('Geert van TuinenGeert van Tuinen', 'Geert van Tuinen'), ('Emre KaplanCurriculum Vitae', 'Emre Kaplan'),
                  ('Name Jan Smit Sex Male', 'Jan Smit'), ('Drs. Paul van Dorsten CISSP', 'Paul van Dorsten'),
                  ('Backend Developer', ''), ('Voorbeeld Experts BV', ''), ('The Netherlands', ''), ('Woonplaats Amsterdam', ''),
                  ('Marc de Gier Contractmanager Belastingdienst', 'Marc de Gier'), ('Curricullum Vitae', ''),
                  ('Sander van Rijswijk', 'Sander van Rijswijk'), ('Jan de Vries', 'Jan de Vries'), ('Naam Mark', ''),
                  ('Medewerker Nils Roest', 'Nils Roest'), ('Wouter Van Velzen Nl', 'Wouter Van Velzen')]:
    check(f'naam "{ruw}" -> "{goed}"', c.clean_person_name(ruw) == goed, c.clean_person_name(ruw))
tekst = 'Persoonlijke gegevens\nNaam Henk Winters\nGeboortedatum 01-01-1980\nWerkervaring\n2010 - heden Ontwikkelaar bij Codewerk'
check('naam uit tabel "Naam  Henk Winters"', c.guess_name(tekst, 'cv', []) == 'Henk Winters', c.guess_name(tekst, 'cv', []))
tekst = 'Voornaam\tHenk\nAchternaam\tWinters\nWerkervaring'
check('voornaam + achternaam zonder dubbele punt', c.guess_name(tekst, 'cv', []) == 'Henk Winters', c.guess_name(tekst, 'cv', []))

# --- Versie 20: rommel telt nooit als bewijs voor 'zeker' ---
for lbl, term in [('werkgever', 'india'), ('werkgever', 'director'), ('school', 'university of'), ('school', 'roc de'),
                  ('werkgever', 'ontwikkelen van een nieuw organisatiemod'), ('functie', 'bangalore'),
                  ('stagebedrijf', 'werkzaamheden'), ('werkgever', 'technical services')]:
    check(f'geen bewijs: {lbl} "{term}"', not c.evidence_term_ok(lbl, term))
for lbl, term in [('werkgever', 'coolblue'), ('school', 'hogeschool van amsterdam'), ('school', 'vrije universiteit'),
                  ('functie', 'linux engineer')]:
    check(f'wel bewijs: {lbl} "{term}"', c.evidence_term_ok(lbl, term))
b, x = online('Lars Pieters', 'Lars Pieters Developer | India 2015 - heden', 'India', 'amsterdam',
              [li('lars-pieters-1', 'Lars Pieters - Developer India', 'Developer at India Tech. Amsterdam')])
check('alleen "India" + plaats = niet zeker', b.get('zekerheid') != 'waarschijnlijk', b)
b, x = online('Lars Pieters', 'Lars Pieters woont in Amsterdam', '', 'amsterdam',
              [li('lars-pieters-1', 'Lars Pieters', 'Werkt in Amsterdam. Woont in Amsterdam')])
check('zelfde plaats 2x (woon + werk) = niet zeker', b.get('zekerheid') != 'waarschijnlijk', b)

# --- Versie 20: herkenning ---
cvtekst = ('Curriculum Vitae\nHenk Winters\nhenk.winters@voorbeeldmail.nl\n06 12345678\nPersonalia\nGeboren 1980\n'
           'Opdrachten\njan 2018 – dec 2020 Ontwikkelaar, Codewerk\nmrt 2021 - heden Architect, Datafabriek\n'
           'Opleidingen & cursussen\nHBO Informatica')
nm = c.guess_name(cvtekst, 'document', ['henk.winters@voorbeeldmail.nl'])
sc, rs = c.classify(cvtekst, 'document', nm, ['henk.winters@voorbeeldmail.nl'], ['+31612345678'], [])
st = c.decide_status(cvtekst, 'document', nm, ['henk.winters@voorbeeldmail.nl'], ['+31612345678'], [], sc)
check('consultant-cv (Personalia/Opdrachten/maanden) = cv', st == 'cv', (nm, sc, rs, st))

check('dubbele naam + kopje', c.clean_person_name('Jan Smitjan Smitwerkervaringwerkervaring') == 'Jan Smit')
for tekst, verwacht in [
    ("Voorbeeld Experts BV\nCV van Lisa Groen\nNaam: Lisa Groen\nlisa.groen@voorbeeldmail.nl\n06-00000112\nWerkervaring\n"
     "2019 - heden Consultant, Voorbeeld Experts BV\nOpleiding\nHBO Bedrijfskunde\n", 'cv'),
    ("Lieke Dam\nlieke.dam@voorbeeldmail.nl\n06-00000110\nUtrecht, 3 maart 2024\n\nBeste heer/mevrouw,\n\nMet veel "
     "enthousiasme reageer ik op uw vacature voor data analist.\n\nMet vriendelijke groet,\nLieke Dam\n", 'other'),
    ("Projectverslag Smart Parking\nAuteur: Ruben Vos\nruben.vos@student.voorbeeld.nl\nSamenvatting\nDit verslag.\n"
     "Projecten\nFase 1 2023 - 2024\nConclusie\nGeslaagd.\n", 'other')]:
    em = c.emails_in(tekst); ph = c.phones_in(tekst); nm = c.guess_name(tekst, 'document', em)
    sc, rs = c.classify(tekst, 'document', nm, em, ph, [])
    st = c.decide_status(tekst, 'document', nm, em, ph, [], sc)
    check(f'herkenning: {tekst.splitlines()[0][:30]} = {verwacht}', st == verwacht, (nm, sc, rs, st))

import json as _json
def _d(name, e, p, sha):
    return dict(name=name, emails=_json.dumps([e]), phones=_json.dumps([p]), links='[]', sha=sha, snippet='', tokens='[]')
check('zelfde mail+tel, naam + bedrijfsnaam = samenvoegen',
      c.safe_pair(_d('Bas Hilberts', 'b@x.nl', '+31600000001', 'a'), _d('Bas Hilberts Linkit', 'b@x.nl', '+31600000001', 'b'))[0])
check('zelfde mail, andere naam = niet samenvoegen',
      not c.safe_pair(_d('Bas Hilberts', 'b@x.nl', '+31600000001', 'a'), _d('Jan de Vries', 'b@x.nl', '+31600000001', 'b'))[0])
check('aan elkaar geplakte e-mail', c.emails_in('1330info@voorbeeld.comwww.voorbeeld.com') == ['info@voorbeeld.com'], c.emails_in('1330info@voorbeeld.comwww.voorbeeld.com'))
for kop in ('Mijn Kracht', 'Mijn Missie', 'Diploma Deelcertificaat'):
    check(f'"{kop}" is geen naam', c.clean_person_name(kop) == '')

for tekst, goed in [("Naam Belonje\nVoornamen Johannes Pieter\nRoepnaam Hans", 'Hans Belonje'),
                    ("Achternaam Beukema\nRoepnaam Peter", 'Peter Beukema'), ("Naam Mark\nAchternaam Nauta", 'Mark Nauta'),
                    ("Achternaam: de Kleijn\nVoornaam: Sanne", 'Sanne de Kleijn')]:
    check(f'naamvelden -> {goed}', c.guess_name(tekst, 'document', []) == goed, c.guess_name(tekst, 'document', []))

check('voorletters + e-mail -> voornaam', c.name_with_email('Ncp Kramer', ['niels_kramer@hotmail.com']) == 'Niels Kramer')
check('andere voornaam niet vervangen', c.name_with_email('Bas Kramer', ['niels_kramer@hotmail.com']) == 'Bas Kramer')
check('cijfers + linkedin aan e-mail geplakt', c.emails_in('41844178nathanmiddelham@gmail.comlinkedin.com') == ['nathanmiddelham@gmail.com'])
check('"Nationality Dutch" is geen naam', c.clean_person_name('Nationality Dutch') == '')

for ruw, goed in [('Baris YAPAR', 'Baris Yapar'), ('MENKVELD Jan-Willem', 'Jan-Willem Menkveld'), ('MACROBUTTON Johan Vermont', 'Johan Vermont'),
                  ('Hendrik Ido Ambacht', ''), ('Over Mij', ''), ('Wsm Perquin Productowner', 'Wsm Perquin')]:
    check(f'naam "{ruw}" -> "{goed}"', c.tidy_name(c.clean_person_name(ruw)) == goed, c.tidy_name(c.clean_person_name(ruw)))
check('achternaam eerst + e-mail', c.name_order_by_email('Elmensdorp Henri', ['ron.elmensdorp@x.nl']) == 'Henri Elmensdorp')
check('label aan e-mail geplakt', c.emails_in('haage-mailadresinfo@voorbeeld.comtelefoon') == ['info@voorbeeld.com'])
check('e-mail van iemand anders herkend', c.email_owner_mismatch('Dennis Kortekaas', 'bart.plasmeijer@voorbeeldmail.nl'))
check('eigen e-mail niet gemarkeerd', not c.email_owner_mismatch('Artem Zaitsev', 'artem.zajtsev@voorbeeldmail.nl'))

# --- AI-controle (nagebootst, zonder model): AI beslist nooit alleen ---
import ai_rechter
_orig = (ai_rechter.available, ai_rechter.judge)
def _fake(oordeel):
    ai_rechter.available = lambda: True
    ai_rechter.judge = lambda cv, prof: {'oordeel': oordeel(prof), 'past': ['Coolblue'], 'botst': ['CEO vs teamleider'], 'sec': 0}
_fake(lambda prof: 'nee' if 'CEO' in prof else 'ja')
b, x = online('Bram Hoekstra', 'Teamleider klantenservice bij Coolblue', 'Coolblue', '',
              [li('bramhoekstra', 'Bram Hoekstra - CEO at Coolblue', 'CEO at Coolblue since 1999')], functie='Teamleider klantenservice')
check('AI: zelfde bedrijf maar CEO = afgewezen (niet zeker)', b.get('zekerheid') == 'niet gevonden' and 'AI-controle' in b.get('overeenkomst', ''), b)
_fake(lambda prof: 'ja')
b, x = online('Bram Hoekstra', 'Teamleider klantenservice bij Coolblue', 'Coolblue', '',
              [li('bramhoekstra', 'Bram Hoekstra - Medewerker', 'Coolblue')], functie='Teamleider klantenservice')
check('AI ja + alleen groot bedrijf (1 ding) = controleren', b.get('zekerheid') == 'controleren', b)
b, x = online('Ruben Hofstede', 'Software ontwikkelaar bij Topicus Deventer', 'Topicus', 'deventer',
              [li('rubenhofstede', 'Ruben Hofstede - Software Developer at Topicus', 'Software Developer at Topicus. Deventer')])
check('AI ja + specifieke werkgever = zeker', b.get('zekerheid') == 'waarschijnlijk' and 'AI-controle' in x['gezocht_waarom'], (b, x['gezocht_waarom']))
_fake(lambda prof: 'onzeker')
b, x = online('Ruben Hofstede', 'Software ontwikkelaar bij Topicus Deventer', 'Topicus', 'deventer',
              [li('rubenhofstede', 'Ruben Hofstede - Software Developer at Topicus', 'Software Developer at Topicus. Deventer')])
check('AI onzeker + specifieke werkgever + plaats = zeker (regels)', b.get('zekerheid') == 'waarschijnlijk', b)
ai_rechter.available, ai_rechter.judge = _orig

for ruw, goed in [('+31 06 55857419', '+31655857419'), ('+31 6 1853 358', ''), ('+27 1172624', ''), ('+44 7700 900123', '+447700900123')]:
    check(f'telefoon {ruw} -> {goed or "afgekeurd"}', c.norm_phone(ruw) == goed, c.norm_phone(ruw))

# --- Tavily-tegoed op: verder via Google ---
import urllib.error as _ue
def _tav_op(*a, **k):
    raise _ue.HTTPError('', 432, 'op', None, None)
_old_online_tav = c.tavily_search
def _online_tav_op(naam, cvt, werkgever, woonplaats, google):
    out = Path(tempfile.mkdtemp())
    c.tavily_search = _tav_op
    c.google_search = google
    db = c.db_open(out)
    row = dict(path=str(out / 'cv.docx'), name=naam, status='cv', snippet=c.normalized_snippet(cvt), employer=werkgever,
               woonplaats=woonplaats, sha=naam, classifier_version=c.CLASSIFIER_VERSION)
    db.execute(f"insert into docs({','.join(row)}) values({','.join('?' * len(row))})", list(row.values())); db.commit()
    person = dict(naam=naam, persoon_id='p1', email='x@voorbeeldmail.nl', telefoon='+31600000000', linkedin='',
                  huidige_functie='', laatst_bekende_werkgever=werkgever, woonplaats=woonplaats)
    c.online_suggestions([person], out, db=db, delay=0)
    rows = list(csv.DictReader(open(out / 'online_suggesties.csv', encoding='utf-8-sig'), delimiter=';'))
    return next((r for r in rows if r['type'] == 'linkedin'), {})
vragen = []
def _g(q):
    vragen.append(q)
    return [li('ruben-hofstede', 'Ruben Hofstede - Developer at Topicus', 'Topicus. Deventer')], 'Serper'
b = _online_tav_op('Ruben Hofstede', 'Developer bij Topicus Deventer', 'Topicus', 'deventer', _g)
check('Tavily op -> Google gebruikt en profiel gevonden', any('site:linkedin.com/in' in q for q in vragen) and 'ruben-hofstede' in b.get('gevonden_waarde', ''), (vragen, b))
c.tavily_search = _old_online_tav

# --- AI bij twijfelgevallen (nagebootst) ---
import ai_rechter as _ar, json as _js
_o = (_ar.available, _ar.classify)
_ar.available = lambda: True
_ar.TWIJFEL_AAN = True
_ar.classify = lambda t: ({'soort': 'geen_cv', 'naam': '', 'reden': 'sollicitatiebrief'} if 'geachte' in t else
                          {'soort': 'cv', 'naam': 'henk winters', 'reden': 'loopbaan'})
_out = Path(tempfile.mkdtemp()); _db = c.db_open(_out)
for pth, name, snip, em in [('a.docx', '', 'henk winters henk winters voorbeeldmail nl werkervaring 2010 heden', ['henk.winters@voorbeeldmail.nl']),
                            ('b.docx', 'Lieke Dam', 'lieke dam geachte heer mevrouw met vriendelijke groet', ['lieke@x.nl']),
                            ('c.docx', '', 'iemand zonder contact werkervaring', [])]:
    _db.execute("insert into docs(path,name,status,snippet,sha,emails,phones,links,reason,classifier_version) values(?,?,?,?,?,?,?,?,?,?)",
                (str(_out / pth), name, 'review', snip, pth, _js.dumps(em), '[]', '[]', 'kopjes: werkervaring; werkperiodes: 1', c.CLASSIFIER_VERSION))
_db.commit()
(_out / c.DECISIONS_FILE).write_text('{}', encoding='utf-8')
c.ai_twijfel(_db, _out)
st = {r['path'].rsplit('/', 1)[-1].rsplit('\\', 1)[-1]: (r['status'], r['name']) for r in _db.execute('select path,status,name from docs')}
check('AI twijfel: cv + naam uit tekst + contact = cv', st['a.docx'] == ('cv', 'Henk Winters'), st)
check('AI twijfel: brief = geen cv', st['b.docx'][0] == 'other', st)
check('AI twijfel: cv zonder contact blijft twijfel', st['c.docx'][0] == 'review', st)
_ar.available, _ar.classify = _o

_ar.available = lambda: True; _ar.TWIJFEL_AAN = True
_ar.classify = lambda t: {'soort': 'cv', 'naam': 'anton bakker', 'reden': 'lijkt een cv'}
_out = Path(tempfile.mkdtemp()); _db = c.db_open(_out)
_db.execute("insert into docs(path,name,status,snippet,sha,emails,phones,links,reason,classifier_version) values(?,?,?,?,?,?,?,?,?,?)",
            (str(_out / 'ref.docx'), '', 'review', 'hierbij verklaar ik dat bas bij ons werkte anton bakker', 'ref',
             _js.dumps(['anton.bakker@voorbeeld.nl']), '[]', '[]', 'kopjes: geen; naam: nee; contact: ja; CV-kop: nee; werkperiodes: 0', c.CLASSIFIER_VERSION))
_db.commit(); (_out / c.DECISIONS_FILE).write_text('{}', encoding='utf-8')
c.ai_twijfel(_db, _out)
check('AI zegt cv bij referentiebrief zonder cv-kenmerken: blijft twijfel',
      _db.execute('select status from docs').fetchone()[0] == 'review')
_ar.available, _ar.classify = _o

check('bedrijfsregel bovenaan: naam die bij e-mail past', c.guess_name("Jak Verhuur\nLisa Groen\nWerkervaring", 'document', ['lisa.groen@voorbeeldmail.nl']) == 'Lisa Groen')

check('leverancier bovenaan (BV)', c.leverancier_from("Voorbeeld Experts BV\nCV van Lisa Groen\nWerkervaring\n2019 - heden Consultant, Codewerk BV") == 'Voorbeeld Experts BV')
check('leverancier via-regel', c.leverancier_from("Anna Visser\nVia: Detachering Voorbeeld\nWerkervaring") == 'via:Detachering Voorbeeld')
check('zzp: eigen BV met achternaam', c.bedrijf_soort('Jansen IT Consultancy BV', 'Jan Jansen', ['jan@gmail.com'])[1] == 'eigen bedrijf (zzp)')
check('zzp: eigen domein', c.bedrijf_soort('Codebloem BV', 'Jan Jansen', ['jan.jansen@codebloem.nl'])[1] == 'eigen bedrijf (zzp)')
check('via = bureau', c.bedrijf_soort('via:Detachering Voorbeeld', 'Anna Visser', [])[1] == 'bureau / leverancier')
check('zelfde bedrijf bij 3 kandidaten = bureau', c.bedrijf_soort('Voorbeeld Experts BV', 'Lisa Groen', [], shared=3)[1] == 'bureau / leverancier')
check('onduidelijk = onbekend', c.bedrijf_soort('Voorbeeld Experts BV', 'Lisa Groen', ['lisa@gmail.com'])[1] == 'onbekend')
check('werkgever in werkervaring is geen leverancier', c.leverancier_from("Lisa Groen\nWerkervaring\n2019 - heden Consultant, Codewerk BV") == '')

# --- Geen persoonsnaam te vinden: AI zegt cv -> eigen bedrijfsnaam als naam ---
_ar.available = lambda: True; _ar.TWIJFEL_AAN = True
_ar.classify = lambda t: {'soort': 'cv', 'naam': '', 'reden': 'lijkt een cv'}
_out = Path(tempfile.mkdtemp()); _db = c.db_open(_out)
_db.execute("insert into docs(path,name,status,snippet,sha,emails,phones,links,reason,leverancier,classifier_version) values(?,?,?,?,?,?,?,?,?,?,?)",
            (str(_out / 'bv.docx'), '', 'review', 'jansen it consultancy bv werkervaring 2015 heden consultant', 'bv',
             _js.dumps(['contact@jansen-it.nl']), _js.dumps(['+31600000501']), '[]', 'kopjes: werkervaring; naam: nee; werkperiodes: 1',
             'Jansen IT Consultancy BV', c.CLASSIFIER_VERSION))
_db.commit(); (_out / c.DECISIONS_FILE).write_text('{}', encoding='utf-8')
c.ai_twijfel(_db, _out)
rw = _db.execute('select status,name,reason from docs').fetchone()
check('geen persoonsnaam: bedrijfsnaam wordt de naam (AI zegt cv)', rw[0] == 'cv' and rw[1] == 'Jansen IT Consultancy BV', tuple(rw))
check('bedrijfsnaam-cv wordt niet elke scan opnieuw gelezen', c.keep_cv_name(rw[1], rw[2]) == 'Jansen IT Consultancy BV')
_ar.available, _ar.classify = _o

_es = ("Juan Garcia\nDatos personales: telefono, correo\nExperiencia laboral\n2018 - actualidad Desarrollador de software en Empresa "
       "Ejemplo. Responsable del desarrollo de aplicaciones web con Java para los clientes del sector bancario.\n2014 - 2018 Tecnico "
       "de sistemas en Redes y Datos. Gestion de redes, soporte y mantenimiento de los servidores.\nFormacion\nGrado en Ingenieria "
       "Informatica, Universidad de Barcelona\nIdiomas: espanol nativo, ingles nivel alto\nConocimientos: Java, SQL, tambien Python.")
_nl = ("Jan de Vries\nWerkervaring\n2018 - heden Developer bij Codewerk. Verantwoordelijk voor de ontwikkeling van webapplicaties "
       "met Java voor klanten in de financiele sector.\n2014 - 2018 Systeembeheerder bij Datafabriek. Beheer van het netwerk en "
       "onderhoud van de servers.\nOpleiding\nMaster Informatica, Universidad de Barcelona (uitwisseling) en Universiteit van Amsterdam\n"
       "Talen: Nederlands, Engels, Spaans\nKennis: Java, SQL, ook Python. Ik werk graag in een team en zoek een uitdaging binnen de IT.")
check('taal: Spaans cv = es', c.cv_taal(_es) == 'es', c.cv_taal(_es))
check('taal: Nederlands cv met Spaanse universiteit = nl', c.cv_taal(_nl) == 'nl', c.cv_taal(_nl))
check('taal: werkt ook op opgeslagen tekst (zonder accenten)', c.cv_taal(c.normalized_snippet(_es)) == 'es')

check('woont in India (+91) = buitenland', c.woonland(c.normalized_snippet("Rahul\nBangalore, India\n+91 98450 12345"), ['+919845012345'])[0] == 'buitenland')
check('Indiase naam, woont in Amsterdam = nl', c.woonland(c.normalized_snippet("Arjun Mehta\nAmsterdam\n06-00000601\n2014 - 2019 Infosys, Pune, India"), ['+31600000601'])[0] == 'nl')
check('woont in België (+32) = blijft erin', c.woonland(c.normalized_snippet("Jan Peeters\nAntwerpen, Belgie\n+32 470 00 00 00"), ['+32470000000'])[0] != 'buitenland')
check('woont in Spanje (Engels cv) = niet voor Loxo', c.woonland(c.normalized_snippet("John Smith\nMadrid, Spain\n+34 600 000 000"), ['+34600000000'])[0] == 'buitenland')
check('Engels cv, woont in Utrecht = blijft erin', c.woonland(c.normalized_snippet("John Smith\nUtrecht\njohn@x.com\nExperience"), [])[0] == 'nl')
check('Engels cv, woont in Antwerpen zonder nummer = blijft erin', c.woonland(c.normalized_snippet("John Smith\nAntwerp, Belgium\nExperience"), [])[0] == 'nl')
check('woont in Londen (+44) = niet voor Loxo', c.woonland(c.normalized_snippet("John Smith\nLondon, UK\n+44 7700 900123"), ['+447700900123'])[0] == 'buitenland')
check('geen adres of nummer = onduidelijk (blijft erin)', c.woonland(c.normalized_snippet("Kees Mol\nkees@x.nl\nWerkervaring"), [])[0] == '')

_pad = ' x' * 250
check('woont buitenland, werkte/studeerde in NL = band (blijft erin)',
      c.nl_be_band(c.normalized_snippet("Jan\nLondon\n+44 7700 900123" + _pad + "\nWerkervaring\nDeveloper bij Datafabriek, Utrecht\nOpleiding\nHogeschool Utrecht"), []) != '')
check('woont in India, geen band met NL/BE = geen band',
      c.nl_be_band(c.normalized_snippet("Rahul\nBangalore, India\n+91 98450 12345" + _pad + "\nExperience\nDeveloper at Infosys, Pune\nEducation\nB.Tech, Anna University"), []) == '')

import datetime as _dt
_jr = _dt.date.today().year
_j, _ = c.laatst_buitenland(c.normalized_snippet(f"Arjun\nAmsterdam\nExperience\n{_jr-1} - Present Developer, Adyen, Amsterdam\n{_jr-6} - {_jr-1} Developer, Infosys, Pune, India"))
check('laatst in India 1 jaar geleden = korter dan 4 jaar', _j == _jr - 1, _j)
_j, _ = c.laatst_buitenland(c.normalized_snippet(f"Arjun\nAmsterdam\nExperience\n{_jr-7} - Present Developer, Adyen, Amsterdam\n{_jr-12} - {_jr-7} Developer, Infosys, Pune, India"))
check('laatst in India 7 jaar geleden = lang genoeg hier', _jr - _j >= c.MIN_JAREN_NL, _j)
_j, _ = c.laatst_buitenland(c.normalized_snippet("Jan\nUtrecht\nWerkervaring\n2015 - heden Developer, Codewerk, Utrecht"))
check('altijd in NL = geen buitenlandperiode', _j == 0, _j)
check('postcode telt als NL', c.woonland(c.normalized_snippet("Jan\n1012 AB Amsterdam\njan@x.nl"), [])[0] == 'nl')

check('NL-er, nu 3 jaar Londen, daarvoor jaren NL = genoeg jaren',
      c.jaren_nl_be(c.normalized_snippet(f"Jan\nWerkervaring\n{_jr-3} - heden Developer, Monzo, London\n{_jr-10} - {_jr-3} Developer, Datafabriek, Utrecht")) >= c.MIN_JAREN_NL)
check('havo in NL = hier opgegroeid', c.jaren_nl_be(c.normalized_snippet("Jan\nOpleiding\nHavo, Utrecht")) >= c.MIN_JAREN_NL)
check('pas 1 jaar in NL = te weinig jaren',
      c.jaren_nl_be(c.normalized_snippet(f"Arjun\nExperience\n{_jr-1} - Present Developer, Adyen, Amsterdam\n{_jr-6} - {_jr-1} Developer, Infosys, Pune, India")) < c.MIN_JAREN_NL)

print()
print('ALLE REGELS OK' if not FOUTEN else f'{len(FOUTEN)} FOUT(EN): ' + ', '.join(FOUTEN))
sys.exit(1 if FOUTEN else 0)
