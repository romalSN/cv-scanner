"""Apify – 'LinkedIn Profile Scraper (No Cookies)' van HarvestAPI: openbare LinkedIn-profielen ophalen op URL.

- Token: environment variable APIFY_TOKEN (de scanner zet die vanuit apify_sleutel.txt).
- Actor: APIFY_ACTOR (standaard harvestapi~linkedin-profile-scraper), modus zonder e-mailzoeken ($4 per 1.000).
- Batches van 100 profielen per run (gratis account: max 50 runs in totaal), alle runs tegelijk gevolgd.
- Dubbele URL's eruit; resultaten in de cache ('ap|<slug>'), dus nooit twee keer betalen.
- 'moreProfiles' (andere mensen), aanbevelingen e.d. worden NIET bewaard of gebruikt.
"""
import json
import os
import time
import urllib.error
import urllib.request

import re


def slug(url):
    m = re.search(r'linkedin\.com/in/([^/?#\s]+)', str(url or ''), re.I)
    return m.group(1).lower().rstrip('/') if m else ''


def nette_url(url):
    s = slug(url)
    return f'https://www.linkedin.com/in/{s}/' if s else ''

PREFIX = 'ap|'
API = 'https://api.apify.com/v2'
ACTOR_STANDAARD = 'harvestapi~linkedin-profile-scraper'
BATCH = int(os.environ.get('APIFY_BATCH', '10'))    # gratis account: max 10 profielen per run en 50 runs in totaal
MAX_WACHT = 10 * 60
PEIL = 10
NEGEER_VELDEN = {'moreProfiles', 'photo', 'receivedRecommendations', 'givenRecommendations', 'featured',
                 'followerCount', 'connectionsCount', 'registeredAt', 'premium', 'influencer', 'verified'}
NAAM = 'Apify'


def sleutel():
    return os.environ.get('APIFY_TOKEN', '').strip()


def actor():
    return os.environ.get('APIFY_ACTOR', '').strip() or ACTOR_STANDAARD


def _verzoek(methode, pad, body=None, timeout=60):
    req = urllib.request.Request(API + pad, method=methode,
                                 data=json.dumps(body).encode('utf-8') if body is not None else None,
                                 headers={'Authorization': f'Bearer {sleutel()}', 'Content-Type': 'application/json',
                                          'User-Agent': 'CV-Scanner'})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read().decode('utf-8', 'replace')


def _met_wachten(fn, *args, **kw):
    wacht = 10
    for _ in range(5):
        try:
            return fn(*args, **kw)
        except urllib.error.HTTPError as exc:
            if exc.code != 429:
                raise
            time.sleep(wacht)
            wacht = min(wacht * 2, 120)
    raise RuntimeError('Apify: te veel verzoeken (429), later opnieuw')


def start_run(urls):
    raw = _met_wachten(_verzoek, 'POST', f'/acts/{actor()}/runs', {'urls': urls})
    d = json.loads(raw).get('data') or {}
    if not d.get('id'):
        raise RuntimeError('Apify gaf geen run-id: ' + raw[:150])
    return d['id']


def run_info(rid):
    return json.loads(_met_wachten(_verzoek, 'GET', f'/actor-runs/{rid}')).get('data') or {}


def items(dataset_id):
    return json.loads(_met_wachten(_verzoek, 'GET', f'/datasets/{dataset_id}/items?clean=true&format=json', timeout=180))


def ophalen(urls, cache, log=print, slaap=time.sleep, nu=time.time):
    """Profielen ophalen voor URL's die nog niet in de cache staan. Lopende runs worden onthouden ('ap_open').
    Geeft het aantal nieuw opgehaalde profielen (-1 bij een fout bij het starten)."""
    if not sleutel():
        return 0
    nieuw = []
    for u in urls:
        s = slug(u)
        if s and PREFIX + s not in cache and nette_url(u) not in nieuw:
            nieuw.append(nette_url(u))
    open_ = cache.setdefault('ap_open', {})
    for i in range(0, len(nieuw), BATCH):
        deel = [u for u in nieuw[i:i + BATCH] if not any(u in v for v in open_.values())]
        if not deel:
            continue
        try:
            rid = start_run(deel)
        except (urllib.error.URLError, RuntimeError, ValueError) as exc:
            log(f'APIFY: run starten mislukt ({str(exc)[:100]}); verder met Serper/SerpApi.')
            return -1
        open_[rid] = deel
        log(f'APIFY: {len(deel)} LinkedIn-profielen aangevraagd.')
    binnen, start = 0, nu()
    while open_ and nu() - start < MAX_WACHT:
        for rid in list(open_):
            try:
                info = run_info(rid)
            except (urllib.error.URLError, RuntimeError, ValueError) as exc:
                log(f'APIFY: status opvragen lukte niet ({str(exc)[:80]}).')
                continue
            st = info.get('status', '')
            if st in ('FAILED', 'ABORTED', 'TIMED-OUT'):
                log(f'APIFY: run {st.lower()}; die profielen gaan via Serper/SerpApi.')
                for u in open_.pop(rid):
                    cache[PREFIX + slug(u)] = {'fout': st.lower()}
                continue
            if st != 'SUCCEEDED':
                continue
            try:
                profielen = items(info.get('defaultDatasetId'))
            except (urllib.error.URLError, RuntimeError, ValueError) as exc:
                log(f'APIFY: resultaten ophalen lukte niet ({str(exc)[:80]}); volgende ronde opnieuw.')
                continue
            gevraagd = open_.pop(rid)
            gezien = set()
            lijst = profielen if isinstance(profielen, list) else []
            if len(lijst) == 1 and set(lijst[0]) == {'error'} and 'limited to' in str(lijst[0]['error']):
                log('APIFY: ' + str(lijst[0]['error'])[:160])
                for u in gevraagd:
                    cache.pop(PREFIX + slug(u), None)      # niet als 'niet gevonden' onthouden: later opnieuw
                continue
            for p in lijst:
                q = p.get('query') or {}
                s_ = slug(p.get('linkedinUrl') or '') or str(p.get('publicIdentifier') or q.get('publicIdentifier') or '').lower()
                if not s_:
                    continue
                gezien.add(s_)
                if p.get('error') or (p.get('status') not in (None, 200)):
                    cache[PREFIX + s_] = {'fout': str(p.get('error') or p.get('status'))[:80]}
                    continue
                schoon = {k: v for k, v in p.items() if k not in NEGEER_VELDEN}
                cache[PREFIX + s_] = schoon
                # Profiel kan onder een nieuwer adres terugkomen: ook bewaren onder het adres dat we vroegen.
                oq = p.get('originalQuery')
                vraag = slug(oq.get('url') or oq.get('query') or '') if isinstance(oq, dict) else slug(str(oq or ''))
                if vraag and vraag != s_:
                    cache[PREFIX + vraag] = schoon
                    gezien.add(vraag)
                binnen += 1
            for u in gevraagd:
                if slug(u) not in gezien:
                    cache[PREFIX + slug(u)] = {'fout': 'niet gevonden'}
        if open_:
            slaap(PEIL)
    if open_:
        log(f'APIFY: {sum(len(v) for v in open_.values())} profielen nog niet klaar na {MAX_WACHT // 60} min; '
            'die gaan nu via Serper/SerpApi en worden bij de volgende scan opgehaald.')
    if binnen:
        log(f'APIFY: {binnen} profielen opgehaald.')
    return binnen


def _datum(d):
    if isinstance(d, dict):
        return d.get('text') or ' '.join(str(x) for x in (d.get('month'), d.get('year')) if x)
    return str(d or '')


def gegevens(p):
    if not p or p.get('fout'):
        return {}
    werk = []
    for e in p.get('experience') or []:
        if isinstance(e, dict):
            periode = ' - '.join(x for x in (_datum(e.get('startDate')), _datum(e.get('endDate'))) if x)
            werk.append(' · '.join(x for x in (e.get('position') or '', e.get('companyName') or '', periode) if x))
    opl = []
    for e in p.get('education') or []:
        if isinstance(e, dict):
            periode = e.get('period') or ' - '.join(x for x in (_datum(e.get('startDate')), _datum(e.get('endDate'))) if x)
            opl.append(' · '.join(x for x in (e.get('schoolName') or '', e.get('degree') or '', e.get('fieldOfStudy') or '', periode) if x))
    cert = [c.get('title', '') for c in p.get('certifications') or [] if isinstance(c, dict)]
    cert += [c.get('title', '') for c in p.get('courses') or [] if isinstance(c, dict)]
    vaard = [v.get('name', '') if isinstance(v, dict) else str(v) for v in p.get('skills') or []]
    huidig = (p.get('currentPosition') or [{}])[0] if isinstance(p.get('currentPosition'), list) and p.get('currentPosition') else {}
    eerste = (p.get('experience') or [{}])[0] if p.get('experience') else {}
    loc = p.get('location') or {}
    naam = ' '.join(x for x in (p.get('firstName'), p.get('lastName')) if x)
    return {
        'naam': naam,
        'functie': (eerste.get('position') if isinstance(eerste, dict) else '') or p.get('headline') or '',
        'werkgever': huidig.get('companyName') or (eerste.get('companyName') if isinstance(eerste, dict) else '') or '',
        'werkervaring': [w for w in werk if w],
        'opleidingen': [o for o in opl if o],
        'certificeringen': [c for c in cert if c],
        'vaardigheden': [v for v in vaard if v][:40],
        'over': (p.get('about') or '')[:1500],
        'kop': p.get('headline') or '',           # kopregel onder de naam (soms staat daar mail/telefoon)
        'locatie': loc.get('linkedinText', '') if isinstance(loc, dict) else str(loc),
        'email': '',          # e-mailzoeken staat uit; op LinkedIn zelf staat geen e-mail
        'telefoon': '',
        'url': nette_url(p.get('linkedinUrl') or ''),
    }


def tekst(p):
    g = gegevens(p)
    if not g:
        return ''
    regels = [g['naam'], f"{g['functie']} at {g['werkgever']}" if g['werkgever'] else g['functie'], g['locatie']]
    regels += [w.replace(' · ', ' at ', 1) for w in g['werkervaring']]
    regels += g['opleidingen'] + g['certificeringen'] + [g['over']]
    return '\n'.join(r for r in regels if r)
