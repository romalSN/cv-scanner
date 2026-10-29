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


# 1. Enige profiel + specifiek bedrijf = zeker, met uitleg. Groot bedrijf alleen (Coolblue) = controleren (0 fouten).
b, x = online('Bram Hoekstra', 'Teamleider bij Datafabriek Noord', 'Datafabriek Noord', '',
              [li('bramhoekstra', 'Bram Hoekstra - Teamleider at Datafabriek Noord', 'Teamleider at Datafabriek Noord')])
check('enige profiel + specifiek bedrijf = zeker', b.get('zekerheid') == 'waarschijnlijk', b)
check('uitleg "enige LinkedIn-profiel"', 'enige LinkedIn-profiel' in b.get('overeenkomst', ''), b)
check('Excel: vinkje bij zeker', '✓ zeker' in x['gezocht_zekerheid'], x)
b, x = online('Bram Hoekstra', 'Teamleider bij Coolblue', 'Coolblue', '',
              [li('bramhoekstra', 'Bram Hoekstra - Teamleider at Coolblue', 'Teamleider at Coolblue')])
check('enige profiel + alleen groot bedrijf (Coolblue) = controleren', b.get('zekerheid') == 'controleren', b)

# 2. Enige profiel + alleen 1 plaats = controleren
b, _ = online('Kees Leeg', 'Bakker bij Bakkerij Fictief', 'Bakkerij Fictief', 'amsterdam',
              [li('keesleeg', 'Kees Leeg - Student', 'Student. Amsterdam.')])
check('enige profiel + 1 plaats = controleren', b.get('zekerheid') == 'controleren', b)

# 3. Enige profiel + 2 plaatsen = zeker (Sander van Rijswijk)
b, _ = online('Sander van Rijswijk', 'Analist bij ING Erasmus Universiteit Rotterdam Amsterdam', 'ING', 'amsterdam',
              [li('sandervanrijswijk', 'Sander van Rijswijk - Analist', 'Amsterdam. Studied in Rotterdam.')])
check('enige profiel + alleen 2 plaatsen = controleren (plaatsen zijn geen specifiek bewijs)', b.get('zekerheid') == 'controleren', b)
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
check('achternaam eerst + e-mail', c.name_order_by_email('Elmensdorp Henri Ernst Ronald', ['ron.elmensdorp@x.nl']) == 'Henri Ernst Ronald Elmensdorp')
check('niet omdraaien als voornaam niet in e-mail past (Dennis Andre / gerk.dennis@)', c.name_order_by_email('Dennis Andre', ['gerk.dennis@gmail.com']) == 'Dennis Andre')
check('e-mail: label "emailaddress1" eraf', c._fix_email('emailaddress1tom.coenraad@synergy-ict.nl') == 'tom.coenraad@synergy-ict.nl')
check('e-mail: "emaily@" blijft (geen label)', c._fix_email('emaily@x.nl') == 'emaily@x.nl')
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

# --- Stap 1: taal, woonplaats NL/BE, 5 jaar (marge 4), AI bij twijfel, anders controleren ---
_Y = _dt.date.today().year
def _doc(text, taal=None, opl='', werk=''):
    return {'snippet': c.normalized_snippet(text), 'taal': c.cv_hoofdtaal(text) if taal is None else taal,
            'opleidingen': opl, 'werkplaatsen': werk, 'sha': str(hash(text))}
def _filter(text, phones, emails=(), ai=None, **kw):
    return c.filter_kandidaat(_doc(text, **kw), phones, list(emails), (lambda soort: ai.get(soort)) if ai else (lambda soort: None))
_EN = ("Experience\n{a} - present Senior developer at Banco Ejemplo. Responsible for the development of the payment platform "
       "and worked with the team on the migration.\n{b} - {a} Developer at Telco Ejemplo. Developed internal tools using Java "
       "and maintained the pipelines for the apps.\nEducation\nMSc Computer Science. I have experience with agile teams and "
       "I enjoy working with international colleagues.")
fk = _filter("Carlos Rodriguez\nMadrid, Spain\n+34 600 000 000\n" + _EN.format(a=_Y-3, b=_Y-8), ['+34600000000'])
check('filter: Engels cv, woont in Madrid, geen band NL/BE = niet meegenomen (woonplaats)', fk == ('weg', 'woont niet in Nederland of België'), fk)
fk = _filter("Carlos Rodriguez\nAmsterdam\n06-00000701\n" + _EN.format(a=_Y-8, b=_Y-12), ['+31600000701'])
check('filter: Engels cv, Spaanse naam, woont in Amsterdam = mee', fk[0] == 'ok', fk)
fk = _filter("Carlos Rodriguez\nAmsterdam\n" + "Experiencia\nResponsable del desarrollo de la plataforma con el equipo y de los clientes "
             "para la empresa durante varios años.\nTrabajo con Java y Python desde hace mucho tiempo con clientes internacionales "
             "y también gestión de proyectos para el banco.", ['+31600000702'])
check('filter: Spaanstalig cv = niet meegenomen (taal), ook als iemand hier woont', fk == ('weg', 'taal van het cv'), fk)
fk = _filter("x", ['+31600000703'], taal='indiaas')
check('filter: cv in Indiase taal = niet meegenomen (taal)', fk == ('weg', 'taal van het cv'), fk)
fk = _filter(f"Rajesh Kumar\nAmsterdam\n06-00000704\nExperience\n{_Y-9} - present Developer, Adyen, Amsterdam. Responsible for the "
             f"development of payment services and worked with the team.\n{_Y-14} - {_Y-9} Developer, Infosys, Pune, India. "
             "Developed tools for the clients and maintained the systems.", ['+31600000704'])
check('filter: Indiase naam, 9 jaar in NL = mee', fk[0] == 'ok', fk)
fk = _filter(f"Rajesh Kumar\nAmsterdam\n06-00000705\nExperience\n{_Y-1} - present Developer, Adyen, Amsterdam. Responsible for the "
             f"development of payment services and worked with the team.\n{_Y-6} - {_Y-1} Developer, Infosys, Pune, India. "
             "Developed tools for the clients and maintained the systems.", ['+31600000705'])
check('filter: 1 jaar NL-werk zichtbaar, daarvoor India-werk, geen aankomstjaar = door (werkjaren zijn geen bewijs)', fk[0] == 'ok', fk)
_twijfel = (f"Rajesh Kumar\nAmsterdam\n06-00000706\nExperience\n{_Y-4} - present Developer, Adyen, Amsterdam. Responsible for the "
            f"development of payment services and worked with the team.\n{_Y-9} - {_Y-4} Developer, Infosys, Pune, India. "
            "Developed tools for the clients and maintained the systems.")
fk = _filter(_twijfel, ['+31600000706'])
check('filter: 4 jaar in NL (marge) zonder AI = door', fk[0] == 'ok', fk)
fk = _filter(_twijfel, ['+31600000706'], ai={'verblijf': 'ja'})
check('filter: 4 jaar in NL, AI bevestigt 5 jaar = mee', fk[0] == 'ok', fk)
fk = _filter(_twijfel, ['+31600000706'], ai={'verblijf': 'nee'})
check('filter: 4 jaar in NL, AI zegt nee = door (AI-vermoeden is geen bewijs)', fk[0] == 'ok', fk)
fk = _filter(f"Jan de Vries\nLondon, United Kingdom\n+44 7700 900123\nWerkervaring\n{_Y-2} - heden Developer bij Monzo. "
             f"Verantwoordelijk voor de ontwikkeling van het platform.\n{_Y-10} - {_Y-2} Developer bij Datafabriek, Utrecht. "
             "Ik heb daar veel ervaring opgedaan met het team.", ['+447700900123'], werk='utrecht')
check('filter: Nederlander nu in Londen, band met NL, zonder AI = door (tegenstrijdig, geen handwerk)', fk[0] == 'ok', fk)
fk2 = _filter(f"Jan de Vries\nLondon, United Kingdom\n+44 7700 900123\nWerkervaring\n{_Y-2} - heden Developer bij Monzo. "
             f"Verantwoordelijk voor de ontwikkeling van het platform.\n{_Y-10} - {_Y-2} Developer bij Datafabriek, Utrecht. "
             "Ik heb daar veel ervaring opgedaan met het team.", ['+447700900123'], werk='utrecht', ai={'woont': 'nee', 'woont_buiten': 'ja'})
check('filter: tegenstrijdig, AI zegt woont niet in NL/BE = niet meegenomen', fk2 == ('weg', 'woont niet in Nederland of België'), fk2)
fk = _filter("Kees Mol\nkees@x.nl\nWerkervaring\nDeveloper bij Codewerk. Ik werk graag met het team aan de ontwikkeling van "
             "de systemen en heb veel ervaring met Java.", [])
check('filter: geen woon- of verblijfsinfo = mee (geen aanname)', fk[0] == 'ok', fk)
fk = _filter("x", ['+31600000707'], taal='onduidelijk')
check('filter: taal onduidelijk zonder AI = controleren', fk[0] == 'controleren', fk)
fk = _filter("x", ['+31600000707'], taal='onduidelijk', ai={'taal': 'engels'})
check('filter: taal onduidelijk, AI zegt Engels = mee', fk[0] == 'ok', fk)
fk = _filter(f"Rajesh Kumar\nAmsterdam\n06-00000708\nExperience\n{_Y-9} - present Developer, Adyen, Amsterdam. Responsible for the "
             "development of payment services and worked with the team.\nEducation\nB.Tech Computer Science, Anna University, Chennai",
             ['+31600000708'])
check('filter: woont lang in NL, opleiding in India = mee (geen uitsluiting op opleidingsland)', fk[0] == 'ok', fk)

# --- Stap 2: huidige woonplaats ---
_ENK = ("Experience\nDeveloper. Responsible for the development of the platform and worked with the team on the migration. "
        "I have experience with agile teams and I enjoy working with colleagues.")
def _w(text, phones=(), emails=(), werk=''):
    return c.woonplaats_bepalen(c.normalized_snippet(text), list(phones), list(emails), _doc(text, werk=werk))[0]
check('woon: geen adres, geen nummer = onbekend', _w("Kees Mol\n" + _ENK) == '')
check('woon: alleen NL-nummer = nl', _w("Kees Mol\n06-00000801\n" + _ENK, ['+31600000801']) == 'nl')
check('woon: postcode + plaats = nl', _w("Kees Mol\nKerkstraat 1\n2011 AB Haarlem\n" + _ENK) == 'nl')
check('woon: Belgisch adres = nl/be', _w("Lies Peeters\nMeir 10, 2000 Antwerpen\n" + _ENK, ['+32470000000']) == 'nl')
check('woon: buitenlands adres + nummer, niets NL = buitenland', _w("Rahul\nAddress: MG Road, Bangalore, India\n+91 98450 12345\n" + _ENK, ['+919845012345']) == 'buitenland')
check('woon: geboren in India, woont in Utrecht = nl', _w("Arjun\nUtrecht\nBorn in Delhi\nNationality: Indian\n" + _ENK) == 'nl')
check('woon: alleen geboorteplaats buitenland = onbekend (niet buitenland)', _w("Arjun\nPlace of birth: Mumbai\n" + _ENK) == '')
check('woon: werkgever in de kop (at Infosys Pune) = geen woonplaats', _w("Arjun\nDeveloper at Infosys Pune\n" + _ENK) == '')
check('woon: verhuiswens naar NL telt niet als NL', _w("Rahul\nBangalore, India\nWilling to relocate to Amsterdam\n+91 98450 12345\n" + _ENK, ['+919845012345']) == 'buitenland')
check('woon: buitenlandse plaats + NL-nummer = tegenstrijdig', _w("Rahul\nBangalore, India\n06-00000802\n" + _ENK, ['+31600000802']) == 'tegenstrijdig')
check('woon: alleen buitenlands nummer = aanwijzing (geen bewijs)', _w("Rahul\n+91 98450 12345\n" + _ENK, ['+919845012345']) == 'aanwijzing_buitenland')
check('woon: jaartal in de kop is geen postcode', _w(f"Rahul\n{_Y-2} to {_Y} Bangalore, India\n" + _ENK) != 'nl')
fk = _filter("Rahul\n+91 98450 12345\n" + _ENK, ['+919845012345'])
check('filter: alleen buitenlands nummer, zonder AI = door', fk[0] == 'ok', fk)
fk = _filter("Rahul\n+91 98450 12345\n" + _ENK, ['+919845012345'], ai={'woont': 'onduidelijk'})
check('filter: alleen buitenlands nummer, AI onzeker = door', fk[0] == 'ok', fk)
fk = _filter("Rahul\n+91 98450 12345\n" + _ENK, ['+919845012345'], ai={'woont': 'nee', 'woont_buiten': 'ja'})
check('filter: alleen buitenlands nummer, AI zeker buitenland = niet meegenomen', fk[0] == 'weg', fk)
fk = _filter("Rahul\nBangalore, India\n06-00000803\n" + _ENK, ['+31600000803'], ai={'woont': 'onduidelijk'})
check('filter: tegenstrijdig, AI onzeker = door', fk[0] == 'ok', fk)
fk = _filter("Rahul\nAddress: MG Road, Bangalore, India\n+91 98450 12345\n" + _ENK, ['+919845012345'], ai={'woont': 'ja'})
check('filter: buitenlands adres maar AI ziet NL = door', fk[0] == 'ok', fk)
fk = _filter("Kees Mol\nDeveloper\n", [])
check('filter: onvolledig cv zonder woonplaats = door', fk[0] == 'ok', fk)
fk = _filter(f"Jan Bakker\nZwolle\n06-00000804\nWerkervaring\n{_Y-12} - {_Y-10} Developer bij Infosys, Pune, India. Ik werkte daar "
             f"met het team aan de ontwikkeling.\n{_Y-10} - heden Developer bij Datafabriek, Zwolle. Ik ontwikkel systemen.", ['+31600000804'])
check('filter: alleen vroeger buitenlandse werkervaring = door', fk[0] == 'ok', fk)
fk = _filter("Arjun Mehta\nUtrecht\nNationality: Indian\nBorn in Delhi\n" + _ENK, [])
check('filter: nationaliteit/geboorteplaats telt niet = door', fk[0] == 'ok', fk)

# --- Stap 2b: meerdere adressen, grensarbeid, gelijknamige plaatsen, postcodes ---
check('adres: huidig NL, vorig buitenland = nl', _w("Arjun\nHuidig adres: Kerkstraat 1, 2011 AB Haarlem\nVorig adres: MG Road, Pune, India\n" + _ENK) == 'nl')
check('adres: current buitenland, previous NL = buitenland', _w("Arjun\nCurrent address: MG Road, Bangalore, India\nPrevious address: Amsterdam\n" + _ENK, ['+919845012345']) == 'buitenland')
check('adres: datum beslist (tot 2020 Pune, sinds 2021 Amsterdam) = nl', _w(f"Arjun\nAddress: Pune, India until 2020\nAddress: Damrak 1, Amsterdam since 2021\n" + _ENK) == 'nl')
check('adres: twee adressen zonder aanduiding = tegenstrijdig (AI)', _w("Arjun\nAdres: Pune, India\nAdres: Damrak 1, Amsterdam\n" + _ENK) == 'tegenstrijdig')
fk = _filter("Arjun\nAdres: Pune, India\nAdres: Damrak 1, Amsterdam\n" + _ENK, [])
check('filter: twee adressen, onduidelijk welk actueel, geen AI = door', fk[0] == 'ok', fk)
check('grens: woont Antwerpen, werkt Rotterdam = nl/be', _w(f"Lies Peeters\nAntwerpen\n+32 470 00 00 00\nWerkervaring\n{_Y-6} - heden Developer bij Havenbouw, Rotterdam. Ik ontwikkel systemen.", ['+32470000000']) == 'nl')
fk = _filter(f"Lies Peeters\nAntwerpen\n+32 470 00 00 00\nWerkervaring\n{_Y-6} - heden Developer bij Havenbouw, Rotterdam. Ik ontwikkel "
             f"systemen en werk graag met het team aan de ontwikkeling.", ['+32470000000'])
check('filter: grensarbeider BE->NL = door', fk[0] == 'ok', fk)
fk = _filter(f"Bram Dekker\nBreda\n06-00000805\nWerkervaring\n{_Y-6} - heden Developer bij Haventech, Antwerpen. Ik ontwikkel "
             f"systemen en werk graag met het team aan de ontwikkeling.", ['+31600000805'])
check('filter: grensarbeider NL->BE = door', fk[0] == 'ok', fk)
check('plaats: Bergen, Norway + nummer = buitenland', _w("Ola\nBergen, Norway\n+47 400 00 000\n" + _ENK, ['+4740000000']) == 'buitenland')
check('plaats: 1861 AB Bergen = nl', _w("Ola\nDorpsstraat 1, 1861 AB Bergen\n" + _ENK) == 'nl')
check('plaats: alleen Bergen + Noors nummer = tegenstrijdig (AI)', _w("Ola\nBergen\n+47 400 00 000\n" + _ENK, ['+4740000000']) == 'tegenstrijdig')
check('plaats: alleen Bergen = nl (door)', _w("Ola\nBergen\n" + _ENK) == 'nl')
check('plaats: Utrecht, Nederland = nl', _w("Jan\nUtrecht, Nederland\n" + _ENK) == 'nl')
check('postcode: 1234AB aaneen = nl', _w("Jan\nKerkstraat 1, 2011AB Haarlem\n" + _ENK) == 'nl')
check('postcode: 2000 Antwerpen = be', _w("Lies\nMeir 10, 2000 Antwerpen\n" + _ENK) == 'nl')
check('postcode: OCR 2O11 AB Haarlem = nl', _w("Jan\nKerkstraat 1, 2O11 AB Haarlem\n" + _ENK) == 'nl')
check('postcode: NL-postcode + buitenlandse plaats = tegenstrijdig', _w("Jan\n1012 AB, Bangalore, India\n" + _ENK) == 'tegenstrijdig')
check('postcode: 8001 Zurich = buitenland', _w("Jan\nBahnhofstrasse 1, 8001 Zurich, Switzerland\n+41 44 000 00 00\n" + _ENK, ['+41440000000']) == 'buitenland')
check('postcode: alleen postcode = nl-aanwijzing (door)', _w("Jan\n2011 AB\n" + _ENK) == 'nl')
check('postcode: onleesbaar = door', _filter("Jan\n2?1l A8\n" + _ENK, [])[0] == 'ok')

fk = _filter("Rahul\n+91 98450 12345\n" + _ENK, ['+919845012345'], ai={'woont': 'nee', 'woont_buiten': 'nee'})
check('filter: AI-vragen oneens = door (geen onterechte uitsluiting)', fk[0] == 'ok', fk)

# --- Stap 3-7: verblijfsduur (verplichte tabel + extra) ---
def _v(text):
    return c.verblijf_beoordelen(c.normalized_snippet(text))
def _f(text, phones=(), ai=None):
    return _filter(text, list(phones), ai=ai)[0]
_W = "Ik werk met het team aan de ontwikkeling van systemen en help klanten."
t = f"Jan\nUtrecht\nWerkervaring\n{_Y-8} - heden Developer bij Datafabriek, Utrecht. {_W}"
check('verblijf: 8 jaar NL = bevestigd, door', _v(t)[0] == 'bevestigd' and _f(t) == 'ok', _v(t))
t = f"Jan\nUtrecht\nWerkervaring\n{_Y-6} - {_Y-3} Developer bij Havenbouw, Antwerpen. {_W}\n{_Y-3} - heden Developer bij Datafabriek, Utrecht. {_W}"
check('verblijf: 3 jaar NL + 3 jaar BE = bevestigd', _v(t)[0] == 'bevestigd', _v(t))
_m = (_dt.date.today().month - 6 - 1) % 12 + 1; _yy = _Y - 4 - (1 if _dt.date.today().month <= 6 else 0)
t = f"Jan\nUtrecht\nWerkervaring\n{_m:02d}/{_yy} - heden Developer bij Datafabriek, Utrecht. {_W}"
check('verblijf: 4,5 jaar NL = marge (door, niet als 5 jaar bevestigd)', _v(t)[0] == 'marge' and _f(t) == 'ok', _v(t))
t = f"Jan\nWerkervaring\n{_Y-2} - heden Developer bij Datafabriek, Utrecht. {_W}"
check('verblijf: alleen 2 jaar werk = onbekend, door', _v(t)[0] == 'onbekend' and _f(t) == 'ok', _v(t))
t = f"Jan\nWerkervaring\n{_Y-3} - {_Y} Developer bij Datafabriek, Rotterdam. {_W}"
check('verblijf: alleen werk 2023-2026 = niet uitsluiten', _f(t) == 'ok')
t = "Jan\nDeveloper. " + _W
check('verblijf: geen adres of jaren = door', _v(t)[0] == 'onbekend' and _f(t) == 'ok', _v(t))
t = (f"Jan de Vries\nUtrecht\nOpleiding\n{_Y-16} - {_Y-10} Havo, Stedelijk Gymnasium Utrecht\nWerkervaring\n"
     f"{_Y-10} - {_Y-7} Developer bij Monzo, London. {_W}\n{_Y-7} - heden Developer bij Datafabriek, Utrecht. {_W}")
check('verblijf: middelbare school NL, daarna Londen = bevestigd (school + werk)', _v(t)[0] == 'bevestigd' and _f(t) == 'ok', _v(t))
t = (f"Jan de Vries\nOpleiding\n{_Y-16} - {_Y-10} Lyceum, Haarlem\nWerkervaring\n{_Y-10} - heden Developer bij Monzo, London. {_W}")
check('verblijf: NL-school, nu lang Londen, geen aankomstjaar = niet uitsluiten op verblijf', c.verblijf_beoordelen(c.normalized_snippet(t))[0] != 'te_kort')
t = (f"Rajesh\nAmsterdam\nEducation\n{_Y-14} - {_Y-10} B.Tech, Anna University, Chennai\nExperience\n{_Y-8} - present Developer, Adyen, Amsterdam. {_W}")
check('verblijf: buitenlandse universiteit, nu 8 jaar NL = door', _v(t)[0] == 'bevestigd' and _f(t) == 'ok', _v(t))
t = (f"Lies\nMeir 10, 2000 Antwerpen\n+32 470 00 00 01\nExperience\n{_Y-6} - present Developer at Google (remote) from Belgium. {_W}")
check('verblijf: buitenlandse werkgever, woont in BE = door', _f(t, ['+32470000001']) == 'ok')
t = (f"Jan\nWerkervaring\n{_Y-5} - heden Developer bij Datafabriek, Utrecht. {_W}\n{_Y-5} - heden Docent bij ROC, Utrecht. {_W}")
check('verblijf: twee overlappende banen van 5 jaar != 10 jaar', 4.5 <= _v(t)[1] <= 5.5, _v(t))
t = (f"Jan\nWerkervaring\n{_Y-12} - {_Y-10} Developer bij Datafabriek, Utrecht. {_W}\n{_Y-3} - heden Developer bij Codewerk, Utrecht. {_W}")
check('verblijf: gaten in werkhistorie = niet uitsluiten', _f(t) == 'ok')
t = (f"Arjun\nStudieadres: Anna University Campus, Chennai, India\n06-00000901\nExperience\n{_Y-6} - present Developer, Adyen, Amsterdam. {_W}")
check('woon: alleen buitenlands studieadres = geen huidig buitenlands adres', _w(t, ['+31600000901']) != 'buitenland' and _f(t, ['+31600000901']) == 'ok', _w(t, ['+31600000901']))
t = (f"Arjun\nExperience\n{_Y-12} - {_Y-9} Developer, Infosys, Pune, India. {_W}\n{_Y-2} - present Developer, Adyen, Amsterdam. {_W}")
check('woon: alleen oude buitenlandse werkgever = geen bewijs huidig buitenland', _w(t) in ('', 'nl') and _f(t) == 'ok', _w(t))
t = f"Arjun\nMoved to the Netherlands in {_Y-1}.\nExperience\n{_Y-1} - present Developer, Adyen, Amsterdam. {_W}"
check('verblijf: onzekere woonduur, AI onduidelijk, geen bewijs volledige duur -> door', _filter(f"Arjun\nExperience\n{_Y-1} - present Developer, Adyen, Amsterdam. {_W}", [], ai={'verblijf': 'onduidelijk'})[0] == 'ok')
check('verblijf: AI niet beschikbaar = niet uitsluiten op ontbrekende info', _filter(f"Arjun\nExperience\n{_Y-1} - present Developer, Adyen, Amsterdam. {_W}", [], ai=None)[0] == 'ok')
t2 = f"Jan\nWerkervaring\n{_Y} - {_Y-6} Developer bij Datafabriek, Utrecht. {_W}\n{_Y+3} - heden Developer bij Codewerk. {_W}"
check('verblijf: tegenstrijdige/onmogelijke jaartallen = niet gokken, door', _f(t2) == 'ok' and _v(t2)[1] == 0, _v(t2))
check('woon: actueel bevestigd buitenlands adres = woonplaatsregel (niet meegenomen)',
      _filter("Rahul\nCurrent address: MG Road 5, Bangalore, India\n+91 98450 12345\n" + _ENK, ['+919845012345'])[0] == 'weg')
check('verblijf: expliciet sinds vorig jaar in NL, niets eerder = te kort -> niet meegenomen', _v(t)[0] == 'te_kort' and _f(t) == 'weg', _v(t))
check('verblijf: expliciet te kort, maar AI ziet 5 jaar = door', _filter(t, [], ai={'verblijf': 'ja'})[0] == 'ok')
t3 = f"Arjun\nLiving in the Netherlands since {_Y-2}\nOpleiding\n{_Y-15} - {_Y-10} Lyceum, Haarlem\nExperience\n{_Y-2} - present Developer, Adyen, Amsterdam. {_W}"
check('verblijf: "sinds" maar ook eerder NL-school = niet gokken, geen uitsluiting', _v(t3)[0] != 'te_kort' and _f(t3) == 'ok', _v(t3))
t4 = f"Arjun\nIn Nederland sinds {_Y-7}\nWerkervaring\n{_Y-2} - heden Developer bij Codewerk, Utrecht. {_W}"
check('verblijf: expliciet sinds 7 jaar in NL = bevestigd', _v(t4)[0] == 'bevestigd', _v(t4))
# datumnotaties
for _txt, _min, _max in ((f"03/{_Y-6} - 06/{_Y-1} Developer bij X, Utrecht", 5.2, 5.3),
                         (f"Mar {_Y-6} - Jun {_Y-1} Developer at X, Utrecht", 5.2, 5.3),
                         (f"maart {_Y-6} tot juni {_Y-1} Developer bij X, Utrecht", 5.2, 5.3),
                         (f"{_Y-6}–{_Y-1} Developer bij X, Utrecht", 5.0, 5.0),
                         (f"2O{str(_Y-6)[2:]} - {_Y-1} Developer bij X, Utrecht", 5.0, 5.0)):
    _j = _v("Piet\n" + _txt + ". " + _W)[1]
    check(f'datum: {_txt[:28]} = {_min}-{_max} jaar', _min <= _j <= _max, _j)
_j = _v(f"Jan\nOnline cursus {_Y-6} - {_Y-1} Universiteit Utrecht, vanuit India Pune. {_W}")
check('school: online vanuit buitenland telt niet als NL-verblijf', _j[1] == 0, _j)

def _d(name, emails, phones=()):
    import json as _j
    return {'name': name, 'emails': _j.dumps(emails), 'phones': _j.dumps(list(phones)), 'links': '[]', 'sha': name,
            'snippet': '', 'tokens': '[]'}
check('dubbel: Botwin Wytze + Botwin Sijtsma, zelfde mail = mogelijk dubbel (andere achternaam, niet automatisch)', not c.safe_pair(_d('Botwin Wytze', ['botwin.sijtsma@gmail.com'], ['+31638189335']), _d('Botwin Sijtsma', ['botwin.sijtsma@gmail.com'], ['+31638189335']))[0])
check('dubbel: Piet Jansen + Marie Jansen, zelfde gezinsmail = NIET samen', not c.safe_pair(_d('Piet Jansen', ['jansen@ziggo.nl']), _d('Marie Jansen', ['jansen@ziggo.nl']))[0])
check('dubbel: bureaumail jan@bureau met Jan Bakker + Jan de Vries = NIET samen', not c.safe_pair(_d('Jan Bakker', ['jan@itbureau.nl']), _d('Jan de Vries', ['jan@itbureau.nl']))[0])
check('dubbel: recruiter jan.bakker@bureau.nl bij Jan Bakker + Jan Smit = NIET samen', not c.safe_pair(_d('Jan Bakker', ['jan.bakker@itbureau.nl']), _d('Jan Smit', ['jan.bakker@itbureau.nl']))[0])
check('dubbel: "bureau"-achtig woord telt niet als achternaam', not c.safe_pair(_d('Jan Consultancy', ['jan.consultancy@gmail.com']), _d('Jan Smit', ['jan.consultancy@gmail.com']))[0])
check('dubbel: Jan van Dijk + Jan Dijk, jandijk@hotmail = samen', c.safe_pair(_d('Jan van Dijk', ['jandijk@hotmail.com']), _d('Jan van Dijk Pietersen', ['jandijk@hotmail.com']))[0])
check('dubbel: achternaam middenin het adres telt niet', not c.safe_pair(_d('Jan Bos', ['jan.bosmans@gmail.com']), _d('Jan Mans', ['jan.bosmans@gmail.com']))[0])
check('dubbel: recruitermail bij 2 kandidaten = NIET samen', not c.safe_pair(_d('Bobby van den Berg', ['erik.mertz@salves.nl']), _d('Kevin Lok', ['erik.mertz@salves.nl']))[0])
for _ruw, _goed in (('Engels Nederlands Duits', ''), ('Mergeformattom Coenraad', 'Tom Coenraad'), ('Ray Hyde Fe Bd', 'Ray Hyde'), ('Kees van der Be', 'Kees van der Be')):
    _x = c.clean_person_name(_ruw); _x = c.tidy_name(_x) if _x else _x
    check(f'naam "{_ruw}" -> "{_goed}"', _x == _goed, _x)

# LinkedIn: eerst zoeken op naam + specifiek bedrijf; groot bedrijf -> naam + functie
_vragen = []
def _tav_rec(query, *a, **k):
    _vragen.append(query); return []
for _wg, _fn, _verwacht in (('Datafabriek B.V.', 'Backend developer', 'Datafabriek'), ('Albert Heijn', 'Backend developer', 'Backend developer')):
    _vragen.clear()
    _out = Path(tempfile.mkdtemp()); _db = c.db_open(_out)
    c.tavily_search = _tav_rec; c.google_search = lambda q: ([], 'Serper')
    c.online_suggestions([dict(naam='Pieter Zoekman', persoon_id='pz', email='', telefoon='', linkedin='',
                               huidige_functie=_fn, laatst_bekende_werkgever=_wg)], _out, db=_db, delay=0)
    shutil.rmtree(_out, ignore_errors=True)
    check(f'linkedin-zoekvraag bij werkgever "{_wg}" bevat "{_verwacht}"', any(_verwacht in q for q in _vragen), _vragen)

# --- Dubbelen: grote set (zelfde persoon / bureau / gezin / referentie) ---
T = '+31612345678'; T2 = '+31687654321'
C = [  # (naam a, naam b, mails, tel a, tel b, verwacht samen?)
 # zelfde persoon
 ('Botwin Wytze', 'Botwin Sijtsma', ['botwin.sijtsma@gmail.com'], [T], [T], False),   # andere achternaam: twijfelgeval
 ('Jan van Dijk', 'Jan van Dijk Pietersen', ['jandijk@hotmail.com'], [], [], True),
 ('Jan Bakker', 'Jan Bakker', ['jan.bakker@gmail.com'], [], [], True),
 ('Jan Bakker', 'J. Bakker', ['jan.bakker@gmail.com'], [], [], True),
 ('Jan Bakker', 'Jan Bakker Developer', ['jbakker@ziggo.nl'], [], [], True),
 ('Kees de Boer', 'Kees Boer', ['kees.deboer@gmail.com'], [], [], True),
 ('Kees de Boer', 'Kees de Boer', ['kdeboer@hotmail.com'], [T], [], True),
 ('Mohamed El Amrani', 'Mohamed Amrani', ['mohamed.elamrani@gmail.com'], [T], [T], True),
 ('Anna Smit-Jansen', 'Anna Smit', ['anna.smit@outlook.com'], [], [], True),
 ('Pieter Jan de Vries', 'Pieter de Vries', ['pj.devries@gmail.com'], [T], [T], True),
 ('Wouter Van Pelt', 'Wouter Vanpelt', ['wouter.vanpelt@ziggo.nl'], [T], [T], True),
 ('Sanne Visser', 'Sanne Visser', ['sanne_visser85@hotmail.com'], [], [], True),
 # verschillende personen
 ('Jan Bakker', 'Jan de Vries', ['jan@itbureau.nl'], [], [], False),
 ('Jan Bakker', 'Jan Smit', ['jan.bakker@itbureau.nl'], [T], [T], False),
 ('Jan Consultancy', 'Jan Smit', ['jan.consultancy@gmail.com'], [], [], False),
 ('Jan Bos', 'Jan Mans', ['jan.bosmans@gmail.com'], [], [], False),
 ('Piet Jansen', 'Marie Jansen', ['jansen@ziggo.nl'], [T], [T], False),
 ('Piet Jansen', 'Piet Jansen', ['info@jansenit.nl'], [], [], None),  # generiek adres: geen oordeel via mail
 ('Bobby van den Berg', 'Kevin Lok', ['erik.mertz@salves.nl'], [T], [T], False),
 ('Jan Bakker', 'Jan de Vries', ['jan.bakker@gmail.com'], [T], [T2], False),   # referentie in cv van ander
 ('Mark Visser', 'Mark de Groot', ['mark.visser@gmail.com'], [], [], False),
 ('Mark Visser', 'Mark Groot', ['mark@gmail.com'], [], [], False),
 ('Mark Visser', 'Marco Visser', ['m.visser@gmail.com'], [], [], False),
 ('Tom Recruiter', 'Tom Hendriks', ['tom.recruiter@gmail.com'], [], [], False),
 ('Ali Yilmaz', 'Ali Kaya', ['ali.yilmaz@hotmail.com'], [T], [T2], False),
 ('Ahmed Hassan', 'Ahmed Ali', ['ahmed.hassan@gmail.com'], [], [], False),
 ('Jeroen IT', 'Jeroen Smit', ['jeroen.it@gmail.com'], [], [], False),
 ('Lisa de Jong', 'Lisa Jongbloed', ['lisa.dejong@gmail.com'], [], [], False),
 ('Eva Bos', 'Eva Bosch', ['eva.bosch@gmail.com'], [], [], False),
 ('Daan Willems', 'Daan Willemsen', ['daan.willemsen@gmail.com'], [], [], False),
 ('Sara Cohen', 'Sara Levi', ['sara@cohen-levi.nl'], [], [], False),
 ('Rob Bast', 'Roos Flooren', ['projects@competa.com'], [T], [T], False),
 ('Johan Smit', 'Johan Smits', ['johansmit@gmail.com'], [], [], False),
 # extra: telefoon, OCR, tussenvoegsels, bureaus
 ('Johan Smit', 'Johan Smits', ['johansmit@gmail.com'], [T], [T], True),       # typfout, ook zelfde nummer
 ('Botwin Wytze', 'Botwin Sijtsma', ['botwin.sijtsma@gmail.com'], [], [], False), # zonder zelfde nummer: niet zeker
 ('Fatima Bouzid', 'Fatima Bouzid', [], [T], [T], True),
 ('Fatima Bouzid', 'Fatima El Bouzid', [], [T], [T], True),
 ('Fatima Bouzid', 'Samira Bouzid', [], [T], [T], False),                       # zussen, zelfde huisnummer
 ('Henk de Vries', 'Henk Vries', ['h.devries@xs4all.nl'], [], [], True),
 ('Henk de Vries', 'Henk van der Vries', ['h.devries@xs4all.nl'], [], [], False),
 ('Lars Mennen', 'Lars Mennen', ['lars.mennen@online.nl'], [T], [T2], True),
 ('Thomas van den Berg', 'Thomas Berg', ['thomas.vandenberg@gmail.com'], [], [], True),
 ('Thomas van den Berg', 'Thomas van der Berg', ['thomas.vandenberg@gmail.com'], [T], [T], True),
 ('Jan-Willem Menkveld', 'Jan Willem Menkveld', ['jw.menkveld@gmail.com'], [], [], True),
 ('Erik Mertz', 'Kevin Lok', ['erik.mertz@salves.nl'], [T], [T], False),
 ('Kevin Lok', 'Kevin Lok', ['kevin.lok@gmail.com', 'erik.mertz@salves.nl'], [], [], True),
 ('Peter Lorent', 'Ron de Kuijer', ['michael@jrecruit.com'], ['+31346550764'], ['+31346550764'], False),
 ('Saddaf Ahmad', 'Mwa Choudry', ['mwa.choudry@gmail.com'], [T], [T], False),
 ('Tom Coenraad', 'Tom Coenraad', ['tom.coenraad@synergy-ict.nl'], [T], [T], True),
 ('Ray Hyde', 'Ray Hyde', ['ray@rayhyde.nl'], [], [], True),
 ('Marcel Hilboezen', 'Marcel Hilboezen Linkit', ['hilboezen@hotmail.com'], [T], [T], True),
 ('Marcel Hilboezen', 'Wesley Oerlemans', [], [T], [T], False),
 ('Nina Kok', 'Nina Kok', ['nina@kokdesign.nl'], [], [], True),
 ('Bram de Wit', 'Bram Wit', ['b.dewit@hetnet.nl'], [], [], True),
 ('Bram de Wit', 'Bram Witte', ['b.dewit@hetnet.nl'], [], [], False),
 ('Yusuf Demir', 'Yusuf Demirel', ['yusuf.demir@gmail.com'], [], [], False),
 ('Chris Pahlmann', 'Christopher Pahlmann', ['chris.pahlmann@gmail.com'], [T], [T], None),  # wordt 'mogelijk dubbel' (liever missen)
 ('Sophie Jansen', 'Sophie Janssen', [], [T], [T2], False),
 ('', 'Frank Jansen', ['frank.jansen@gmail.com'], [T], [T], True),        # cv zonder naam, zelfde mail+tel
 ('', 'Frank Jansen', ['frank.jansen@gmail.com'], [], [], False),        # alleen mail: niet zeker
 ('', 'Kevin Lok', ['erik.mertz@salves.nl'], [T], [T], False),           # recruitermail past niet bij naam
 ('', 'Frank Jansen', ['info@jansen.nl'], [T], [T], False),              # algemeen adres
]
for a, b, m, ta, tb, verwacht in C:
    if verwacht is None:
        continue
    _r = c.safe_pair(_d(a, m, ta), _d(b, m, tb))
    check(f'dubbel: {a} + {b} ({(m or ["tel"])[0]}) = {"samen" if verwacht else "apart"}', _r[0] == verwacht, _r)

# --- Bureau-cv's: naam in bestandsnaam vs recruiter bovenaan ---
_sn = c.normalized_snippet('Elmer van den Heuvel\nJRecruit\naplitrakmail@broadbean.net\nCurriculum Vitae\nTom den Bulck\nWerkervaring')
check('bestandsnaam: denBulck Tom HQ… -> Tom den Bulck', c.naam_uit_bestandsnaam('/u/JR/D/denBulck Tom HQ00000869 (U).txt', _sn) == 'Tom den Bulck')
check('bestandsnaam: recruiter bovenaan wordt kandidaat uit bestandsnaam', c.naam_botst_met_bestand('Elmer van den Heuvel', '/u/JR/D/denBulck Tom HQ00000869 (U).txt', _sn) == 'Tom den Bulck')
check('bestandsnaam: naam niet in cv = niet gebruiken', c.naam_uit_bestandsnaam('/u/Piet Klaassen.pdf', _sn) == '')
check('bestandsnaam: "CV Java Developer" = geen naam', c.naam_uit_bestandsnaam('/u/CV Java Developer.pdf', c.normalized_snippet('Java Developer')) == '')
check('bestandsnaam: 20250325_CV_Rick_van_der_Kraan_NL (1)', c.naam_uit_bestandsnaam('/u/20250325_CV_Rick_van_der_Kraan_NL (1).pdf', c.normalized_snippet('Rick van der Kraan')) == 'Rick van der Kraan')
check('bestandsnaam: gelijk aan gevonden naam = geen wijziging', c.naam_botst_met_bestand('Rick van der Kraan', '/u/CV_Rick_van_der_Kraan.pdf', c.normalized_snippet('Rick van der Kraan')) == '')
import json as _jj
def _dp(name, path, text, emails, phones):
    return {'name': name, 'path': path, 'snippet': c.normalized_snippet(text), 'emails': _jj.dumps(emails),
            'phones': _jj.dumps(phones), 'links': _jj.dumps(['https://linkedin.com/in/jrecruit']), 'sha': path, 'tokens': '[]'}
_a = _dp('Elmer van den Heuvel', '/u/JR/denBulck Tom HQ1 (U).txt', 'Elmer van den Heuvel Tom den Bulck', ['aplitrakmail@broadbean.net'], ['+31224227904'])
_b = _dp('Elmer van den Heuvel', '/u/JR/Enyedi Robert HQ2 (U).txt', 'Elmer van den Heuvel Robert Enyedi', ['aplitrakmail@broadbean.net'], ['+31224227904'])
check('dubbel: bureau-cv\'s met zelfde recruiter-naam/contact maar andere kandidaat in bestandsnaam = apart', not c.safe_pair(_a, _b)[0])

# --- Dubbelen: harde blokkades, groepscontrole, AI-controle (nagebootst) ---
check('voornaam: Jan / Piet botst', c._voornaam_botst('jan bakker', 'piet bakker'))
check('voornaam: Jan / Jana botst', c._voornaam_botst('jan bakker', 'jana bakker'))
check('voornaam: J / Jan botst niet', not c._voornaam_botst('j bakker', 'jan bakker'))
check('voornaam: Chris / Christopher botst niet', not c._voornaam_botst('chris pahlmann', 'christopher pahlmann'))
check('voornaam: leeg botst niet', not c._voornaam_botst('', 'jan bakker'))
import ai_rechter as _air
def _dd(name, emails=(), phones=(), werk='', opl='', path=None, snippet=''):
    return {'name': name, 'emails': _jj.dumps(list(emails)), 'phones': _jj.dumps(list(phones)), 'links': '[]',
            'sha': (path or name) + str(emails) + str(phones), 'path': path or f'/u/{name}.pdf', 'snippet': snippet or c.normalized_snippet(name),
            'tokens': '[]', 'werkgevers': werk, 'opleidingen': opl}
class _Row(dict):
    def keys(self): return super().keys()
def _ai_test(paren, ai_antwoord):
    docs = [_Row(d) for pair in paren for d in pair]
    reviews = [(2 * k, 2 * k + 1, 'mogelijk dubbel') for k in range(len(paren))]
    samen = []
    _oud = (_air.available, _air.zelfde_persoon, os.environ.get('CV_AI_DUBBEL'))
    _air.available = lambda: True
    _air.zelfde_persoon = lambda a, b: {'oordeel': ai_antwoord}
    os.environ['CV_AI_DUBBEL'] = '1'
    try:
        over = c.ai_dubbel_controle(docs, reviews, lambda a, b: samen.append((a, b)) or True, None)
    finally:
        _air.available, _air.zelfde_persoon = _oud[0], _oud[1]
    return samen, over
_p1 = (_dd('Chris Pahlmann', ['chris.pahlmann@gmail.com'], ['+31612345678'], 'Codewerk'),
       _dd('Christopher Pahlmann', ['chris.pahlmann@gmail.com'], ['+31612345678'], 'Codewerk'))
check('AI-dubbel: 2 bewijzen + AI zelfde = samen', len(_ai_test([_p1], 'zelfde')[0]) == 1)
check('AI-dubbel: AI anders = apart (twijfelgeval)', len(_ai_test([_p1], 'anders')[0]) == 0)
check('AI-dubbel: AI onzeker = apart (twijfelgeval)', len(_ai_test([_p1], 'onzeker')[0]) == 0)
_p2 = (_dd('Jan Bakker', ['jan.bakker@gmail.com'], ['+31612345678']), _dd('Piet Bakker', ['jan.bakker@gmail.com'], ['+31612345678']))
check('AI-dubbel: andere voornaam = nooit samen, ook als AI zelfde zegt', len(_ai_test([_p2], 'zelfde')[0]) == 0)
_p3 = (_dd('Chris Pahlmann', ['chris.pahlmann@gmail.com']), _dd('Christopher Pahlmann', ['chris.pahlmann@gmail.com']))
check('AI-dubbel: maar 1 bewijs (alleen mail) = niet samen', len(_ai_test([_p3], 'zelfde')[0]) == 0)
c.BUREAU_CONTACT.update({'+31224227904', 'aplitrakmail@broadbean.net'})
_p4 = (_dd('Tom Bulck', ['aplitrakmail@broadbean.net'], ['+31224227904']), _dd('Tom Bulcke', ['aplitrakmail@broadbean.net'], ['+31224227904']))
check('AI-dubbel: bureaugegevens tellen niet als bewijs', len(_ai_test([_p4], 'zelfde')[0]) == 0)
c.BUREAU_CONTACT.clear()
os.environ.pop('CV_AI_DUBBEL', None)

# --- Dubbelen: 400 verzonnen paren (zelfde persoon in allerlei versies / naamgenoten, familie, bureaus) ---
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / 'ai_test'))
from dubbel_paren import paren as _paren
class _R(dict):
    def keys(self): return super().keys()
def _doc_t(t, i):
    em = c.emails_in(t); ph = sorted(set(filter(None, (c.norm_phone(x) for x in c.phones_in(t)))))
    return _R(name=c.guess_name(t, f'cv{i}', em), emails=_jj.dumps(em), phones=_jj.dumps(ph), links='[]', sha=str(i),
              path=f'/u/cv{i}.pdf', snippet=c.normalized_snippet(t), tokens=_jj.dumps(c.signature(t)),
              werkgevers=c.werkgevers_from(t), opleidingen=c.opleidingen_from(t))
_fout, _samen, _zelfde = [], 0, 0
for _k, (_waar, _soort, _ta, _tb) in enumerate(_paren(400, seed=11)):
    _a, _b = _doc_t(_ta, 2 * _k), _doc_t(_tb, 2 * _k + 1)
    _auto, _rev, _ = c.safe_pair(_a, _b)
    if not _auto and _rev:
        _st = c._bewijs_stukken(_a, _b)
        _auto = bool(not c.harde_blokkade(_a, _b) and c.zelfde_werk(_a, _b)
                     and any(x.startswith(('zelfde persoonlijk', 'zelfde telefoon', 'zelfde LinkedIn')) for x in _st))
    if _waar == 'anders' and _auto:
        _fout.append(_soort)
    _zelfde += _waar == 'zelfde'; _samen += _waar == 'zelfde' and _auto
check('dubbel-set 400 paren: 0 verschillende personen samengevoegd', not _fout, _fout)
check(f'dubbel-set 400 paren: minstens 90% echte dubbelen samen ({_samen}/{_zelfde})', _samen >= .9 * _zelfde, (_samen, _zelfde))

# --- Meerdere voornamen: naam opnieuw controleren + cv vergelijken ---
_bw1 = 'Curriculum Vitae\nBotwin Wytze Sijtsma\nbotwin.sijtsma@gmail.com\n06 38189335\nWerkervaring\n2019 - heden Developer bij Codewerk. Bouwt systemen.\n2015 - 2019 Tester bij Datafabriek. Testen.'
_bw2 = 'Curriculum Vitae\nBotwin Sijtsma\nbotwin.sijtsma@gmail.com\n06 38189335\nWerkervaring\n2015 - 2019 Tester bij Datafabriek. Testen.'
_x1, _x2 = _doc_t(_bw1, 9001), _doc_t(_bw2, 9002)
_x1['name'] = 'Botwin Wytze'
_r = c.safe_pair(_x1, _x2)
check('meerdere voornamen: Botwin Wytze + Botwin Sijtsma, volledige naam in cv + zelfde loopbaan = samen', _r[0] and 'Botwin Wytze Sijtsma' in _r[2], _r)
_x3 = _doc_t(_bw1.replace('Botwin Wytze Sijtsma', 'Botwin Wytze'), 9003)
_r = c.safe_pair(_x3, _x2)
check('meerdere voornamen: volledige naam staat nergens = twijfelgeval (niet samen)', not _r[0] and _r[1], _r)
_x4 = _doc_t(_bw1.replace('Codewerk', 'Bitbouw').replace('Datafabriek', 'Havenbouw'), 9004); _x4['name'] = 'Botwin Wytze'
_r = c.safe_pair(_x4, _x2)
check('meerdere voornamen: naam past maar loopbaan niet = niet samen', not _r[0], _r)

# --- Contact uit LinkedIn-bio/Info-tekst: verstopte e-mail en losse telefoonschrijfwijzen ---
for tekst, mail in [('jan.bakker [at] gmail [dot] com', 'jan.bakker@gmail.com'), ('jan.bakker(at)gmail.com', 'jan.bakker@gmail.com'),
                    ('jan.bakker @ gmail . com', 'jan.bakker@gmail.com'), ('jan at gmail dot com', 'jan@gmail.com'),
                    ('mail: jan punt bakker apenstaartje gmail punt com', 'jan.bakker@gmail.com'),
                    ('📧 jan.bakker＠gmail.com', 'jan.bakker@gmail.com'), ('j_b (at) eigenbedrijf (dot) nl', 'j_b@eigenbedrijf.nl'),
                    ('Sales at Bol.com', None), ('Team lead at Ziggo.nl', None), ('Consultant at Outlook.com team', None)]:
    got = c.emails_in(c.ontsluier_email(tekst))
    check(f'bio-mail: {tekst!r}', got == ([mail] if mail else []), got)
for tekst in ['Bel 06-12345678', '📞 06 12 34 56 78', '+31 (0)6 1234 5678', 'mobiel 316 12345678', 'WhatsApp: 0612345678']:
    check(f'bio-telefoon: {tekst!r}', c.phones_in(c.fix_nl_mobile(tekst)) == ['+31612345678'], c.phones_in(c.fix_nl_mobile(tekst)))

print()
print('ALLE REGELS OK' if not FOUTEN else f'{len(FOUTEN)} FOUT(EN): ' + ', '.join(FOUTEN))
sys.exit(1 if FOUTEN else 0)
