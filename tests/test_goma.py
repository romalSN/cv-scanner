"""Goma controleert een al gevonden LinkedIn-adres. Geen echte netwerkaanroep en geen echte sleutel."""
import csv
import json
import os
import shutil
import sys
import tempfile
import threading
import time
import urllib.error
from pathlib import Path

os.environ['GOMA_RETRY_WACHT'] = '0'
os.environ['LINKEDIN_RETRY_WACHT'] = '0'
os.environ['GOMA_TIMEOUT'] = '2'
os.environ.pop('GOMA_API_KEY', None)
os.environ.pop('APIFY_TOKEN', None)
os.environ.pop('LINKUP_API_KEY', None)

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import cv_crawler as c  # noqa: E402
import goma_linkedin as g  # noqa: E402

FOUT = []


def check(naam, ok, info=''):
    print(('OK   ' if ok else 'FOUT ') + naam + ('' if ok else ' ' + str(info)[:180]))
    if not ok:
        FOUT.append(naam)


def body(naam, banen, opleidingen=None, credits=5):
    delen = naam.split()
    return {
        'data': {
            'first_name': delen[0],
            'last_name': ' '.join(delen[1:]),
            'experience': [
                {'title': functie, 'company_name': bedrijf,
                 'start_date': str(start or ''), 'end_date': str(eind or '')}
                for bedrijf, functie, start, eind in banen
            ],
            'education': opleidingen or [],
        },
        'meta': {'credits_remaining': credits, 'request_id': 'test'},
    }


CV = {
    'werkgevers': ['Datafabriek'],
    'functies': ['Monteur installaties'],
    'opleidingen': ['TU Eindhoven'],
    'perioden': [{'org': 'Datafabriek', 'start': 2010, 'eind': 2018},
                 {'org': 'TU Eindhoven', 'start': 2014, 'eind': 2018}],
}
URL_A = 'https://www.linkedin.com/in/jan-de-vries-data'
URL_B = 'https://www.linkedin.com/in/jan-de-vries-ander'


def antwoord(payload, status=200, headers=None):
    return status, headers or {}, json.dumps(payload)


def klant(http, timeout=2):
    os.environ['GOMA_API_KEY'] = 'test-niet-echt'
    os.environ['GOMA_TIMEOUT'] = str(timeout)
    k = g.Klant(http=http, slaap=lambda _s: None)
    return k


def oordeel(banen, opleidingen=None, naam='Jan de Vries', cv=None):
    profiel = g.normaliseer(body(naam, banen, opleidingen))
    return g.vergelijk('Jan de Vries', cv or CV, profiel)


# --- het cv gaat niet naar Goma ---
gevraagd = []


def vang(api, key, timeout):
    gevraagd.append(api)
    return 404, {}, '{}'


k = klant(vang)
k.ophalen(URL_A, {})
check('alleen het LinkedIn-adres gaat naar Goma',
      gevraagd and 'jan-de-vries-data' in gevraagd[0] and 'Datafabriek' not in gevraagd[0], gevraagd[:1])
check('bedrijfslink wordt niet opgevraagd', k.ophalen('https://www.linkedin.com/company/datafabriek', {}) is None and len(gevraagd) == 1)

# --- vergelijken ---
alleen_naam = oordeel([])
check('dezelfde naam alleen is niet genoeg', alleen_naam['oordeel'] == 'geen gegevens')
check('juiste werkgever wordt bevestigd', oordeel([('Datafabriek', 'Monteur installaties', 2010, 2018)])['oordeel'] == 'steun')
check('andere werkgever wordt niet bevestigd', oordeel([('Bakkerij Zuid', 'Verkoper', 2012, 2019)])['oordeel'] == 'onvoldoende')
check('dezelfde school bevestigt ook zonder dezelfde werkgever',
      oordeel([('Bakkerij Zuid', 'Verkoper', 2016, 2020)],
              [{'school': 'Technische Universiteit Eindhoven', 'degree': 'Bachelor', 'period': '2014 - 2018'}])['oordeel'] == 'steun')
check('extra baan na het cv is geen tegenbewijs',
      oordeel([('Datafabriek', 'Monteur installaties', 2010, 2018),
               ('Nieuwbedrijf', 'Projectleider', 2022, 2026)])['oordeel'] == 'steun')
check('extra stage is geen tegenbewijs',
      oordeel([('Datafabriek', 'Monteur installaties', 2010, 2018),
               ('Stagehuis', 'Stagiair', 2009, 2010)])['oordeel'] == 'steun')
check('bachelor plus master blijft dezelfde persoon',
      oordeel([('Datafabriek', 'Monteur installaties', 2016, 2020)],
              [{'school': 'Technische Universiteit Eindhoven', 'degree': 'Bachelor', 'period': '2010 - 2014'},
               {'school': 'TU Delft', 'degree': 'Master', 'period': '2014 - 2016'}])['oordeel'] == 'steun')
check('oud cv en nieuwere baan blijven kloppen',
      oordeel([('Datafabriek B.V.', 'Monteur installaties', 2011, 2019),
               ('Ander Huis', 'Adviseur', 2020, '')])['oordeel'] == 'steun')
dicht = oordeel([('Datafabriek', 'Monteur installaties', 2011, 2019)])
check('klein jaartalverschil telt mee en keurt niet af', dicht['oordeel'] == 'steun' and any('jaren' in b for b in dicht['bewijs']))
check('andere naam wordt afgewezen', oordeel([('Datafabriek', 'Monteur', 2010, 2018)], naam='Piet de Vries')['oordeel'] == 'afwijzen')
check('groot bedrijf alleen is niet genoeg',
      g.vergelijk('Jan de Vries', {'werkgevers': ['ASML'], 'functies': [], 'opleidingen': []},
                  g.normaliseer(body('Jan de Vries', [('ASML', 'Iets', 2010, 2018)])))['oordeel'] == 'onvoldoende')
check('groot bedrijf plus dezelfde functie is wel genoeg',
      g.vergelijk('Jan de Vries', {'werkgevers': ['ASML'], 'functies': ['Software Engineer'], 'opleidingen': []},
                  g.normaliseer(body('Jan de Vries', [('ASML', 'Software Engineer', 2010, 2018)])))['oordeel'] == 'steun')

# --- kiezen tussen profielen ---
os.environ['GOMA_MAX_PER_KANDIDAAT'] = '3'


def kies(profielen):
    calls = []

    def http(api, key, timeout):
        calls.append(api)
        for deel, payload in profielen:
            if deel in api:
                return antwoord(payload)
        return 404, {}, '{}'

    k = klant(http)
    candidates = {}
    for deel, _payload in profielen:
        url = 'https://www.linkedin.com/in/' + deel
        candidates[('linkedin', url)] = {'hits': set(), 'match': set(), 'url': url, 'title': 'Jan de Vries', 'query': 'x'}
    g.beoordeel(candidates, 'Jan de Vries', CV, {}, klant=k)
    return candidates, calls


goed = body('Jan de Vries', [('Datafabriek', 'Monteur installaties', 2010, 2018)])
fout = body('Jan de Vries', [('Bakkerij Zuid', 'Verkoper', 2014, 2020)])
kandidaten, aanvragen = kies([('jan-de-vries-data', goed), ('jan-de-vries-ander', fout)])
winnaar = [url for (typ, url), info in kandidaten.items() if typ == 'linkedin' and info.get('goma_bevestigd')]
check('Goma kiest het profiel met dezelfde werkgever', winnaar == [URL_A], winnaar)
check('het andere profiel wordt niet bevestigd',
      not kandidaten[('linkedin', URL_B)].get('goma_bevestigd') and kandidaten[('linkedin', URL_B)].get('goma_onvoldoende'))
gelijk, _ = kies([('jan-de-vries-data', goed), ('jan-de-vries-ander', goed)])
check('twee even aannemelijke profielen krijgen geen winnaar',
      all(info.get('goma_geen_winnaar') for (_t, _u), info in gelijk.items())
      and not any(info.get('goma_bevestigd') for (_t, _u), info in gelijk.items()))

# --- cache, Apify, credits, foutcodes ---
cache = {}
n = {'n': 0}


def eenmaal(api, key, timeout):
    n['n'] += 1
    return antwoord(goed)


k = klant(eenmaal)
eerste = k.ophalen(URL_A, cache)
tweede = k.ophalen(URL_A, cache)
check('cache voorkomt een tweede betaling', eerste and tweede and n['n'] == 1, n['n'])

n = {'n': 0}
apify_cache = {'ap|jan-de-vries-data': {
    'firstName': 'Jan', 'lastName': 'de Vries',
    'experience': [{'position': 'Monteur installaties', 'companyName': 'Datafabriek', 'startDate': {'year': 2010}}],
    'education': [{'schoolName': 'TU Eindhoven', 'period': '2006 - 2010'}],
}}
uit_apify = klant(eenmaal).ophalen(URL_A, apify_cache)
check('opgeslagen Apify-profiel wordt hergebruikt zonder Goma', uit_apify and uit_apify['bron'] == 'apify' and n['n'] == 0)

n = {'n': 0}


def tegoed_op(api, key, timeout):
    n['n'] += 1
    return 402, {}, json.dumps({'error': {'code': 'insufficient_credits', 'message': 'op'}})


k = klant(tegoed_op)
check('Goma zonder tegoed geeft niets terug', k.ophalen(URL_A, {}) is None and k.op)
check('een volgende kandidaat vraagt Goma niet opnieuw', k.ophalen(URL_B, {}) is None and n['n'] == 1)
check('een opgemaakt tegoed wist de sleutel niet', os.environ.get('GOMA_API_KEY') == 'test-niet-echt')

n = {'n': 0}


def niet_gevonden(api, key, timeout):
    n['n'] += 1
    return 404, {}, json.dumps({'error': {'code': 'profile_not_found'}})


k = klant(niet_gevonden)
cache = {}
check('HTTP 404 wordt onthouden', k.ophalen(URL_A, cache) is None and k.ophalen(URL_A, cache) is None and n['n'] == 1)

slaap = []
n = {'n': 0}


def te_vaak(api, key, timeout):
    n['n'] += 1
    return 429, {'Retry-After': '30'}, json.dumps({'error': {'code': 'rate_limited'}})


k = klant(te_vaak)
k.slaap = slaap.append
check('HTTP 429 met lange wacht wacht niet en schakelt over', k.ophalen(URL_A, {}) is None and not any(x >= 5 for x in slaap))
check('de beperkte dienst wordt daarna overgeslagen', k.ophalen(URL_B, {}) is None and n['n'] == 1)

n = {'n': 0}


def te_vaak_kort(api, key, timeout):
    n['n'] += 1
    if n['n'] == 1:
        return 429, {'Retry-After': '1'}, '{}'
    return antwoord(goed)


k = klant(te_vaak_kort)
k.slaap = slaap.append
kort = k.ophalen(URL_A, {})
check('HTTP 429 met korte Retry-After probeert één keer opnieuw', kort and n['n'] == 2 and 1 in slaap)

n = {'n': 0}


def storing(api, key, timeout):
    n['n'] += 1
    if n['n'] == 1:
        return 503, {}, json.dumps({'error': {'code': 'service_unavailable'}})
    return antwoord(goed)


k = klant(storing)
check('HTTP 503 krijgt één nieuwe poging', k.ophalen(URL_A, {}) and n['n'] == 2 and not k.op)

n = {'n': 0}


def hangt(api, key, timeout):
    n['n'] += 1
    time.sleep(1)
    return antwoord(goed)


k = klant(hangt, timeout=0.2)
start = time.perf_counter()
check('een trage Goma-aanvraag stopt bij de maximale duur', k.ophalen(URL_A, {}) is None and time.perf_counter() - start < 0.8)

n = {'n': 0}
k = klant(hangt, timeout=0.15)
k.ophalen(URL_A, {})
k.ophalen(URL_B, {})
derde = k.ophalen('https://www.linkedin.com/in/jan-de-vries-drie', {})
check('twee timeouts zetten Goma vijf minuten op pauze', derde is None and n['n'] == 2 and k.pauze_tot > time.time())

n = {'n': 0}


def ongeldig(api, key, timeout):
    n['n'] += 1
    return 401, {}, json.dumps({'error': {'code': 'unauthenticated'}})


k = klant(ongeldig)
k.ophalen(URL_A, {})
k.ophalen(URL_B, {})
check('ongeldige sleutel stopt Goma en laat de sleutel staan', k.ongeldig and n['n'] == 1 and os.environ.get('GOMA_API_KEY') == 'test-niet-echt')

# --- geen sleutel, geen internet, grote groep ---
geroepen = {'n': 0}


def mag_niet(*a):
    geroepen['n'] += 1
    raise AssertionError('Goma werd aangeroepen')


leeg = {('linkedin', URL_A): {'hits': set(), 'match': set(), 'url': URL_A, 'title': '', 'query': ''},
        ('linkedin', URL_B): {'hits': set(), 'match': set(), 'url': URL_B, 'title': '', 'query': ''}}
zonder = klant(mag_niet)
os.environ.pop('GOMA_API_KEY', None)
g.beoordeel(leeg, 'Jan de Vries', CV, {}, klant=zonder)
check('zonder Goma-sleutel blijft de gewone vergelijking staan', geroepen['n'] == 0 and ('linkedin', URL_A) in leeg)


def geen_net(api, key, timeout):
    geroepen['n'] += 1
    raise urllib.error.URLError('geen internet')


os.environ['GOMA_API_KEY'] = 'test-niet-echt'
k = klant(geen_net)
check('Goma zonder internet stopt die aanvraag en niet de scanner', k.ophalen(URL_A, {}) is None and not k.op)

start = time.perf_counter()
for _ in range(200):
    g.vergelijk('Jan de Vries', CV, g.normaliseer(goed))
check('200 vergelijkingen blijven snel', time.perf_counter() - start < 2)

cache = {}
profiel = g.normaliseer(goed)
for i in range(200):
    cache['goma|persoon-%d' % i] = {'profiel': profiel, 'op': time.time(), 'bron': 'goma', 'fout': ''}
n = {'n': 0}
k = klant(lambda *a: n.__setitem__('n', n['n'] + 1))
start = time.perf_counter()
for i in range(200):
    k.ophalen('https://www.linkedin.com/in/persoon-%d' % i, cache)
check('200 gecachte profielen kosten geen nieuwe aanvraag en geen wachttijd',
      n['n'] == 0 and time.perf_counter() - start < 2)

bezig = {'n': 0, 'max': 0}
slot = threading.Lock()


def tegelijk(api, key, timeout):
    with slot:
        bezig['n'] += 1
        bezig['max'] = max(bezig['max'], bezig['n'])
    time.sleep(0.15)
    with slot:
        bezig['n'] -= 1
    return antwoord(goed)


k = klant(tegelijk, timeout=2)
threads = [threading.Thread(target=k.ophalen, args=('https://www.linkedin.com/in/persoon-%d' % i, {})) for i in range(6)]
for t in threads:
    t.start()
for t in threads:
    t.join(3)
check('hooguit vijf Goma-aanvragen tegelijk', bezig['max'] <= 5 and bezig['max'] >= 2, bezig['max'])

# --- de scanner zelf: cv-link, juiste persoon, gelijkspel, Excel ---
os.environ['SERPER_API_KEY'] = 'test'
os.environ['GOMA_API_KEY'] = 'test-niet-echt'
os.environ['GOMA_TIMEOUT'] = '2'
_serper, _serpapi, _tavily, _google = c.serper_search, c.serpapi_search, c.tavily_search, c.google_search
try:
    import ai_rechter
    _ai = ai_rechter.available
    ai_rechter.available = lambda: False
except Exception:
    _ai = None

gemaakt = {'n': 0}
k = g.reset()
k.timeout = 2
k.slaap = lambda _s: None
k.http = lambda *a: gemaakt.__setitem__('n', gemaakt['n'] + 1)
c.serper_search = lambda *a, **k: (_ for _ in ()).throw(AssertionError('gezocht'))
c.serpapi_search = c.serper_search
c.tavily_search = c.serper_search
c.google_search = lambda q: ([], 'Serper')
_out = Path(tempfile.mkdtemp())
_db = c.db_open(_out)
try:
    c.online_suggestions([dict(naam='Jan de Vries', persoon_id='p-cv', email='jan@voorbeeld.nl',
                               telefoon='+31612345678', linkedin='https://www.linkedin.com/in/jan-de-vries',
                               huidige_functie='Monteur installaties', laatst_bekende_werkgever='Datafabriek',
                               woonplaats='')], _out, db=_db, delay=0)
    check('LinkedIn uit het cv slaat Goma over', gemaakt['n'] == 0, gemaakt['n'])
finally:
    shutil.rmtree(_out, ignore_errors=True)

rijen = {}


def zoek_twee(query, key):
    return [
        {'url': URL_A, 'title': 'Jan de Vries | LinkedIn', 'content': ''},
        {'url': URL_B, 'title': 'Jan de Vries | LinkedIn', 'content': ''},
    ]


def goma_http(api, key, timeout):
    if 'jan-de-vries-data' in api:
        return antwoord(goed)
    if 'jan-de-vries-ander' in api:
        return antwoord(fout)
    return 404, {}, '{}'


c.serper_search = zoek_twee
c.serpapi_search = lambda *a, **k: []
c.tavily_search = lambda *a, **k: []
k = g.reset()
k.timeout = 2
k.slaap = lambda _s: None
k.http = goma_http
_out = Path(tempfile.mkdtemp())
_db = c.db_open(_out)
persoon = dict(naam='Jan de Vries', persoon_id='p-juist', email='jan@voorbeeld.nl', telefoon='+31612345678',
               linkedin='', huidige_functie='Monteur installaties', laatst_bekende_werkgever='Datafabriek',
               woonplaats='Eindhoven', kandidaat_id='SNK', cv_id='SNCV', cv_bestand='', cv_pad='', cv_archief='')
try:
    klaar = c.online_suggestions([dict(persoon)], _out, db=_db, delay=0)
    with (_out / 'online_suggesties.csv').open(encoding='utf-8-sig', newline='') as f:
        rijen = list(csv.DictReader(f, delimiter=';'))
    c.merge_online_suggestions([persoon], _out)
    excel, _cols = c.personen_overzicht([persoon])
    gekozen = [r for r in rijen if r.get('type') == 'linkedin' and r.get('zekerheid') == 'waarschijnlijk']
    check('de scanner blijft heel als Goma meedoet', klaar is True)
    check('alleen het passende profiel wordt zeker',
          len(gekozen) == 1 and 'jan-de-vries-data' in gekozen[0].get('gevonden_waarde', ''), gekozen[:1])
    check('het verkeerde profiel komt niet in de Excel',
          excel and 'jan-de-vries-ander' not in str(excel[0].get('linkedin')) and 'jan-de-vries-data' in str(excel[0].get('linkedin')))
    check('de Excel noemt de bron van de bevestiging', 'goma' in (gekozen[0].get('overeenkomst') or '').lower())
finally:
    shutil.rmtree(_out, ignore_errors=True)

k = g.reset()
k.timeout = 2
k.slaap = lambda _s: None
k.http = lambda *a, **kw: antwoord(goed)
_out = Path(tempfile.mkdtemp())
_db = c.db_open(_out)
persoon = dict(naam='Jan de Vries', persoon_id='p-gelijk', email='jan@voorbeeld.nl', telefoon='+31612345678',
               linkedin='', huidige_functie='Monteur installaties', laatst_bekende_werkgever='Datafabriek',
               woonplaats='', kandidaat_id='SNK', cv_id='SNCV', cv_bestand='', cv_pad='', cv_archief='')
try:
    c.online_suggestions([dict(persoon)], _out, db=_db, delay=0)
    with (_out / 'online_suggesties.csv').open(encoding='utf-8-sig', newline='') as f:
        rijen = list(csv.DictReader(f, delimiter=';'))
    c.merge_online_suggestions([persoon], _out)
    excel, _cols = c.personen_overzicht([persoon])
    check('bij gelijkspel wordt geen profiel ingevuld',
          not any(r.get('zekerheid') == 'waarschijnlijk' for r in rijen if r.get('type') == 'linkedin')
          and not persoon.get('online_linkedin'))
    check('Excel blijft leeg voor een onbevestigde LinkedIn',
          excel and 'jan-de-vries' not in str(excel[0].get('linkedin')))
finally:
    shutil.rmtree(_out, ignore_errors=True)
    c.serper_search, c.serpapi_search, c.tavily_search, c.google_search = _serper, _serpapi, _tavily, _google
    if _ai is not None:
        ai_rechter.available = _ai

check('standaard wacht Goma hooguit 15 seconden', g.timeout_seconden() == 15 or os.environ.get('GOMA_TIMEOUT') == '2')
os.environ.pop('GOMA_TIMEOUT', None)
check('zonder instelling is de Goma-wacht 15 seconden', g.timeout_seconden() == 15)

# --- keten: afgewezen profiel, volgende bron, Linkup, budget ---
os.environ['GOMA_API_KEY'] = 'test-niet-echt'
os.environ['GOMA_MAX_PER_KANDIDAAT'] = '3'
os.environ['SERPER_API_KEY'] = 'test'
os.environ['SERPAPI_API_KEY'] = 'test'
os.environ['TAVILY_API_KEY'] = 'test'
os.environ['LINKEDIN_RETRY_WACHT'] = '0'
try:
    import ai_rechter
    _ai2 = ai_rechter.available
    ai_rechter.available = lambda: False
except Exception:
    _ai2 = None
_serper2, _serpapi2, _tavily2, _google2, _linkup2 = (
    c.serper_search, c.serpapi_search, c.tavily_search, c.google_search, c.linkup_search)

voor_cap = g.aanvragen_deze_run
os.environ['GOMA_MAX_PER_KANDIDAAT'] = '3'
cap_calls = {'n': 0}


def cap_http(api, key, timeout):
    cap_calls['n'] += 1
    return antwoord(goed)


k = klant(cap_http)
veel = {}
for i in range(4):
    url = 'https://www.linkedin.com/in/jan-de-vries-%d' % i
    veel[('linkedin', url)] = {'hits': set(), 'match': set(), 'url': url, 'title': 'Jan de Vries', 'query': 'x'}
teller = {'n': 0}
g.beoordeel(veel, 'Jan de Vries', CV, {}, klant=k, teller=teller)
g.beoordeel(veel, 'Jan de Vries', CV, {}, klant=k, teller=teller)
check('hooguit drie nieuwe Goma-aanvragen per kandidaat', cap_calls['n'] == 3 and teller['n'] == 3, cap_calls['n'])

bronnen_gebruikt = []


def serper_verkeerd(query, key):
    bronnen_gebruikt.append('Serper')
    return [{'url': URL_B, 'title': 'Jan de Vries | LinkedIn', 'content': ''}]


def serp_goed(query, key):
    bronnen_gebruikt.append('SerpApi')
    return [{'url': URL_A, 'title': 'Jan de Vries | LinkedIn', 'content': ''}]


def tavily_mag_niet(*a, **k):
    bronnen_gebruikt.append('Tavily')
    raise AssertionError('Tavily werd aangeroepen nadat het profiel al klopte')


def goma_volgende(api, key, timeout):
    if 'jan-de-vries-data' in api:
        return antwoord(goed)
    if 'jan-de-vries-ander' in api:
        return antwoord(fout)
    return 404, {}, '{}'


c.serper_search = serper_verkeerd
c.serpapi_search = serp_goed
c.tavily_search = tavily_mag_niet
c.linkup_search = tavily_mag_niet
c.google_search = lambda q: ([], 'Serper')
k = g.reset()
k.timeout = 2
k.slaap = lambda _s: None
k.http = goma_volgende
_out = Path(tempfile.mkdtemp())
_db = c.db_open(_out)
persoon = dict(naam='Jan de Vries', persoon_id='p-volgende', email='jan@voorbeeld.nl', telefoon='+31612345678',
               linkedin='', huidige_functie='Monteur installaties', laatst_bekende_werkgever='Datafabriek',
               woonplaats='', kandidaat_id='SNK', cv_id='SNCV', cv_bestand='', cv_pad='', cv_archief='')
try:
    c.online_suggestions([dict(persoon)], _out, db=_db, delay=0)
    with (_out / 'online_suggesties.csv').open(encoding='utf-8-sig', newline='') as f:
        rijen = list(csv.DictReader(f, delimiter=';'))
    c.merge_online_suggestions([persoon], _out)
    excel, _cols = c.personen_overzicht([persoon])
    gekozen = [r for r in rijen if r.get('type') == 'linkedin' and r.get('zekerheid') == 'waarschijnlijk']
    check('afgewezen profiel: de volgende zoekbron wordt in dezelfde scan geprobeerd',
          'Serper' in bronnen_gebruikt and 'SerpApi' in bronnen_gebruikt and 'Tavily' not in bronnen_gebruikt,
          bronnen_gebruikt)
    check('de volgende bron levert het passende profiel',
          len(gekozen) == 1 and 'jan-de-vries-data' in gekozen[0].get('gevonden_waarde', ''), gekozen[:1])
    check('het afgewezen profiel komt niet in de Excel',
          excel and 'jan-de-vries-ander' not in str(excel[0].get('linkedin')))
finally:
    shutil.rmtree(_out, ignore_errors=True)

linkup_urls = []


def leeg(*a, **k):
    return []


def linkup_profiel(query, key):
    linkup_urls.append(query)
    return [{'url': URL_A, 'title': 'Jan de Vries | LinkedIn', 'content': ''}]


c.serper_search = leeg
c.serpapi_search = leeg
c.tavily_search = leeg
c.linkup_search = linkup_profiel
os.environ['LINKUP_API_KEY'] = 'test-linkup'
goma_urls = []


def goma_linkup(api, key, timeout):
    goma_urls.append(api)
    return antwoord(goed)


k = g.reset()
k.timeout = 2
k.slaap = lambda _s: None
k.http = goma_linkup
_out = Path(tempfile.mkdtemp())
_db = c.db_open(_out)
persoon = dict(naam='Jan de Vries', persoon_id='p-linkup', email='jan@voorbeeld.nl', telefoon='+31612345678',
               linkedin='', huidige_functie='Monteur installaties', laatst_bekende_werkgever='Datafabriek',
               woonplaats='', kandidaat_id='SNK', cv_id='SNCV', cv_bestand='', cv_pad='', cv_archief='')
try:
    c.online_suggestions([dict(persoon)], _out, db=_db, delay=0)
    with (_out / 'online_suggesties.csv').open(encoding='utf-8-sig', newline='') as f:
        rijen = list(csv.DictReader(f, delimiter=';'))
    gekozen = [r for r in rijen if r.get('type') == 'linkedin' and r.get('zekerheid') == 'waarschijnlijk']
    check('Linkup-resultaat gaat naar Goma', bool(linkup_urls) and goma_urls and 'jan-de-vries-data' in goma_urls[0],
          goma_urls[:1])
    check('Goma bevestigt het Linkup-profiel', len(gekozen) == 1 and 'jan-de-vries-data' in gekozen[0].get('gevonden_waarde', ''))
    check('de bevestiging noemt geen Google-fallback',
          'Google X-ray' not in (gekozen[0].get('overeenkomst') or ''))
finally:
    shutil.rmtree(_out, ignore_errors=True)
    os.environ.pop('LINKUP_API_KEY', None)

lokaal = {'goma': 0, 'serp': 0}


def serper_lokaal(query, key):
    return [{'url': URL_A, 'title': 'Jan de Vries - Monteur at Datafabriek | LinkedIn',
             'content': 'Monteur installaties at Datafabriek'}]


def serp_telt(query, key):
    lokaal['serp'] += 1
    return []


def goma_telt(api, key, timeout):
    lokaal['goma'] += 1
    return antwoord(goed)


c.serper_search = serper_lokaal
c.serpapi_search = serp_telt
c.tavily_search = leeg
c.linkup_search = leeg
k = g.reset()
k.timeout = 2
k.slaap = lambda _s: None
k.http = goma_telt
_out = Path(tempfile.mkdtemp())
_db = c.db_open(_out)
persoon = dict(naam='Jan de Vries', persoon_id='p-lokaal', email='jan@voorbeeld.nl', telefoon='+31612345678',
               linkedin='', huidige_functie='Monteur installaties', laatst_bekende_werkgever='Datafabriek',
               woonplaats='', kandidaat_id='SNK', cv_id='SNCV', cv_bestand='', cv_pad='', cv_archief='')
try:
    c.online_suggestions([dict(persoon)], _out, db=_db, delay=0)
    check('duidelijk zoekresultaat stopt de keten en slaat Goma over', lokaal['goma'] == 0 and lokaal['serp'] == 0, lokaal)
finally:
    shutil.rmtree(_out, ignore_errors=True)


def alles_op(*_a, **_k):
    raise urllib.error.HTTPError('https://voorbeeld.test/zoek', 402, 'op', None, None)


c.serper_search = alles_op
c.serpapi_search = alles_op
c.tavily_search = alles_op
c.linkup_search = alles_op
os.environ['LINKUP_API_KEY'] = 'test-linkup'
goma_leeg = {'n': 0}


def goma_niet(api, key, timeout):
    goma_leeg['n'] += 1
    return 402, {}, '{}'


k = g.reset()
k.timeout = 2
k.slaap = lambda _s: None
k.http = goma_niet
_out = Path(tempfile.mkdtemp())
_db = c.db_open(_out)
persoon = dict(naam='Jan de Vries', persoon_id='p-op', email='jan@voorbeeld.nl', telefoon='+31612345678',
               linkedin='', huidige_functie='Monteur installaties', laatst_bekende_werkgever='Datafabriek',
               woonplaats='', kandidaat_id='SNK', cv_id='SNCV', cv_bestand='', cv_pad='', cv_archief='')
try:
    klaar = c.online_suggestions([dict(persoon)], _out, db=_db, delay=0)
    with (_out / 'online_suggesties.csv').open(encoding='utf-8-sig', newline='') as f:
        rijen = list(csv.DictReader(f, delimiter=';'))
    c.merge_online_suggestions([persoon], _out)
    excel, _cols = c.personen_overzicht([persoon])
    import linkedin_zoek
    open_ids = linkedin_zoek.lees_open(_out / linkedin_zoek.OPEN_BESTAND)
    check('alle zoekcredits op: de scanner blijft werken', klaar is True)
    check('alle zoekcredits op: geen LinkedIn ingevuld en geen Goma-aanvraag',
          goma_leeg['n'] == 0 and excel and 'linkedin.com/in/' not in str(excel[0].get('linkedin')))
    zekerheid = str(excel[0].get('gezocht_zekerheid') or '') + str(excel[0].get('online_zekerheid') or '')
    check('alle zoekcredits op: kandidaat blijft open, zonder Google-fallbackmelding',
          'p-op' in open_ids and 'Google X-ray' not in zekerheid, open_ids)
finally:
    shutil.rmtree(_out, ignore_errors=True)
    os.environ.pop('LINKUP_API_KEY', None)
    c.serper_search, c.serpapi_search, c.tavily_search, c.google_search, c.linkup_search = (
        _serper2, _serpapi2, _tavily2, _google2, _linkup2)
    if _ai2 is not None:
        ai_rechter.available = _ai2

check('Goma-aanvragen in deze test zijn nagebootst', g.aanvragen_deze_run >= voor_cap)
print('Goma-aanvragen in deze test (nep, geen echt tegoed):', g.aanvragen_deze_run)

print()
print('GOMA OK' if not FOUT else f'{len(FOUT)} FOUT(EN): ' + ', '.join(FOUT))
sys.exit(1 if FOUT else 0)
