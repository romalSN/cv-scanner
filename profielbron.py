"""LinkedIn-profielen ophalen via Apify (HarvestAPI LinkedIn Profile Scraper, zonder cookies).
Daarna (in de scanner) Serper -> SerpApi -> Tavily voor zoeken, extra bewijs en contact (X-ray)."""
import apify_linkedin

slug = apify_linkedin.slug
BRONNEN = (apify_linkedin,)


def sleutel():
    return any(m.sleutel() for m in BRONNEN)


def ophalen(urls, cache, log=print, **kw):
    totaal = 0
    for m in BRONNEN:
        if m.sleutel():
            totaal += max(m.ophalen(urls, cache, log=log, **kw), 0)
    return totaal


def _profiel(cache, s):
    for m in BRONNEN:
        p = cache.get(m.PREFIX + s)
        if p and not p.get('fout'):
            return m, p
    return None, None


def tekst_van(cache, s):
    m, p = _profiel(cache, s)
    return m.tekst(p) if m else ''


def gegevens_van(cache, s):
    m, p = _profiel(cache, s)
    return m.gegevens(p) if m else {}
