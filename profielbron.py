"""Welke dienst haalt LinkedIn-profielen op? Apify (als APIFY_TOKEN er is), anders Bright Data.
Zelfde functies voor beide: sleutel, slug, ophalen, gegevens, tekst, PREFIX, NAAM."""
import apify_linkedin
import brightdata

brightdata.PREFIX = 'bd|'
brightdata.NAAM = 'Bright Data'


def kies():
    if apify_linkedin.sleutel():
        return apify_linkedin
    return brightdata


_m = kies()
sleutel, slug, ophalen, gegevens, tekst, PREFIX, NAAM = _m.sleutel, _m.slug, _m.ophalen, _m.gegevens, _m.tekst, _m.PREFIX, _m.NAAM
