"""Bright Data-koppeling met nagebootste API (geen echte sleutel, geen echte profielen)."""
import json, os, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ['CV_AI'] = '0'
os.environ['BRIGHTDATA_API_KEY'] = 'nep'
import brightdata as bd  # noqa: E402
fout = []
def check(n, ok, info=''):
    print(('OK   ' if ok else 'FOUT ') + n + ('' if ok else f'  -> {info}'))
    if not ok: fout.append(n)

PROF = {'jan-bakker-1': {'name': 'Jan Bakker', 'url': 'https://www.linkedin.com/in/jan-bakker-1/', 'position': 'Java developer',
                         'current_company': {'name': 'Datafabriek Noord', 'title': 'Java developer'},
                         'experience': None, 'education': [{'title': 'Hogeschool Utrecht', 'start_year': '2010', 'end_year': '2014'}],
                         'about': 'Backend ontwikkelaar.', 'activity': [{'title': 'X liked a post about Google'}],
                         'certifications': [{'title': 'OCP Java 17'}], 'city': 'Utrecht'},
        'piet-smit': {'name': 'Piet Smit', 'url': 'https://www.linkedin.com/in/piet-smit/',
                      'experience': [{'company': 'Codewerk', 'title': 'Tester', 'start_date': '2019', 'end_date': 'Present'}]}}
aanvragen, peilingen = [], {'n': 0}
def nep_verzoek(methode, pad, body=None, timeout=60):
    if pad.startswith('/trigger'):
        aanvragen.append([b['url'] for b in body])
        return json.dumps({'snapshot_id': f's_{len(aanvragen)}'})
    if pad.startswith('/progress/'):
        peilingen['n'] += 1
        return json.dumps({'status': 'running' if peilingen['n'] % 2 else 'ready'})
    if pad.startswith('/snapshot/'):
        sid = int(pad.split('_')[1].split('?')[0])
        uit = []
        for u in aanvragen[sid - 1]:
            s = bd.slug(u)
            uit.append(PROF[s] if s in PROF else {'input': {'url': u}, 'error': 'profile not found'})
        return json.dumps(uit)
bd._verzoek = nep_verzoek
cache = {}
urls = ['https://nl.linkedin.com/in/jan-bakker-1', 'https://www.linkedin.com/in/Jan-Bakker-1/?trk=x',
        'https://www.linkedin.com/in/piet-smit/', 'https://www.linkedin.com/in/bestaat-niet/']
n = bd.ophalen(urls, cache, log=lambda m: None, slaap=lambda s: None)
check('dubbele URL\'s eruit (3 unieke aangevraagd)', sum(len(a) for a in aanvragen) == 3, aanvragen)
check('2 profielen opgehaald', n == 2, n)
check('niet-bestaand profiel als fout in cache', cache.get('bd|bestaat-niet', {}).get('fout'))
check('activiteit/likes van anderen niet bewaard', 'activity' not in cache['bd|jan-bakker-1'])
check('geen openstaande snapshots', cache.get('bd_open') == {})
aanvragen.clear()
bd.ophalen(urls, cache, log=lambda m: None, slaap=lambda s: None)
check('tweede keer: niets opnieuw aangevraagd (cache)', aanvragen == [])
g = bd.gegevens(cache['bd|jan-bakker-1'])
check('werkgever + functie', g['werkgever'] == 'Datafabriek Noord' and g['functie'] == 'Java developer', g)
check('experience null -> werkervaring leeg (dan Serper/SerpApi)', g['werkervaring'] == [] and bd.werkervaring_ontbreekt(cache['bd|jan-bakker-1']))
check('opleiding met jaren', g['opleidingen'] == ['Hogeschool Utrecht · 2010 - 2014'], g['opleidingen'])
check('certificering', g['certificeringen'] == ['OCP Java 17'])
t = bd.tekst(cache['bd|jan-bakker-1'])
check('profieltekst: "functie at werkgever" (telt als bedrijf)', 'Java developer at Datafabriek Noord' in t, t)
check('profieltekst bevat geen liked post', 'liked' not in t and 'Google' not in t, t)
g2 = bd.gegevens(cache['bd|piet-smit'])
check('werkervaring met periode', g2['werkervaring'] == ['Tester · Codewerk · 2019 - Present'], g2['werkervaring'])
# batches: 250 URL's -> 3 snapshots
aanvragen.clear(); cache2 = {}
bd.ophalen([f'https://www.linkedin.com/in/p{i}' for i in range(12)], cache2, log=lambda m: None, slaap=lambda s: None)
check('12 URL\'s -> batches van max 5', [len(a) for a in aanvragen] == [5, 5, 2], [len(a) for a in aanvragen])
# één hangende snapshot houdt de rest niet op
aanvragen.clear(); cache3 = {}; klok = {'t': 0}
_oude = bd._verzoek
def hangt(methode, pad, body=None, timeout=60):
    if pad.startswith('/progress/') and pad.endswith('_1'):
        return json.dumps({'status': 'running'})      # eerste snapshot blijft hangen
    if pad.startswith('/progress/'):
        return json.dumps({'status': 'ready'})
    return _oude(methode, pad, body, timeout)
bd._verzoek = hangt
def tik(s): klok['t'] += s
n3 = bd.ophalen([f'https://www.linkedin.com/in/q{i}' for i in range(10)] + ['https://www.linkedin.com/in/piet-smit/'], cache3,
                log=lambda m: None, slaap=tik, nu=lambda: klok['t'])
check('hangende snapshot: de andere batches wel opgehaald', 'bd|piet-smit' in cache3 and len(cache3.get('bd_open', {})) == 1, list(cache3))
check('hangende snapshot: gestopt na max wachttijd en onthouden voor volgende scan', klok['t'] >= bd.MAX_WACHT and list(cache3['bd_open']) == ['s_1'], klok)
bd._verzoek = _oude
# geen sleutel -> niets
os.environ['BRIGHTDATA_API_KEY'] = ''
check('zonder sleutel: niets doen', bd.ophalen(urls, {}, log=lambda m: None) == 0)

# --- In de online stap: Bright Data eerst, Serper/SerpApi alleen voor het adres zoeken en als fallback ---
import csv, shutil, tempfile
import cv_crawler as c
os.environ['BRIGHTDATA_API_KEY'] = 'nep'; os.environ['SERPER_API_KEY'] = 'nep'; os.environ.pop('TAVILY_API_KEY', None)
google = []
def nep_google(q):
    google.append(q)
    if 'Jan Bakker' in q:
        return ([{'url': 'https://nl.linkedin.com/in/jan-bakker-1', 'title': 'Jan Bakker - Java developer - LinkedIn', 'content': 'Utrecht'}], 'Serper')
    return ([], 'Serper')
c.google_search = nep_google
c.tavily_search = lambda *a, **k: (_ for _ in ()).throw(AssertionError('Tavily mag niet'))
aanvragen.clear(); peilingen['n'] = 0
out = Path(tempfile.mkdtemp()); db = c.db_open(out)
tekst_cv = 'Jan Bakker\nWerkervaring\n2016 - heden Java developer bij Datafabriek Noord, Utrecht\nOpleiding\n2010 - 2014 Hogeschool Utrecht'
row = dict(path=str(out / 'cv.docx'), name='Jan Bakker', status='cv', snippet=c.normalized_snippet(tekst_cv),
           employer='Datafabriek Noord', woonplaats='utrecht', opleidingen='Hogeschool Utrecht', stage='', sha='jb',
           classifier_version=c.CLASSIFIER_VERSION)
db.execute(f"insert into docs({','.join(row)}) values({','.join('?' * len(row))})", list(row.values())); db.commit()
mensen = [dict(naam='Jan Bakker', persoon_id='p1', kandidaat_id='SNK-1', email='', telefoon='', linkedin='',
               huidige_functie='Java developer', laatst_bekende_werkgever='Datafabriek Noord', woonplaats='utrecht'),
          dict(naam='Piet Smit', persoon_id='p2', kandidaat_id='SNK-2', email='p@x.nl', telefoon='+31600000000',
               linkedin='https://www.linkedin.com/in/piet-smit/', huidige_functie='Tester', laatst_bekende_werkgever='Codewerk')]
c.online_suggestions(mensen, out, db=db, delay=0)
rows = list(csv.DictReader(open(out / 'online_suggesties.csv', encoding='utf-8-sig'), delimiter=';'))
best = next((r for r in rows if r['type'] == 'linkedin' and r['keuze'] == 'beste'), {})
check('LinkedIn zeker via Bright Data-profiel (bedrijf + school)', best.get('zekerheid') == 'waarschijnlijk', best)
check('Bright Data haalde beide profielen op (cv-link + gevonden)', sorted(bd.slug(u) for a in aanvragen for u in a) == ['jan-bakker-1', 'piet-smit'], aanvragen)
check('Google maar 1x gebruikt (alleen adres zoeken, geen extra controles)', len([q for q in google if 'linkedin.com/in' in q]) == 1, google)
c.merge_online_suggestions(mensen, out)
r = c.linkedin_gegevens_rijen(mensen, out)
check('tabblad LinkedIn-gegevens: Piet (cv-link) en Jan (online zeker)', sorted(x['naam'] for x in r) == ['Jan Bakker', 'Piet Smit'], [x['naam'] for x in r])
jr = next((x for x in r if x['naam'] == 'Jan Bakker'), {})
check('LinkedIn-gegevens: werkervaring niet openbaar gemeld, opleiding met jaren', jr.get('werkervaring') == '(niet openbaar)' and '2010 - 2014' in jr.get('opleidingen', ''), jr)
shutil.rmtree(out, ignore_errors=True)
print('BRIGHTDATA OK' if not fout else f'{len(fout)} FOUT(EN)'); sys.exit(1 if fout else 0)
