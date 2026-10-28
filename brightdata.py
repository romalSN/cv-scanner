"""Bright Data Web Scraper API: openbare LinkedIn-profielen ophalen ('LinkedIn people profiles – collect by URL').

- Sleutel: environment variable BRIGHTDATA_API_KEY (de scanner zet die vanuit brightdata_sleutel.txt).
- Dataset: BRIGHTDATA_DATASET_ID (standaard gd_l1viktl72bvl7bjuj0 = People profiles).
- Asynchroon in batches: trigger -> snapshot_id -> progress pollen -> snapshot downloaden.
- Dubbele URL's eruit; resultaten in een cache (opnieuw opvragen kost dan niets).
- Berichten/likes/activiteit van anderen worden NIET gebruikt.
"""
import json
import os
import re
import time
import urllib.error
import urllib.request

API = 'https://api.brightdata.com/datasets/v3'
DATASET_STANDAARD = 'gd_l1viktl72bvl7bjuj0'
BATCH = 100                # URL's per snapshot (1.500 cv's = 15 snapshots, die tegelijk lopen)
MAX_WACHT = 15 * 60        # seconden per snapshot
NEGEER_VELDEN = {'activity', 'posts', 'people_also_viewed', 'similar_profiles', 'recommendations',
                 'recommendations_count', 'followers', 'connections', 'banner_image', 'avatar', 'image_url',
                 'default_avatar', 'memorialized_account', 'bio_links'}


def sleutel():
    return os.environ.get('BRIGHTDATA_API_KEY', '').strip()


def dataset_id():
    return os.environ.get('BRIGHTDATA_DATASET_ID', '').strip() or DATASET_STANDAARD


def slug(url):
    m = re.search(r'linkedin\.com/in/([^/?#\s]+)', str(url or ''), re.I)
    return m.group(1).lower().rstrip('/') if m else ''


def nette_url(url):
    s = slug(url)
    return f'https://www.linkedin.com/in/{s}/' if s else ''


def _verzoek(methode, pad, body=None, timeout=60):
    req = urllib.request.Request(API + pad, method=methode,
                                 data=json.dumps(body).encode('utf-8') if body is not None else None,
                                 headers={'Authorization': f'Bearer {sleutel()}', 'Content-Type': 'application/json',
                                          'User-Agent': 'CV-Scanner'})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        raw = r.read().decode('utf-8', 'replace')
    return raw


def _met_wachten(fn, *args, **kw):
    """Bij 429 (te veel verzoeken) netjes wachten en opnieuw (max 5x), nooit blijven hameren."""
    wacht = 10
    for _ in range(5):
        try:
            return fn(*args, **kw)
        except urllib.error.HTTPError as exc:
            if exc.code != 429:
                raise
            ra = exc.headers.get('Retry-After') if exc.headers else None
            time.sleep(int(ra) if ra and ra.isdigit() else wacht)
            wacht = min(wacht * 2, 120)
    raise RuntimeError('Bright Data: te veel verzoeken (429), later opnieuw')


def start_snapshot(urls):
    raw = _met_wachten(_verzoek, 'POST', f'/trigger?dataset_id={dataset_id()}&include_errors=true',
                       [{'url': u} for u in urls])
    sid = json.loads(raw).get('snapshot_id')
    if not sid:
        raise RuntimeError('Bright Data gaf geen snapshot_id: ' + raw[:150])
    return sid


def status(sid):
    return json.loads(_met_wachten(_verzoek, 'GET', f'/progress/{sid}')).get('status', '')


def download(sid):
    raw = _met_wachten(_verzoek, 'GET', f'/snapshot/{sid}?format=json', timeout=180)
    data = json.loads(raw)
    if isinstance(data, dict):           # soms {"status": "building"} of één profiel
        return [] if data.get('status') else [data]
    return data


def ophalen(urls, cache, log=print, slaap=time.sleep, nu=time.time):
    """Haal profielen op voor alle URL's die nog niet in de cache staan. cache: dict, sleutel 'bd|<slug>'.
    Lopende snapshots worden onthouden ('bd_open'), zodat een onderbroken scan ze later alsnog ophaalt.
    Geeft het aantal nieuw opgehaalde profielen."""
    if not sleutel():
        return 0
    nieuw = []
    for u in urls:
        s = slug(u)
        if s and f'bd|{s}' not in cache and nette_url(u) not in nieuw:
            nieuw.append(nette_url(u))
    open_ = cache.setdefault('bd_open', {})
    for i in range(0, len(nieuw), BATCH):
        deel = [u for u in nieuw[i:i + BATCH] if not any(u in v for v in open_.values())]
        if not deel:
            continue
        try:
            sid = start_snapshot(deel)
        except (urllib.error.URLError, RuntimeError, ValueError) as exc:
            log(f'BRIGHT DATA: aanvraag mislukt ({str(exc)[:100]}); verder met Serper/SerpApi.')
            return -1
        open_[sid] = deel
        log(f'BRIGHT DATA: {len(deel)} LinkedIn-profielen aangevraagd (snapshot {sid[:10]}…).')
    binnen = 0
    for sid in list(open_):
        start = nu()
        st = ''
        while nu() - start < MAX_WACHT:
            try:
                st = status(sid)
            except (urllib.error.URLError, RuntimeError, ValueError) as exc:
                log(f'BRIGHT DATA: status opvragen lukte niet ({str(exc)[:80]}).')
                break
            if st in ('ready', 'failed', 'canceled'):
                break
            slaap(10)
        if st != 'ready':
            if st in ('failed', 'canceled'):
                log(f'BRIGHT DATA: snapshot {sid[:10]}… {st}; die profielen gaan via Serper/SerpApi.')
                for u in open_.pop(sid):
                    cache[f'bd|{slug(u)}'] = {'fout': st}
            else:
                log(f'BRIGHT DATA: snapshot {sid[:10]}… nog niet klaar; volgende scan haalt hem op.')
            continue
        try:
            profielen = download(sid)
        except (urllib.error.URLError, RuntimeError, ValueError) as exc:
            log(f'BRIGHT DATA: downloaden lukte niet ({str(exc)[:80]}); volgende scan opnieuw.')
            continue
        gevraagd = open_.pop(sid)
        gezien = set()
        for p in profielen:
            s = slug(p.get('url') or p.get('input_url') or (p.get('input') or {}).get('url', ''))
            if not s:
                continue
            gezien.add(s)
            if p.get('error') or p.get('error_code'):
                cache[f'bd|{s}'] = {'fout': str(p.get('error') or p.get('error_code'))[:80]}
                continue
            cache[f'bd|{s}'] = {k: v for k, v in p.items() if k not in NEGEER_VELDEN}
            binnen += 1
        for u in gevraagd:
            if slug(u) not in gezien:
                cache[f'bd|{slug(u)}'] = {'fout': 'niet gevonden'}
    if binnen:
        log(f'BRIGHT DATA: {binnen} profielen opgehaald.')
    return binnen


def _lijst(x):
    return x if isinstance(x, list) else ([] if x in (None, '') else [x])


def _txt(x):
    if isinstance(x, dict):
        return ' '.join(str(v) for v in x.values() if isinstance(v, (str, int)) and v)
    return str(x or '')


def gegevens(p):
    """Nette velden uit een Bright Data-profiel (alleen het profiel zelf, geen activiteit van anderen)."""
    if not p or p.get('fout'):
        return {}
    cc = p.get('current_company') or {}
    werk = []
    for e in _lijst(p.get('experience')):
        if not isinstance(e, dict):
            continue
        bedrijf = e.get('company') or e.get('company_name') or ''
        for pos in (_lijst(e.get('positions')) or [e]):
            if not isinstance(pos, dict):
                continue
            titel = pos.get('title') or e.get('title') or ''
            periode = ' - '.join(x for x in (str(pos.get('start_date') or ''), str(pos.get('end_date') or '')) if x)
            werk.append(' · '.join(x for x in (titel, bedrijf, periode) if x))
    opl = []
    for e in _lijst(p.get('education')):
        if isinstance(e, dict):
            jaren = ' - '.join(str(x) for x in (e.get('start_year'), e.get('end_year')) if x)
            opl.append(' · '.join(x for x in (e.get('title') or e.get('school') or '', e.get('degree') or '',
                                                e.get('field') or '', jaren) if x))
    if not opl and p.get('educations_details'):
        opl = [_txt(p.get('educations_details'))]
    cert = [_txt(c.get('title') if isinstance(c, dict) else c) for c in _lijst(p.get('certifications'))]
    cursus = [_txt(c.get('title') if isinstance(c, dict) else c) for c in _lijst(p.get('courses'))]
    vaard = [_txt(v) for v in _lijst(p.get('skills'))]
    return {
        'naam': p.get('name') or '',
        'functie': p.get('position') or (cc.get('title') if isinstance(cc, dict) else '') or '',
        'werkgever': (cc.get('name') if isinstance(cc, dict) else _txt(cc)) or '',
        'werkervaring': [w for w in werk if w],
        'opleidingen': [o for o in opl if o],
        'certificeringen': [c for c in cert + cursus if c],
        'vaardigheden': [v for v in vaard if v],
        'over': (p.get('about') or '')[:1500],
        'locatie': p.get('city') or p.get('location') or '',
        # contact alleen als het rechtstreeks op het profiel staat
        'email': p.get('email') or '',
        'telefoon': p.get('phone') or p.get('phone_number') or '',
        'url': nette_url(p.get('url') or ''),
    }


def tekst(p):
    """Profieltekst voor de vergelijking met het cv (werk 'bij X', school, jaren, functie, over)."""
    g = gegevens(p)
    if not g:
        return ''
    regels = [g['naam'], f"{g['functie']} at {g['werkgever']}" if g['werkgever'] else g['functie'], g['locatie']]
    regels += [w.replace(' · ', ' at ', 1) for w in g['werkervaring']]
    regels += g['opleidingen'] + g['certificeringen'] + [g['over']]
    return '\n'.join(r for r in regels if r)


def werkervaring_ontbreekt(p):
    g = gegevens(p)
    return bool(g) and not g['werkervaring']
