#!/usr/bin/env python3
"""CV Crawler V8: lokale CV-indexering voor Windows en macOS, deduplicatie en exports.

Werkt op Python 3.10+. Schrijft nooit in originele mappen.
"""
from __future__ import annotations

import argparse
import base64
import tempfile
import collections
import csv
import datetime as dt
import difflib
import hashlib
import html
import json
import os
from pathlib import Path
import re
import shutil
import sqlite3
import subprocess
import sys
import time
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
import uuid
import xml.etree.ElementTree as ET
import zipfile

CLASSIFIER_VERSION = 27
# Al beoordeelde cv's en geen-cv's vanaf deze versie worden bij een nieuwere herkenning NIET opnieuw
# gelezen (scheelt uren bij 15.000 bestanden); alleen twijfelgevallen en fouten worden opnieuw gelezen.
# Alleen verhogen als een wijziging ook voor al gevonden cv's nodig is.
KEEP_FROM_VERSION = 18


def maybe_cv(reason):
    """'Geen cv' dat toch cv-trekjes had (kopjes of een cv-kop): wel opnieuw beoordelen bij een
    nieuwe herkenning, want misschien wordt het nu wel herkend (bv. Engelstalig cv)."""
    r = (reason or '').lower()
    if 'geen leesbare tekst' in r or 'vacature' in r or 'sjabloon' in r:
        return False
    m = re.search(r'kopjes: ([^;]*)', r)
    # V20: ook 'geen cv' met eigen contactgegevens: misschien een cv met kopjes die we toen nog niet kenden
    # (Personalia, Opdrachten …) of een naam in een tabel ('Naam   Jan de Vries').
    return bool(m and m.group(1).strip() != 'geen') or 'cv-kop: ja' in r or 'contact: ja' in r


def keep_cv_name(name, reason=''):
    """Al gevonden cv: mag het blijven staan zonder opnieuw lezen? Alleen als de naam (opgeschoond) klopt.
    Geeft de opgeschoonde naam terug, of '' = opnieuw lezen."""
    if 'naam = bedrijfsnaam' in (reason or ''):
        return ''    # Een bedrijf mag nooit als naam van een kandidaat blijven staan.
    return tidy_name(clean_person_name(name or ''))
SUPPORTED = {'.pdf', '.doc', '.docx', '.txt', '.rtf', '.odt', '.pages'}
# Zip-archieven worden alleen gelezen, nooit uitgepakt in de map of gewijzigd.
ARCHIVES = {'.zip'}
ZIP_SEP = ' › '          # 'C:\\map\\sollicitaties.zip › Jan/CV Jan.pdf'
ZIP_MAX_MEMBERS = 2000   # meer bestanden in één zip: rest overslaan
ZIP_MAX_TOTAL_MB = 500   # totaal uitgepakte grootte per zip
GROOT_MB = 500           # pdf/docx tot deze grootte worden toch gelezen (pdf: alleen de eerste pagina's)


def limiet_mb(ext, args):
    """Grote pdf's (vaak door foto's) en .docx-bestanden (tekst staat los van de plaatjes) wel lezen."""
    return GROOT_MB if str(ext).lower() in ('.pdf', '.docx') else args.max_mb
ZIP_MAX_RATIO = 200      # extreem gecomprimeerd = mogelijke zip-bom


def container_path(path_str):
    """Het echte bestand op schijf: bij 'x.zip › cv.pdf' is dat x.zip."""
    return Path(str(path_str).split(ZIP_SEP, 1)[0])
SKIP_DIRS = {'.git', '.svn', '.Trash', '.cache', '.npm', '.venv', 'venv',
             '__pycache__', 'node_modules', 'Caches', 'Cache', 'tmp', 'Temp',
             '.Spotlight-V100', '.fseventsd', 'DerivedData', 'site-packages',
             '$Recycle.Bin', 'System Volume Information', 'Windows', 'Program Files',
             'Program Files (x86)', 'ProgramData', 'Recovery', 'PerfLogs',
             'Application Data', 'Local Settings', 'Microsoft', 'Packages'}
EMAIL_RE = re.compile(r'(?<![\w.+-])[\w.+%-]+@[A-Z\d.-]+\.[A-Z]{2,}(?![\w.-])', re.I)
URL_RE = re.compile(r'(?:https?://)?(?:www\.)?linkedin\.com/in/[a-z0-9%._~-]+/?', re.I)
PHONE_RE = re.compile(r'(?<!\w)(?:\+\s?\d{1,3}[\s.\-()]*)?(?:\(?\d{1,4}\)?[\s.\-]*){2,5}\d{2,4}(?!\w)')
CV_TERMS = {
 'experience': ('werkervaring', 'werk ervaring', 'employment history', 'work experience',
                'professional experience', 'ervaring', 'employment', 'loopbaan', 'berufserfahrung'),
 'education': ('opleiding', 'opleidingen', 'education', 'onderwijs', 'universiteit',
               'hogeschool', 'bachelor', 'master', 'diploma', 'degree'),
 'skills': ('vaardigheden', 'competenties', 'skills', 'expertise', 'technische kennis',
            'kwalificaties', 'certificeringen', 'certifications'),
 'profile': ('profiel', 'persoonlijk profiel', 'profile', 'about me', 'samenvatting',
             'professional summary', 'over mij'),
 'job': ('functie', 'werkgever', 'employer', 'responsibilities', 'projecten',
         'projects', 'taken en verantwoordelijkheden', 'bedrijf'),
}
EXCLUDE_NAME = {'curriculum vitae', 'resume', 'resumé', 'cv', 'werkervaring',
 'education', 'opleiding', 'profiel', 'contact', 'persoonlijke gegevens',
 'personal details', 'skills', 'vaardigheden', 'sollicitatie', 'contactgegevens',
 'professional summary', 'motivatiebrief', 'cover letter', 'profile',
 'software engineer', 'project manager', 'sales manager', 'data analyst',
 'account manager', 'marketing manager', 'software developer', 'senior developer',
 'professional experience', 'human resources', 'portfolio', 'junior consultant'}
STOP_WORDS = {'de','het','een','van','voor','met','aan','bij','op','in','en','of','te',
 'as','an','and','the','at','for','to','from','this','with','are','was','were',
 'work','experience','education','skills','team','project','projects','ervaring',
 'werkzaamheden','verantwoordelijk','kennis','jaar','jaren','year','years',
 'ontwikkeling','management','bedrijf','binnen','door','sinds','opleiding',
 'werkervaring','company','functie','werkgever','profiel','profile', '2021', '2022',
 '2023','2024','2025','2026','2027'}


def configure_console():
    """Always emit Unicode on Windows, including filenames with non-ANSI symbols.

    Avoid fatal UnicodeEncodeError when the GUI runs Python with cp1252 stdout.
    """
    for stream_name in ('stdout', 'stderr'):
        stream = getattr(sys, stream_name, None)
        if stream is not None and hasattr(stream, 'reconfigure'):
            try:
                stream.reconfigure(encoding='utf-8', errors='backslashreplace')
            except (OSError, ValueError, LookupError):
                pass


def safe_xml_cell(value):
    """Clean illegal XML chars and unpaired surrogates in names/file paths."""
    value = str(value if value is not None else '')
    value = value.encode('utf-8', errors='backslashreplace').decode('utf-8')
    return ''.join(c for c in value if ord(c) in (9, 10, 13) or
                   32 <= ord(c) <= 0xD7FF or 0xE000 <= ord(c) <= 0xFFFD or
                   0x10000 <= ord(c) <= 0x10FFFF)


def asciifold(s):
    return ''.join(c for c in unicodedata.normalize('NFKD', s) if not unicodedata.combining(c))


def normalized_name(s):
    s = asciifold(s or '').casefold()
    s = re.sub(r'\b(dr|mr|mevr|mw|mevrouw|dhr|ing|msc|bsc)\.?\b', '', s)
    return ' '.join(re.findall(r'[a-z]+', s))


# Eigennamen worden niet afgeleid van willekeurige kopjes, rapporttitels of rollen.
NAME_BLOCKLIST = {
    'advies', 'adviseur', 'analyse', 'assessment', 'automatisering', 'beleidsplan',
    'boek', 'brief', 'business', 'competenties', 'contexten', 'contract', 'cv',
    'curriculum', 'data', 'document', 'documentatie', 'domeinen', 'evaluatie',
    'feedback', 'formulier', 'functie', 'gemeente', 'hogeschool', 'informatie',
    'installatie', 'introductie', 'jaarverslag', 'kennis', 'management',
    'manager', 'marketing', 'motivatie', 'onderwijs', 'onderzoek', 'opdracht',
    'organisatie', 'overzicht', 'planning', 'portfolio', 'presentatie',
    'profiel', 'project', 'projecten', 'projectplanning', 'rapport',
    'rapportage', 'samenvatting', 'school', 'sollicitatie', 'stageovereenkomst',
    'strategie', 'studie', 'team', 'template', 'test', 'training', 'universiteit',
    'vacature', 'vacaturetekst', 'vaardigheden', 'verbetering', 'versie',
    'voorbeeld', 'werkervaring', 'werkgever', 'werknemer', 'werkzaamheden',
    'woords', 'woorden', 'zelfportret', 'zoekopdracht', 'opleiding', 'hbo', 'ict',
    'ictervaring', 'solution', 'solutions', 'employment', 'experience',
    'personal', 'details', 'resume', 'profile', 'education', 'skills',
    'professional', 'summary', 'company', 'references', 'consultant',
    'technical', 'report', 'application', 'project', 'prompt',
    # kopjes en labels die bovenaan een cv staan (geen naam)
    'gegevens', 'persoonlijke', 'persoonsgegevens', 'algemene', 'personalia', 'ervaring', 'relevante',
    'recente', 'opdrachten', 'opdracht', 'vitae', 'curiculum', 'cirriculum', 'curricilum', 'curricullum',
    'curriculim', 'voornamen', 'woonplaats', 'adres', 'address', 'naam', 'name', 'voornaam', 'achternaam', 'roepnaam',
    'full', 'kandidaat', 'candidate', 'medewerker', 'contact', 'information', 'informatie', 'geboortedatum',
    'nationaliteit', 'nationality', 'rijbewijs', 'sex', 'male', 'female', 'gender', 'geslacht', 'telefoon',
    'mobile', 'phone', 'email', 'pagina', 'page', 'recent', 'change', 'about', 'the',
    # functies, vakgebieden en bedrijfswoorden (een functietitel of bedrijf is geen naam)
    'developer', 'engineer', 'software', 'java', 'frontend', 'backend', 'fullstack', 'senior', 'junior',
    'medior', 'beheerder', 'applicatie', 'technisch', 'contractmanager', 'architect', 'programmeur',
    'tester', 'analist', 'analyst', 'specialist', 'designer', 'agile', 'scrum', 'design', 'systems',
    'concepting', 'typescript', 'react', 'informatica', 'hogere', 'award', 'winning', 'bv', 'ltd', 'inc',
    'gmbh', 'holding', 'group', 'groep', 'service', 'services', 'consultancy', 'experts', 'united',
    'netherlands', 'nederland', 'neterlands', 'wonen', 'geschikt', 'belastingdienst', 'logius',
    'employer', 'instelling', 'bedrijf', 'rws', 'type', 'script', 'sint', 'info', 'intro', 'master',
    'ontwikkelaar', 'present', 'current', 'projectverslag', 'verslag', 'rapport', 'scriptie', 'thesis',
    'afstudeerverslag', 'stageverslag', 'plan', 'notitie', 'memo', 'offerte', 'factuur', 'brief',
    # kopjes uit 'persoonlijke' cv's: 'Mijn Kracht', 'Mijn Missie', 'My Strengths'
    'mijn', 'kracht', 'krachten', 'missie', 'visie', 'motto', 'ambitie', 'ambities', 'doel', 'doelen', 'passie',
    'drijfveren', 'drijfveer', 'kernwaarden', 'kwaliteiten', 'my', 'mission', 'vision', 'strengths', 'goals',
    'hobby', 'hobbys', 'interesses', 'referenties', 'references', 'diploma', 'deelcertificaat', 'certificaat',
    'nospam', 'spam', 'mij', 'macrobutton', 'mergefield', 'formtext', 'productowner', 'owner'
}
# Een bedrijfsnaam is nooit een persoon: dan de hele 'naam' afkeuren (niet alleen dat woord weghalen).
COMPANY_WORDS = {'bv', 'bvba', 'nv', 'vof', 'ltd', 'inc', 'gmbh', 'llc', 'holding', 'group', 'groep',
                 'consultancy', 'services', 'service', 'solutions', 'experts', 'united', 'company', 'stichting',
                 'verhuur', 'techniek', 'installatietechniek', 'installaties', 'transport', 'logistiek', 'advies',
                 'adviesbureau', 'makelaardij', 'onderhoud', 'schoonmaak', 'beveiliging', 'accountancy',
                 'accountants', 'diensten', 'dienstverlening', 'media', 'partners', 'detachering', 'uitzendbureau',
                 'recruitment', 'interim', 'automatisering', 'software', 'webdesign', 'reclame', 'vastgoed',
                 'kinderopvang', 'thuiszorg', 'tandartspraktijk', 'praktijk', 'kliniek', 'apotheek', 'garage',
                 'autobedrijf', 'bakkerij', 'restaurant', 'cafe', 'hotel', 'winkel', 'webshop'}
JOB_ENDINGS = ('owner', 'manager', 'developer', 'engineer', 'beheerder', 'consultant', 'adviseur', 'architect', 'analist',
               'ontwikkelaar', 'specialist', 'coordinator', 'programmeur', 'tester', 'designer', 'medewerker')
NAME_FILLERS = {'de','den','der','van','von','ten','ter','te','het','el','al','bin','di', 'da'}


def reasonable_name(value):
    name = value.strip()
    if not (5 <= len(name) <= 65) or re.search(r'[,;:!?@/\\|_\d]', name):
        return False
    if re.search(r'\s+[–—-]\s+', name):
        return False
    if not re.fullmatch(r"[A-Za-zÀ-ÿ.'’\-\s]+", name):
        return False
    words = normalized_name(name).split()
    if not (2 <= len(words) <= 5):
        return False
    if any(w in NAME_BLOCKLIST or w.endswith(JOB_ENDINGS) for w in words):
        return False
    if words[0] in NAME_FILLERS or words[-1] in NAME_FILLERS:
        return False
    meaningful = [w for w in words if w not in NAME_FILLERS]
    if len(meaningful) < 2 or any(len(w) < 2 for w in meaningful):
        return False
    # Een titel als 'Grote Verschillen' is op zichzelf geen naam; elders
    # eist de classifier cv-structuur en contactinformatie.
    return True


# Expliciete labels mogen niet in de naam terechtkomen, ook niet als de
# gebruiker de velden op afzonderlijke regels heeft gezet.
PERSON_LABEL = re.compile(
    r'(?i)^(voornamen|voornaam|first\s*name|given\s*name|achternaam|familienaam|surname|last\s*name|family\s*name|'
    r'volledige\s*naam|full\s*name|roepnaam|naam\s*kandidaat|naam|name|candidate\s*name|kandidaat)'
    r'(?:\s*[:：=\-]\s*|\s+(?=[A-ZÀ-Ý])|\s*$)(.*)$')
# Na de naam volgt in tabellen vaak meteen het volgende veld: 'Name Jan Smit Sex Male'.
NEXT_FIELD = re.compile(r'(?i)\s+(?:sex|gender|geslacht|geboren|geboortedatum|geboorteplaats|date\s+of\s+birth|'
                        r'birth\s*date|nationaliteit|nationality|adres|address|woonplaats|residence|telefoon|tel\.?|'
                        r'phone|mobiel|mobile|e-?mail|burgerlijke\s+staat|marital|rijbewijs|driving|leeftijd|age|'
                        r'functie|beschikbaar|postcode|plaats|city)\b.*$')


def _roepnaam_tussen_haakjes(v):
    """'ing. R.M. (Ruben) Knol' -> 'Ruben Knol'; 'J. (Jan) Moors' -> 'Jan Moors'; titels ervoor eraf."""
    v = re.sub(r'(?i)^(?:(?:ing|ir|drs|dr|mr|prof|ds)\.?\s+)+', '', v.strip())
    m = re.search(r'\(\s*([A-ZÀ-Ý][a-zà-ÿ]+(?:[- ][A-ZÀ-Ý][a-zà-ÿ]+)?)\s*\)', v)
    if m:
        rest = (v[:m.start()] + ' ' + v[m.end():]).strip()
        rest = re.sub(r'\b(?:[A-Z]\.\s*)+', ' ', rest)          # voorletters weg: de roepnaam staat erbij
        v = ' '.join((m.group(1) + ' ' + rest).split())
    return v


def name_parts(value):
    """Label-only of gelabelde regels in de eerste 45 tekstregels uitlezen."""
    lines = [' '.join(v.split()) for v in value.splitlines()[:45]]
    fields = {}
    for i, line in enumerate(lines[:38]):
        m = PERSON_LABEL.match(line)
        if not m:
            continue
        label = re.sub(r'\s+', '', m.group(1).casefold())
        remainder = m.group(2).strip()
        # Soms staat 'Voornaam: Jan Achternaam: de Vries' op één regel.
        suffix = re.search(r'(?i)\s+(?=(?:achternaam|last\s*name|surname|familienaam)\s*[:：=])', remainder)
        if suffix:
            nextfield = PERSON_LABEL.match(remainder[suffix.end():])
            if nextfield:
                fields[re.sub(r'\s+', '', nextfield.group(1).casefold())] = nextfield.group(2).strip()
                remainder = remainder[:suffix.start()].strip()
        if not remainder and i + 1 < len(lines):
            nxt = lines[i+1].strip()
            if nxt and not PERSON_LABEL.match(nxt) and not re.match(r'(?i)^(?:e-?mail|telefoon|phone|linkedin|adres|address)\s*[:：=]',nxt):
                remainder = nxt
        remainder = _roepnaam_tussen_haakjes(NEXT_FIELD.sub('', remainder).strip())
        if remainder:
            fields[label] = remainder
    # Roepnaam gaat voor ('Johannes Pieter' heet 'Hans').
    given = next((fields[k] for k in ('roepnaam','voornaam','firstname','givenname','voornamen') if fields.get(k)), '')
    family = next((fields[k] for k in ('achternaam','familienaam','surname','lastname','familyname') if fields.get(k)), '')
    complete = next((fields[k] for k in ('volledigenaam','fullname','naamkandidaat','naam','name','candidatename','kandidaat')
                     if fields.get(k)), '')
    if given and family:
        candidate = f'{given} {family}'
        if reasonable_name(candidate):
            return candidate
    if given and complete and len(complete.split()) == 1 and not family:
        candidate = f'{given} {complete}'          # 'Naam Belonje' + 'Roepnaam Hans'
        if reasonable_name(candidate):
            return candidate
    if family and complete and len(complete.split()) == 1:
        candidate = f'{complete} {family}'
        if reasonable_name(candidate):
            return candidate
    if complete and reasonable_name(complete):
        return complete
    return ''


LOWER_PARTICLES = {'van','de','den','der','ten','ter','te','het','von','du','da','di'}


def tidy_name(name):
    """'THOMAS VAN DEN BERG' -> 'Thomas van den Berg'; 'Baris YAPAR' -> 'Baris Yapar';
    'MENKVELD Jan-Willem' (achternaam eerst in hoofdletters) -> 'Jan-Willem Menkveld'."""
    if not name:
        return name
    words = name.split()
    if not (name.isupper() or name.islower()) and any(w.isupper() and len(w) > 2 for w in words):
        if len(words) >= 2 and words[0].isupper() and len(words[0]) > 2 and not any(w.isupper() for w in words[1:]):
            words = words[1:] + [words[0]]          # 'MENKVELD Jan-Willem'
        fixed = []
        for w in words:
            if w.isupper() and len(w) > 1:
                low = w.lower()
                fixed.append(low if low in LOWER_PARTICLES and fixed else
                             '-'.join(x[:1].upper() + x[1:] for x in low.split('-')))
            else:
                fixed.append(w)
        return ' '.join(fixed)
    if not (name.isupper() or name.islower()):
        return name
    words = name.lower().split()
    out = []
    for i, w in enumerate(words):
        if 0 < i < len(words)-1 and w in LOWER_PARTICLES:
            out.append(w)
        else:
            out.append('-'.join(part[:1].upper() + part[1:] for part in w.split('-')))
    return ' '.join(out)


def name_from_linkedin(link):
    """'linkedin.com/in/jan-de-boer-12ab3' -> 'Jan de Boer' (lokaal, kost niets)."""
    slug = urllib.parse.unquote(str(link).rstrip('/').rsplit('/', 1)[-1])
    parts = [w for w in re.split(r'[-_.]+', slug) if w]
    while parts and (any(ch.isdigit() for ch in parts[-1]) or (len(parts) > 2 and len(parts[-1]) <= 2 and parts[-1].lower() not in NAME_FILLERS)):
        parts.pop()   # achtervoegsels als '12ab3' of 'b4' weghalen
    candidate = ' '.join(parts)
    return tidy_name(candidate.lower()) if reasonable_name(candidate) else ''


TITLE_BEFORE = re.compile(r'(?i)^(?:ir|ing|drs|dr|mr|prof|dhr|mevr|mw|mevrouw|de heer|mrs|ms|miss)\.?\s+')
PRIVE_MAILDOMEINEN = {'gmail.com', 'googlemail.com', 'hotmail.com', 'hotmail.nl', 'live.nl', 'live.com', 'outlook.com',
                      'outlook.nl', 'msn.com', 'yahoo.com', 'yahoo.nl', 'icloud.com', 'me.com', 'mac.com', 'ziggo.nl',
                      'kpnmail.nl', 'kpnplanet.nl', 'planet.nl', 'xs4all.nl', 'home.nl', 'hetnet.nl', 'chello.nl',
                      'casema.nl', 'upcmail.nl', 'tele2.nl', 'online.nl', 'zeelandnet.nl', 'quicknet.nl',
                      'telenet.be', 'skynet.be', 'proximus.be', 'protonmail.com', 'proton.me', 'gmx.com', 'gmx.net'}
TAAL_NAAM_WOORDEN = {'engels', 'nederlands', 'duits', 'frans', 'spaans', 'italiaans', 'portugees', 'arabisch',
                     'turks', 'pools', 'russisch', 'chinees', 'english', 'dutch', 'german', 'french', 'spanish',
                     'italian', 'portuguese', 'arabic', 'turkish', 'polish', 'russian', 'chinese', 'hindi', 'urdu',
                     'vloeiend', 'fluent', 'moedertaal', 'native', 'goed', 'redelijk', 'basis', 'talen', 'languages'}
NAAM_STAART = {'cms', 'crm', 'erp', 'sap', 'fe', 'bd', 'ict', 'cv', 'nl', 'uk', 'java'}
TITLE_AFTER = {'msc', 'bsc', 'ba', 'ma', 'mba', 'phd', 'pmp', 'cissp', 'binftech', 'mtd', 'llm', 'bba', 'ra',
               'rc', 'nl', 'ing', 'ir', 'drs', 'cpa', 'cisa', 'cism', 'itil', 'prince2'}
LABEL_BEFORE = re.compile(r'(?i)^(?:macrobutton|mergefield|formtext|naam|name|full\s*name|volledige\s*naam|voornaam|roepnaam|naam\s*kandidaat|'
                          r'kandidaat|candidate|medewerker|consultant)\s*[:：=\-]?\s+')


def clean_person_name(name):
    """Maak van wat als naam is gevonden een echte naam, of geef '' als het geen naam is.
    'Naam Henk Winters' -> 'Henk Winters'; 'Geert van TuinenGeert van Tuinen' -> 'Geert van Tuinen';
    'Name Jan Smit Sex Male' -> 'Jan Smit'; 'Drs. Paul van Dorsten CISSP' -> 'Paul van Dorsten';
    'Persoonlijke Gegevens', 'Backend Developer', 'Voorbeeld Experts BV' -> ''."""
    s = ' '.join(str(name or '').split())
    if not s:
        return ''
    def undouble(t):
        half = len(t) // 2
        if len(t) % 2 == 0 and t[:half].casefold() == t[half:].casefold():
            return t[:half].strip()
        w = t.split()
        if len(w) >= 4 and len(w) % 2 == 0 and [x.casefold() for x in w[:len(w)//2]] == [x.casefold() for x in w[len(w)//2:]]:
            return ' '.join(w[:len(w)//2])
        return t
    # 'CV – Randy Vaessen', 'Curriculum Vitae: Jan Smit', 'Resume | Jan Smit': kopwoord ervoor eraf.
    s = re.sub(r'(?i)^(?:cv|c\.v\.|curriculum\s+vitae|resume|résumé)\s*(?:[-–—:|]|van|of)\s*(?=\S)', '', s).strip()
    # Word-veldcode aan de naam geplakt: 'MERGEFORMATtom Coenraad' -> 'tom Coenraad'.
    s = re.sub(r'(?i)^(?:mergeformat|macrobutton|mergefield|formtext)(?=[a-z])', '', s).strip()
    s = undouble(s)
    # 'Jan Smitjan Smitwerkervaringwerkervaring': naam dubbel + kopje erachter geplakt.
    m = re.match(r'(?is)^(.{5,40}?)\1(.*)$', s)
    if m:
        rest = undouble(m.group(2).strip())
        junk = not rest or normalized_name(rest) in NAME_BLOCKLIST or any(
            rx.fullmatch(rest) for rx in CV_SECTION_PATTERNS.values())
        s = m.group(1).strip() + ('' if junk else ' ' + rest)
    # Aan elkaar geplakt uit een tabel: 'Emre KaplanCurriculum Vitae', 'Ravi PrakashMohan'.
    s = re.sub(r'(?<=[a-zà-ÿ]{3})(?=[A-ZÀ-Ý][a-zà-ÿ]{2})', ' ', s)
    s = undouble(s)
    for _ in range(3):
        s = LABEL_BEFORE.sub('', s)
        s = TITLE_BEFORE.sub('', s)
    s = NEXT_FIELD.sub('', s).strip()
    words = s.split()
    while len(words) > 2 and words[-1].strip('.').lower() in TITLE_AFTER:
        words.pop()
    # Verschreven 'curriculum' ('Curicculum', 'Curiculum', 'Cirriculum'): geen naam.
    cur = [i for i, w in enumerate(words) if difflib.SequenceMatcher(None, asciifold(w).lower(), 'curriculum').ratio() >= .8]
    if cur:
        if cur[0] < 2:
            return ''
        words = words[:cur[0]]               # 'Emre Kaplan Curriculum Vitae' -> 'Emre Kaplan'
    if any(asciifold(w).lower().strip('.,') in COMPANY_WORDS for w in words):
        return ''
    # Rommel achter de naam afknippen: 'Marc de Gier Contractmanager Belastingdienst' -> 'Marc de Gier'.
    for i, w in enumerate(words):
        low = asciifold(w).lower().strip('.,')
        if i >= 2 and (low in NAME_BLOCKLIST or low.endswith(JOB_ENDINGS)):
            words = words[:i]
            break
    # Plaatsnaam erachter ('Marieke van OmmenZoetermeer'), maar niet 'Sander van Rijswijk'.
    if len(words) >= 3 and normalized_name(words[-1]) in NL_PLACES and normalized_name(words[-2]) not in NAME_FILLERS:
        words = words[:-1]
    # Alleen talen ('Engels Nederlands Duits') of nationaliteiten: geen naam.
    if words and all(asciifold(w).lower().strip('.,') in TAAL_NAAM_WOORDEN for w in words):
        return ''
    # Losse afkortingen/vakwoorden achteraan ('Ray Hyde Fe Bd', 'Ray Hyde Cms'), niet 'Jan de' / 'Li Na'.
    while len(words) > 2 and asciifold(words[-1]).lower().strip('.,') in NAAM_STAART \
            and normalized_name(words[-2]) not in NAME_FILLERS:
        words.pop()
    if words and words[0][:1].islower() and normalized_name(words[0]) not in NAME_FILLERS:
        words[0] = words[0][:1].upper() + words[0][1:]
    s = ' '.join(words).strip(' .,-')
    if normalized_name(s) in NL_PLACES or normalized_name(s).replace(' ', '') in {p.replace(' ', '') for p in NL_PLACES}:
        return ''                                   # 'Hendrik Ido Ambacht' is een plaats
    return s if reasonable_name(s) else ''


def name_with_email(name, emails):
    """Voorletters vervangen door de voornaam uit het eigen e-mailadres:
    'Ncp Kramer' / 'N.C.P. Kramer' + niels_kramer@… -> 'Niels Kramer'. Alleen als de achternaam
    ook in het adres staat en de voornaam met dezelfde letter begint."""
    words = (name or '').split()
    if len(words) < 2:
        return name
    first = words[0]
    letters = re.sub(r'[^A-Za-z]', '', first)
    initials = bool(re.fullmatch(r'(?:[A-Z]\.){1,4}', first)) or (
        2 <= len(letters) <= 4 and not re.search(r'[aeiouy]', letters.lower()))
    if not initials:
        return name
    surname = normalized_name(words[-1])
    return _initials_from_email(words, letters, surname, emails, name)


def _initials_from_email(words, letters, surname, emails, name):
    for e in emails or []:
        if GENERIC_EMAIL.match(e):
            continue
        parts = [x for x in re.split(r'[._\-]+', e.split('@')[0].lower()) if x.isalpha()]
        if surname in parts:
            given = [x for x in parts if x != surname and len(x) >= 3 and x[0] == letters[0].lower()]
            if given:
                return ' '.join([given[0].capitalize()] + words[1:])
    return name


def name_order_by_email(name, emails):
    """'Elmensdorp Henri Ernst Ronald' + ron.elmensdorp@… -> 'Henri Ernst Ronald Elmensdorp'."""
    words = (name or '').split()
    if len(words) < 2:
        return name
    for e in emails or []:
        if GENERIC_EMAIL.match(e):
            continue
        parts = [x for x in re.split(r'[._\-]+', e.split('@')[0].lower()) if x.isalpha()]
        first, last = normalized_name(words[0]), normalized_name(words[-1])
        rest = [normalized_name(w) for w in words[1:]]
        # De rest van het adres moet ook bij de voornaam/voornamen passen ('ron' bij Ronald, 'h' bij Henri);
        # 'gerk.dennis@' bij 'Dennis Andre' past niet -> niet omdraaien (liever laten staan dan fout).
        voornaam_past = any(p == r or (len(p) >= 3 and r.startswith(p)) or (len(p) == 1 and r.startswith(p))
                            for p in parts[:-1] for r in rest if r)
        if len(parts) >= 2 and parts[-1] == first and last not in parts and first not in NAME_FILLERS and voornaam_past:
            return ' '.join(words[1:] + [words[0]])
    return name


def email_matches_name(name, email):
    """Het e-mailadres bevat (een deel van) de naam: jan.devries@, jdevries@, vries84@."""
    local = re.sub(r'[^a-z]', '', (email or '').split('@')[0].lower())
    return any(len(w) >= 3 and w in local for w in normalized_name(name).split() if w not in NAME_FILLERS)


def email_owner_mismatch(name, email):
    """True als het e-mailadres duidelijk de naam van iemand anders bevat (bart.plasmeijer@ bij Dennis Kortekaas)."""
    if not name or not email or GENERIC_EMAIL.match(email):
        return False
    parts = [x for x in re.split(r'[._\-]+', email.split('@')[0].lower()) if x.isalpha()]
    if len(parts) < 2 or any(len(x) < 3 for x in parts[:2]):
        return False
    nwords = [w for w in normalized_name(name).split() if len(w) >= 3]
    hit = any(difflib.SequenceMatcher(None, p, w).ratio() >= .8 or p in w or w in p for p in parts for w in nwords)
    return not hit


BESTANDSNAAM_RUIS = {'cv', 'resume', 'curriculum', 'vitae', 'nl', 'en', 'eng', 'uk', 'final', 'versie', 'def',
                     'definitief', 'nieuw', 'new', 'updated', 'update', 'latest', 'kopie', 'copy', 'u', 'pdf', 'docx',
                     'doc', 'nederlands', 'engels', 'english', 'dutch', 'concept', 'v', 'vs', 'van', 'de', 'der', 'den',
                     'het', 'ter', 'ten', 'la', 'le', 'el', 'da', 'di', 'du', 'del', 'von', 'mei', 'jan', 'feb'}


def naam_uit_bestandsnaam(path_or_stem, snippet, zonder_tekst=False):
    """Haal een mogelijke persoonsnaam UITSLUITEND uit de eigen bestandsnaam.
    De bovenliggende mappen kunnen van recruiters of andere kandidaten zijn.
    Normaal moet de naam ook in de cv-tekst staan; zonder_tekst wordt alleen
    gebruikt na een aparte controle op sterke CV-kenmerken."""
    # PosixPath(...).stem snijdt op een Windows-pad de map niet af.
    basis = str(path_or_stem or '').split(ZIP_SEP)[-1].replace('\\', '/').rsplit('/', 1)[-1]
    stem = re.sub(r'(?i)\.(?:pdf|docx?|txt|rtf|odt|pages)$', '', basis)
    stem = re.sub(r'\([^)]*\)', ' ', stem)
    stem = re.sub(r'(?<=[a-z])(?=[A-Z])', ' ', stem)              # denBulck -> den Bulck
    stem = re.sub(r'\b[A-Za-z]{0,4}\d+[A-Za-z0-9]*\b', ' ', stem)        # HQ00000869, v2, 2024
    woorden = [w for w in re.split(r'[^A-Za-zÀ-ÿ]+', stem) if w]
    woorden = [w for w in woorden if not (w.isupper() and len(w) <= 3) or normalized_name(w) in NAME_FILLERS]
    # Na een Hoofdletter-naam: kleine woorden ('data governance') of HOOFDLETTER-codes ('SAPBO') zijn geen naam meer.
    for i, w in enumerate(woorden):
        eerder = [x for x in woorden[:i] if x[:1].isupper() and not x.isupper() and normalized_name(x) not in BESTANDSNAAM_RUIS]
        if len(eerder) >= 2 and normalized_name(w) not in NAME_FILLERS and (w.islower() or (w.isupper() and len(w) >= 4)):
            woorden = woorden[:i]
            break
    kern = [w for w in woorden if normalized_name(w) not in BESTANDSNAAM_RUIS]
    if not (2 <= len(kern) <= 4):
        return ''
    naam_w = [w for w in woorden if normalized_name(w) not in BESTANDSNAAM_RUIS or normalized_name(w) in NAME_FILLERS]
    # tussenvoegsels alleen houden als ze vlak voor een kernwoord staan
    while naam_w and normalized_name(naam_w[-1]) in NAME_FILLERS:
        naam_w.pop()
    kandidaat = ' '.join(naam_w)
    if (not reasonable_name(' '.join(w[:1].upper() + w[1:] for w in kern)) or any(asciifold(w).lower() in COMPANY_WORDS for w in kern)
            or not clean_person_name(' '.join(w[:1].upper() + w[1:] for w in kern))):
        return ''
    tekst = ' ' + (snippet or '')[:4000] + ' '
    gewone_match = all(f' {normalized_name(w)} ' in tekst for w in kern if len(normalized_name(w)) >= 2)
    # Bij een aan elkaar geplakte naam hoort het tussenvoegsel er ook bij:
    # 'marthijnvanrheenen' is 'Marthijn van Rheenen'.
    aaneen_match = bool(kern) and any(
        f' {vorm} ' in tekst for vorm in (
            ''.join(normalized_name(w) for w in kern),
            ''.join(normalized_name(w) for w in naam_w)))
    if not (gewone_match or aaneen_match or zonder_tekst):
        return ''
    # Volgorde zoals in het cv ('Tom den Bulck'), anders de bestandsnaam ('Bulck Tom' -> achternaam eerst).
    n = normalized_name(kandidaat).split()
    for k in range(1, len(n)):
        omgedraaid = n[k:] + n[:k]
        if ' ' + ' '.join(omgedraaid) + ' ' in tekst and ' ' + ' '.join(n) + ' ' not in tekst:
            ws = kandidaat.split()
            kandidaat = ' '.join(ws[k:] + ws[:k])
            break
    return tidy_name(' '.join(w if normalized_name(w) in NAME_FILLERS else w[:1].upper() + w[1:] for w in kandidaat.split()))


def naam_botst_met_bestand(name, path_or_stem, snippet):
    """Geeft de naam uit de bestandsnaam als die in het cv staat én niets gemeen heeft met de gevonden naam
    (bv. de naam van een recruiter bovenaan een bureau-cv), anders ''."""
    fn = naam_uit_bestandsnaam(path_or_stem, snippet)
    if not fn or not name:
        return ''
    a = {w for w in normalized_name(name).split() if w not in NAME_FILLERS}
    b = {w for w in normalized_name(fn).split() if w not in NAME_FILLERS}
    return fn if a and b and not (a & b) else ''


def naam_aanvullen(name, emails, snippet):
    """Achternaam opnieuw controleren: 'Botwin Wytze' met botwin.sijtsma@… en in het cv 'Botwin Wytze Sijtsma'
    -> 'Botwin Wytze Sijtsma'. Alleen als de achternaam uit het EIGEN mailadres in het cv direct na de naam staat
    (de scanner had dan een tweede voornaam als achternaam gelezen). Anders blijft de naam zoals hij was."""
    words = normalized_name(name).split()
    if len(words) < 2 or not snippet:
        return name
    tekst = ' ' + snippet + ' '
    for e in emails or []:
        if GENERIC_EMAIL.match(e) or e.split('@')[-1].lower() not in PRIVE_MAILDOMEINEN:
            continue
        delen = [x for x in re.split(r'[._\-0-9]+', e.split('@')[0].lower()) if len(x) >= 3]
        if len(delen) < 2 or delen[0] != words[0] or delen[-1] in words:
            continue
        achter = delen[-1]
        # Staat 'voornaam … achternaam' (max. 2 woorden ertussen, zoals tussenvoegsels) letterlijk in het cv?
        toks = tekst.split()
        n = len(words)
        for i in range(len(toks) - n):
            if toks[i:i + n] != words:
                continue
            # Direct erna: tussenvoegsels + achternaam ('de vries' = devries in het mailadres).
            for k in range(1, 5):
                stuk = toks[i + n:i + n + k]
                if len(stuk) == k and ''.join(stuk) == achter and all(x in NAME_FILLERS for x in stuk[:-1]):
                    return tidy_name(name + ' ' + ' '.join(x if x in NAME_FILLERS else x.title() for x in stuk))
    return name


def guess_name(text, filename, emails, links=()):
    name = name_with_email(tidy_name(clean_person_name(_guess_name(text, filename, emails, links))), emails)
    name = naam_aanvullen(name, emails, normalized_snippet(text))
    beter = naam_botst_met_bestand(name, filename, normalized_snippet(text))
    if beter and not any(email_matches_name(name, e) for e in emails if not GENERIC_EMAIL.match(e)):
        return beter          # bureau-cv: bovenaan staat de recruiter, de kandidaat staat in de bestandsnaam
    return name


def _guess_name(text, filename, emails, links=()):
    lines = [' '.join(line.split()) for line in text.splitlines()[:35]]
    explicit = clean_person_name(name_parts(text))
    if explicit:
        return explicit
    # Een naam in de CV-kop is mogelijk; een willekeurig later kopje niet.
    # Meerdere kandidaten (bv. 'Jak Verhuur' en 'Lisa Groen')? Dan de regel die bij het eigen e-mailadres past.
    top = []
    for line in lines[:7]:
        # Een cv kan een kop met functietermen hebben zoals
        # 'SCOM OMS Consultant profiel en opdrachten'. Het oude afknippen
        # tot 'Scom Oms' leek op een persoonsnaam, maar is een acroniem.
        # Alleen de echte bestandsnaam wordt later eventueel gebruikt.
        raw_words = line.split()
        if (len(raw_words) >= 4 and len(raw_words[0]) <= 6 and len(raw_words[1]) <= 6
                and raw_words[0].isupper() and raw_words[1].isupper()
                and any(re.search(r'(?i)\b' + re.escape(term) + r'\b', line)
                        for term in ('consultant', 'developer', 'engineer', 'profiel',
                                     'profile', 'werkervaring', 'opdrachten', 'skills'))):
            continue
        line = clean_person_name(line)
        if not line:
            continue
        core = [w for w in line.split() if normalized_name(w) not in NAME_FILLERS]
        if len(core) >= 2 and all(w[0].isupper() for w in core if w):
            top.append(line)
    if top:
        own = [e for e in emails if not GENERIC_EMAIL.match(e)]
        for line in top:
            if any(email_matches_name(line, e) for e in own):
                return line
        return top[0]
    # LinkedIn-adres is betrouwbaarder dan de bestandsnaam.
    for link in links:
        from_link = name_from_linkedin(link)
        if from_link:
            return from_link
    # 'Ik ben Salomo Petrus, …' / 'Mijn naam is …' / 'My name is …' in de eerste regels.
    m = re.search(r'(?:\b[Ii]k ben|\b[Mm]ijn naam is|\b[Mm]y name is|\bI am|\bI\'m)\s+'
                  r'([A-ZÀ-Ý][a-zà-ÿ]+(?:\s+(?:van|de|der|den|el|la|ten|ter|van der|van den)?\s*[A-ZÀ-Ý][a-zà-ÿ\-]+){1,3})',
                  '\n'.join(lines[:20]))
    gewone = set().union(*TAAL_WOORDEN.values()) | {'en', 'and', 'gedreven', 'flexibel', 'enthousiast', 'ervaren',
                                                     'motivated', 'passionate', 'experienced', 'senior', 'junior',
                                                     'zelfstandig', 'creatief', 'creative', 'professional', 'certified'}
    if (m and clean_person_name(m.group(1)) and reasonable_name(m.group(1))
            and not any(w.lower() in gewone for w in m.group(1).split())):
        return clean_person_name(m.group(1))
    # Naam in de bestandsnaam die ook in het cv staat ('Boin Hans HQ00003041 (U)', '2025-04-08 CV Frank Jansen (1)').
    uit_bestand = naam_uit_bestandsnaam(filename, normalized_snippet(text))
    if uit_bestand:
        return uit_bestand
    # Sommige Word/PDF-lezers missen de zichtbare naam, maar in de eigen
    # bestandsnaam staat bijv. 'Marthijn_vanRheenen-SCOM_OMS_Consultant'.
    # Probeer dit pas na de normale naamherkenning in de cv-tekst, en NOOIT
    # met een naam afkomstig uit bovenliggende mappen (bijv. 'Micha').
    if len(text.strip()) >= 60:
        h, hdr, cv_file, non_cv, enormous, periods = cv_signals(text, filename)
        twee_inhoudelijke_kopjes = (
            ('opleiding' in h and bool(h & {'werkervaring', 'profiel', 'vaardigheden'}))
            or ('werkervaring' in h and bool(h & {'profiel', 'vaardigheden'})))
        contact = personal_contact(emails, phones_in(text), links)
        context = hdr or cv_file or contact or (len(h) >= 3 and len(periods) >= 2)
        if (twee_inhoudelijke_kopjes and context and not non_cv and not enormous
                and not is_template(text) and not is_vacancy(text)
                and not is_school_doc(text, filename, strong_cv=bool(
                    'werkervaring' in h and 'opleiding' in h and contact))):
            kandidaat = naam_uit_bestandsnaam(filename, '', zonder_tekst=True)
            # Een e-mailadres van aantoonbaar iemand anders kan een bureaureferentie
            # zijn: neem dan niet blind de naam uit de bestandsnaam over.
            if kandidaat and not any(email_owner_mismatch(kandidaat, e) for e in emails
                                     if not GENERIC_EMAIL.match(e)):
                return kandidaat
    candidate = re.sub(r'(?i)^(cv|resume|curriculum[ _-]*vitae)[ _-]+', '', filename)
    if candidate != filename:
        candidate = re.sub(r'(?i)[ _-]+(?:final|versie|nieuw|new|updated|definitief|lang|kort|nl|en|eng|uk)\b.*$', '', candidate)
        candidate = re.sub(r'[_-]+', ' ', candidate).strip()
        candidate = re.sub(r'(?<=[a-z]) [A-Z]{4,}$', '', candidate)      # 'Ting Kung SAPBO' -> 'Ting Kung'
        if reasonable_name(candidate):
            return candidate.title()
    for email in emails:
        local = email.split('@',1)[0]
        if re.fullmatch(r'[a-zA-ZÀ-ÿ]{2,}[._-][a-zA-ZÀ-ÿ]{2,}', local):
            candidate = re.sub(r'[._-]', ' ', local)
            if reasonable_name(candidate):
                return candidate.title()
    return ''

def _fix_email(e):
    """'1330info@bedrijf.comwww.bedrijf.com' (aan elkaar geplakt in een pdf) -> 'info@bedrijf.com'."""
    e = e.strip('.,:;').lower()
    # 'haage-mailadresinfo@…' / 'e-mailjan@…': label (en wat ervoor zat) eraf.
    e = re.sub(r'^(?:.*?(?:e-mail(?:address|adres)?|email(?:address|adres)|mailadres|mail:)\d?|.+?email\d?)(?=[a-z0-9])',
               '', e.split('@')[0]) + '@' + e.split('@', 1)[1] \
        if re.search(r'(?:e-?mail(?:adres)?|email(?:address)?|mailadres)[a-z0-9]', e.split('@')[0]) else e
    # '…@x.nlgeboortedatum', '…@x.comtelefoon': woord achter het domein (zonder punt erna) eraf.
    e = re.sub(r'(\.(?:com|nl|org|net|eu|be|de|io|info|biz|uk|nu|me|fr|it|es))[a-z]{3,}$', r'\1', e)
    e = re.sub(r'(\.(?:com|nl|org|net|eu|be|de|io|info|biz|uk|nu|me))(?:www\.|https?|linkedin|tel|mob|phone|\d).*$', r'\1', e)
    local, _, dom = e.partition('@')
    stripped = re.sub(r'^\d{3,}', '', local)
    if stripped != local and (GENERIC_EMAIL.match(stripped + '@') or (re.match(r'^\d{5,}', local) and len(stripped) >= 3)):
        e = stripped + '@' + dom          # postcode/huisnummer/telefoon ervoor geplakt
    return e


def emails_in(text):
    return sorted(set(_fix_email(m.group(0)) for m in EMAIL_RE.finditer(text)))[:12]


def norm_phone(raw):
    raw = raw.replace('(0)', ' ')  # '+31 (0)6 ...' -> '+31 6 ...'
    d = re.sub(r'\D', '', raw)
    if d.startswith('00'):
        d = d[2:]
    if raw.strip().startswith('+') or re.sub(r'\D','',raw).startswith('00'):
        if d.startswith('310') and len(d) == 12:
            d = '31' + d[3:]            # '+31 06 12345678' -> '+31 6 12345678'
        if d.startswith('31') and len(d) != 11:
            return ''                   # Nederlands nummer heeft altijd 9 cijfers na +31
        if not 10 <= len(d) <= 15:
            return ''
        return '+' + d
    if not (10 <= len(d) <= 13):
        return ''
    if d.startswith('06') and len(d) == 10:
        return '+31' + d[1:]
    if d.startswith('0') and len(d) == 10:
        return '+31' + d[1:]
    return ''


def phones_in(text):
    out = []
    for line in text.splitlines():
        # '0031 (0)6 …' / '31 06 …' / '316…' zonder plus: eerst netjes als '+31 6 …', dan pas herkennen.
        line = re.sub(r'(?<![\d+])00[\s.\-]*31(?=[\s.\-(]*\d)', '+31', line)
        line = re.sub(r'\(\s*0\s*\)', ' ', line)
        line = fix_nl_mobile(line)
        for match in PHONE_RE.finditer(line):
            raw = match.group(0)
            # Voorkom het verwarren van getallen, jaartallen en datums met telefoonnummers.
            if not (raw.lstrip().startswith('+') or re.sub(r'\D','',raw).startswith(('00','0'))):
                continue
            p = norm_phone(raw)
            if p and p not in out:
                out.append(p)
    return out[:10]


WEB_RE = re.compile(r'(?<![@\w.-])(?:https?://)?(?:www\.)?((?:[a-z0-9-]+\.)+(?:nl|com|dev|io|me|net|org|eu|app|site|tech|be|online|digital|codes|page))(/[^\s,;)<>"\']*)?', re.I)
SKIP_SITES = ('linkedin.com', 'facebook.com', 'instagram.com', 'tiktok.com', 'twitter.com', 'x.com',
              'youtube.com', 'google.', 'microsoft.com', 'wikipedia.org', 'apple.com', 'gmail.com',
              'outlook.com', 'hotmail.com', 'live.nl', 'live.com', 'icloud.com', 'yahoo.')


def websites_in(text):
    """Eigen websites / GitHub / portfolio-links die de kandidaat zelf in het cv zette."""
    out = []
    for m in WEB_RE.finditer(text[:60000]):
        host = m.group(1).lower()
        path = (m.group(2) or '').rstrip('.,/')
        if any(skip in host for skip in SKIP_SITES):
            continue
        if host.count('.') == 0 or re.fullmatch(r'[\d.]+', host):
            continue
        url = 'https://' + host + path
        if url not in out:
            out.append(url)
    return out[:8]


CURRENT_JOB = re.compile(r'(?i)(?:19|20)\d\d\s*(?:[–—-]|tot|to)\s*(?:heden|present|nu|now|current)\b[\s:|·–—-]*(.{3,90})')


def current_job(text):
    """'2021 – heden  Functioneel beheerder, Gemeente X' -> ('Functioneel beheerder', 'Gemeente X').
    Werkt ook als de functie op de regel ONDER de periode staat, en kijkt eerst onder
    het kopje Werkervaring (zodat een lopende opleiding niet als functie telt)."""
    lines = [' '.join(l.split()) for l in text.splitlines()[:300]]
    start = next((k for k, l in enumerate(lines)
                  if CV_SECTION_PATTERNS['werkervaring'].fullmatch(l.strip(' :•*-\t'))), None)
    order = (list(range(start, len(lines))) + list(range(0, start))) if start is not None else range(len(lines))
    for k in order:
        line = lines[k]
        m = CURRENT_JOB.search(line + ' ')
        present = re.search(r'(?i)(?:19|20)\d\d\s*(?:[–—-]|tot|to)\s*(?:heden|present|nu|now|current)\b', line)
        if not present:
            continue
        rest = line[present.end():].strip(' .:|·–—-')
        if len(rest) < 3:   # functie staat op de volgende regel(s)
            nxt = [l for l in lines[k+1:k+4] if l.strip()]
            if not nxt or re.search(r'(?i)^(hbo|wo|mbo|vwo|havo|bachelor|master|opleiding)', nxt[0]):
                continue
            first = re.split(r'\s*(?:,| bij | at | - | – | \| | · )\s*', nxt[0].strip(' .:'), maxsplit=1)
            role = first[0][:60]
            employer = (first[1][:60] if len(first) > 1 else
                        nxt[1].strip(' .:')[:60] if len(nxt) > 1 and len(nxt[1]) <= 60 and not re.search(r'\d{4}', nxt[1]) else '')
        else:
            parts = re.split(r'\s*(?:,| bij | at | - | – | \| | · )\s*', rest, maxsplit=1)
            role = parts[0].strip()[:60]
            employer = parts[1].strip()[:60] if len(parts) > 1 else ''
        if role and not re.search(r'\d{4}', role) and not role.startswith('▸'):
            return role, employer
    return '', ''


STAGE_LINE = re.compile(r'(?i)\b(?:afstudeer)?stag(?:e|iair|iaire|eplek)\b|\bintern(?:ship)?\b')


def stage_from(text):
    """Alle stagebedrijven uit het cv, gescheiden door ' ; ' (iemand kan meerdere stages hebben gelopen).
    Bv. 'Stage Data Analyst bij Solutionnow' -> 'Solutionnow'."""
    found = []
    for company in _stages_iter(text):
        if normalized_name(company) not in [normalized_name(f) for f in found]:
            found.append(company)
    return ' ; '.join(found[:4])


LEVERANCIER_LABEL = re.compile(r'(?i)^(?:via|aangeboden door|aangeboden via|namens|leverancier|bemiddeld door|'
                               r'ingediend door|voorgesteld door|supplier|agency|bureau)\s*[:\-]?\s*(.{2,60})$')
LEGAL_SUFFIX = re.compile(r'(?i)(?:^|\s)(?:b\.?v\.?|bvba|n\.?v\.?|v\.?o\.?f\.?|ltd\.?|gmbh|holding)(?=\s|$|[.,])')


def leverancier_from(text):
    """Bedrijf bovenaan het cv (detacheerder, bureau of eigen BV): 'Via: Detachering Voorbeeld',
    'Voorbeeld Experts BV'. Alleen in de kop (vóór het eerste cv-kopje), niet uit de werkervaring."""
    lines = [' '.join(l.split()) for l in (text or '').splitlines()[:15] if l.strip()]
    for line in lines[:12]:
        clean = line.strip(' :•*-\t')
        if any(rx.fullmatch(clean) for rx in CV_SECTION_PATTERNS.values()):
            break                                   # werkervaring e.d. begint: daar staan werkgevers, geen leverancier
        if re.search(r'\b(?:19|20)\d{2}\b|@|https?://|www\.', line):
            continue
        m = LEVERANCIER_LABEL.match(clean)
        if m:
            return 'via:' + m.group(1).strip(' .,')      # 'Via: …' = bureau/leverancier
        words = clean.split()
        if 1 <= len(words) <= 6 and (LEGAL_SUFFIX.search(clean) or any(
                asciifold(w).lower().strip('.,') in COMPANY_WORDS for w in words)):
            if not re.search(r'(?i)\b(?:cv|curriculum|vitae|profiel|resume)\b', clean):
                return clean.strip(' .,')
    return ''


def bedrijf_soort(bedrijf_raw, name, emails, shared=1):
    """('Jansen IT BV', 'eigen bedrijf (zzp)') / ('Detachering X', 'bureau / leverancier') / (…, 'onbekend')."""
    if not bedrijf_raw:
        return '', ''
    via = bedrijf_raw.startswith('via:')
    bedrijf = bedrijf_raw[4:] if via else bedrijf_raw
    if via or shared >= 3:
        return bedrijf, 'bureau / leverancier'
    nb = re.sub(r'[^a-z]', '', asciifold(bedrijf).lower())
    nwords = [w for w in normalized_name(name).split() if len(w) >= 3 and w not in NAME_FILLERS]
    if any(w in nb for w in nwords):
        return bedrijf, 'eigen bedrijf (zzp)'           # 'Jansen IT BV' bij Jan Jansen
    core = [w for w in re.findall(r'[a-z]{3,}', asciifold(bedrijf).lower())
            if w not in COMPANY_WORDS and w not in ('bv', 'nv', 'vof', 'the')]
    freemail = ('gmail', 'hotmail', 'outlook', 'live', 'icloud', 'yahoo', 'kpnmail', 'ziggo', 'planet', 'xs4all',
                'casema', 'home', 'msn', 'me', 'mail', 'online', 'chello', 'hetnet', 'zonnet', 'tele2', 'upcmail')
    for e in emails or []:
        local, _, dom = e.partition('@')
        base = dom.rsplit('.', 1)[0].split('.')[-1]
        if base in freemail or any(base.startswith(f) for f in ('gmail', 'hotmail', 'outlook', 'yahoo')):
            continue
        dom = re.sub(r'[^a-z]', '', base)
        # Domein = de bedrijfsnaam zelf ('codebloem.nl' bij Codebloem BV, 'jansen-it.nl' bij Jansen IT BV).
        joined = ''.join(w for w in re.findall(r'[a-z]+', asciifold(bedrijf).lower())
                         if w not in ('bv', 'b', 'v', 'nv', 'vof', 'bvba', 'ltd', 'gmbh', 'holding'))
        same = len(dom) >= 4 and (dom == joined or (dom in joined and len(dom) >= len(joined) * .5)
                                  or (joined in dom and len(joined) >= len(dom) * .7))
        if core and same and (email_matches_name(name, e) or len(local) <= 4):
            return bedrijf, 'eigen bedrijf (zzp)'       # jan@jansen-it.nl bij 'Jansen IT BV'
    return bedrijf, 'onbekend'


# Taalherkenning van het cv. Kijkt naar de ZINNEN (werkervaring, opleiding, vaardigheden): woorden met een
# hoofdletter (namen, plaatsen, bedrijven, scholen) en woorden met cijfers tellen niet mee. Per zin wordt de
# taal bepaald met kleine woordjes die alleen in die taal voorkomen; de taal met de meeste tekst is de hoofdtaal.
# Gedeelde woordjes ('de', 'en', 'in', 'op', 'a', 'o', 'la', 'no', 'son', 'he', 'era') tellen bewust niet.
TAAL_WOORDEN = {
    'es': {'el', 'los', 'las', 'del', 'con', 'para', 'por', 'una', 'que', 'como', 'sus', 'pero', 'muy', 'mas',
           'más', 'esta', 'este', 'estos', 'entre', 'sobre', 'desde', 'hasta', 'durante', 'tambien', 'también',
           'donde', 'cuando', 'mediante', 'segun', 'según', 'fue', 'está', 'están', 'estan', 'ser', 'hacer',
           'realizar', 'realizado', 'experiencia', 'formacion', 'formación', 'conocimientos', 'habilidades',
           'responsable', 'gestion', 'gestión', 'desarrollo', 'trabajo', 'idiomas', 'nivel', 'actualidad',
           'funciones', 'equipo', 'clientes', 'datos', 'empresa', 'proyectos', 'años', 'anos', 'y'},
    'nl': {'het', 'een', 'met', 'voor', 'naar', 'bij', 'van', 'zijn', 'wordt', 'worden', 'heb', 'heeft', 'hebben',
           'ook', 'niet', 'deze', 'dit', 'dat', 'die', 'waar', 'wat', 'als', 'door', 'onder', 'tussen', 'binnen',
           'op', 'werkervaring', 'opleiding', 'vaardigheden', 'heden', 'jaar', 'ervaring', 'kennis',
           'verantwoordelijk', 'werkzaamheden', 'ontwikkeling', 'ik', 'wij', 'mijn', 'onze', 'gewerkt', 'werk'},
    'en': {'the', 'and', 'with', 'for', 'of', 'to', 'is', 'are', 'was', 'were', 'have', 'has', 'been', 'this',
           'that', 'which', 'from', 'at', 'by', 'an', 'as', 'our', 'my', 'experience', 'education', 'skills',
           'responsible', 'development', 'team', 'present', 'years', 'working', 'worked', 'including', 'within',
           'managed', 'developed', 'using'},
    'hi': {'hai', 'hain', 'aur', 'ka', 'ki', 'ke', 'mein', 'se', 'ko', 'nahi', 'nahin', 'bhi', 'tha', 'thi',
           'kya', 'yeh', 'woh', 'liye', 'ek', 'kiya', 'karna', 'karte'},
}
# Indiase schriften: Devanagari, Bengaals, Gurmukhi, Gujarati, Oriya, Tamil, Telugu, Kannada, Malayalam.
INDIC_RE = re.compile(r'[\u0900-\u0D7F]')


def cv_hoofdtaal(text):
    """Hoofdtaal van het cv: 'nl', 'en', 'es', 'indiaas', 'anders', 'onduidelijk' of '' (te weinig tekst).
    Werkt op de originele tekst (dan tellen woorden met een hoofdletter niet mee) en op de opgeslagen
    tekst (kleine letters; dan in blokjes van 12 woorden)."""
    text = (text or '')[:30000]
    # Klinkertekens in Indiase schriften zijn geen 'letter' voor Python: die tellen hier wel mee.
    letters = len(re.findall(r'[^\W\d_]|[\u0900-\u0D7F]', text))
    if letters >= 120:
        indic = len(INDIC_RE.findall(text))
        if indic >= .3 * letters:
            return 'indiaas'
        if indic >= .1 * letters:
            return 'onduidelijk'
    has_case = any(ch.isupper() for ch in text[:3000])
    if has_case:
        stukken = re.split(r'[\n.;:!?•|]+', text)
    else:
        w = text.split()
        stukken = [' '.join(w[i:i + 12]) for i in range(0, len(w), 12)]
    gewicht = collections.Counter()
    for stuk in stukken:
        words = re.findall(r"[^\W\d_][^\W\d_'’]*", stuk)
        if has_case:
            # Namen, plaatsen, bedrijven en scholen (met hoofdletter) tellen niet voor de taal.
            words = [w for w in words if not w[0].isupper()]
        words = [w.lower() for w in words]
        if len(words) < 4:
            continue
        tel = {t: sum(1 for w in words if w in ws) for t, ws in TAAL_WOORDEN.items()}
        order = sorted(tel.items(), key=lambda kv: kv[1], reverse=True)
        (best, n1), (_t2, n2) = order[0], order[1]
        if n1 >= 2 and n1 >= 2 * max(n2, 1) - (1 if n2 == 0 else 0):
            gewicht[best] += len(words)
    totaal = sum(gewicht.values())
    if totaal < (15 if has_case else 25):
        return ''
    best, n = gewicht.most_common(1)[0]
    if n < .6 * totaal:
        return 'onduidelijk'
    return {'hi': 'indiaas'}.get(best, best)


def cv_taal(text):
    """Oude naam (wordt elders gebruikt): 'es' / 'nl' / 'en' / ''."""
    t = cv_hoofdtaal(text)
    return t if t in ('es', 'nl', 'en') else ''


# Wens opdrachtgever: kandidaten moeten in Nederland of België wonen. Wie aantoonbaar ergens anders woont,
# telt niet als kandidaat ('niet meegenomen'). Gebaseerd op woonplaats/telefoon in de kop, nooit op naam, afkomst of cv-taal.
INDIA_PLACES = {'india', 'bangalore', 'bengaluru', 'mumbai', 'bombay', 'delhi', 'new delhi', 'chennai', 'madras', 'pune',
                'hyderabad', 'kolkata', 'calcutta', 'noida', 'gurgaon', 'gurugram', 'ahmedabad', 'kochi', 'cochin',
                'coimbatore', 'trivandrum', 'thiruvananthapuram', 'chandigarh', 'jaipur', 'indore', 'nagpur', 'mysore',
                'mysuru', 'visakhapatnam', 'vizag', 'bhubaneswar', 'lucknow', 'mangalore', 'karnataka', 'maharashtra',
                'tamil nadu', 'telangana', 'kerala', 'andhra pradesh', 'uttar pradesh', 'west bengal', 'gujarat',
                'haryana', 'rajasthan', 'punjab', 'odisha'}
BE_PLACES = {'belgie', 'belgium', 'belgique', 'belgien', 'antwerpen', 'antwerp', 'brussel', 'brussels', 'bruxelles',
             'gent', 'ghent', 'leuven', 'brugge', 'bruges', 'mechelen', 'hasselt', 'genk', 'luik', 'liege', 'namen',
             'namur', 'charleroi', 'kortrijk', 'aalst', 'oostende', 'ostend', 'sint niklaas', 'turnhout', 'roeselare',
             'mons', 'bergen', 'lier', 'zaventem', 'vilvoorde', 'dendermonde', 'beveren', 'geel', 'vlaanderen',
             'flanders', 'wallonie', 'limburg'}
def _woorden_en_cijfers(t):
    """Kleine letters zonder accenten, cijfers BLIJVEN (jaartallen, postcodes)."""
    return ' '.join(re.findall(r'[a-z0-9]+', asciifold((t or '').lower())))


BUITENLAND_IGNORE = {'nederland', 'netherlands', 'holland', 'the netherlands', 'remote', 'worldwide', 'emea',
                     'benelux', 'europe', 'europa', 'us'}


# Woorden waarna een plaats NIET de huidige woonplaats is: geboorteplaats, nationaliteit, oude woonplaats,
# verhuiswens, werkgever ('at Infosys Bangalore') of school. Die plaatsen tellen niet mee voor de woonplaats.
_NIET_WOON_MARK = (r'born in|born at|born|place of birth|birthplace|date and place of birth|geboren te|geboren in|geboren|'
    r'geboorteplaats|geboortedatum en plaats|geboortedatum|date of birth|nationality|nationaliteit|nationalite|'
    r'citizenship|citizen of|staatsburgerschap|previously lived in|previously based in|formerly|voorheen|vroeger|'
    r'eerder woonachtig|willing to relocate to|relocating to|relocate to|open to relocation to|moving to|'
    r'verhuist naar|verhuis naar|verhuizen naar|visa for|werkvergunning|work permit for|at|bij|for|voor|'
    r'university of|universiteit van|college of|institute of|school of')
# Na deze woorden is een plaats NIET de huidige woonplaats (geboorteplaats, nationaliteit, verhuiswens, werkgever,
# school). Maximaal 3 woorden worden overgeslagen, maar nooit over een volgend kenmerk heen.
NIET_WOONPLAATS_RE = re.compile(r' (?:' + _NIET_WOON_MARK + r') ((?:(?!(?:' + _NIET_WOON_MARK + r'|adres|address|'
                                r'woonplaats|location|residence) )[a-z]+ ){1,3})')
# Werk- of studieperiode in de kop ('2014 2019 Infosys Pune'): die plaats is werk/school, geen woonplaats.
PERIODE_IN_KOP_RE = re.compile(r' (?:19|20)\d\d (?:(?:19|20)\d\d|heden|present|now|nu|today|current) (?:[a-z]+ ){1,6}')
FOREIGN_LANDEN = {'afghanistan', 'africa', 'andhra pradesh', 'argentina', 'aruba', 'asia', 'australia', 'australie',
                  'austria', 'azie', 'bangladesh', 'brazil', 'brazilie', 'bulgaria', 'bulgarije', 'canada', 'china',
                  'croatia', 'curacao', 'czech republic', 'denemarken', 'denmark', 'duitsland', 'egypt', 'egypte',
                  'engeland', 'england', 'filipijnen', 'finland', 'france', 'frankrijk', 'germany', 'ghana', 'greece',
                  'griekenland', 'gujarat', 'haryana', 'hongarije', 'hungary', 'ierland', 'india', 'indonesia',
                  'indonesie', 'irak', 'iran', 'iraq', 'ireland', 'israel', 'italie', 'italy', 'japan', 'karnataka',
                  'kenya', 'kerala', 'kroatie', 'maharashtra', 'malaysia', 'maleisie', 'marokko', 'mexico', 'morocco',
                  'nepal', 'new york', 'nigeria', 'noorwegen', 'norway', 'odisha', 'oekraine', 'oostenrijk', 'pakistan',
                  'philippines', 'poland', 'polen', 'portugal', 'punjab', 'rajasthan', 'roemenie', 'romania', 'rusland',
                  'russia', 'saudi arabia', 'scotland', 'serbia', 'servie', 'south africa', 'spain', 'spanje',
                  'sri lanka', 'suriname', 'sweden', 'switzerland', 'syria', 'syrie', 'tamil nadu', 'telangana',
                  'turkey', 'turkije', 'uae', 'uk', 'ukraine', 'united kingdom', 'united states', 'usa', 'uttar pradesh',
                  'vietnam', 'west bengal', 'zweden', 'zwitserland', 'norge', 'deutschland', 'espana', 'ny',
                  'new jersey', 'california', 'texas', 'ontario', 'gauteng', 'hordaland', 'vestland'}


# Adreslabels. 'huidig' / 'oud' zegt welk adres actueel is; zonder aanduiding = neutraal.
ADRES_LABEL_RE = re.compile(
    r' (?:(?P<huidig>huidig adres|huidige adres|huidige woonplaats|current address|present address|current location|'
    r'current residence|correspondentieadres|woonadres)|'
    r'(?P<oud>vorig adres|vorige adres|oud adres|oude adres|previous address|former address|old address|'
    r'vorige woonplaats|previous residence)|'
    r'(?P<neutraal>adres|address|woonplaats|woonachtig te|woonachtig in|wonende te|residence|residing in|resides in|'
    r'lives in|living in|based in|location|locatie|plaats|domicilio|residencia|permanent address)) ')
NL_LAND = {'nederland', 'netherlands', 'the netherlands', 'holland', 'pays bas', 'niederlande'}
BE_LAND = {'belgie', 'belgium', 'belgique', 'belgien'}
# Plaatsnamen die ook buiten NL/BE bestaan (Bergen in Noorwegen, Amsterdam NY, Utrecht in Zuid-Afrika ...).
DUBBELE_PLAATSEN = {'bergen', 'limburg', 'amsterdam', 'rotterdam', 'utrecht', 'groningen', 'middelburg', 'hoorn',
                    'zwolle', 'leiden', 'holland', 'namen', 'luik', 'gent', 'hasselt', 'geel', 'lier', 'mons'}
NU_RE = r'(?:heden|present|nu|now|current|today|huidig)'


def _kop(snippet):
    """Begin van het cv (tot het eerste cv-kopje), zonder plaatsen die niet de woonplaats zijn."""
    head = ' ' + _woorden_en_cijfers((snippet or '')[:450]) + ' '
    m = re.search(r' (?:werkervaring|ervaring|experience|professional experience|work experience|profiel|profile|'
                  r'samenvatting|summary|opleiding|opleidingen|education|vaardigheden|skills|loopbaan|career|'
                  r'experiencia|formacion|over mij|about me|opdrachten|projecten|projects) ', head)
    if m and m.start() > 10:
        head = head[:m.start()] + ' '
    head = PERIODE_IN_KOP_RE.sub(' ', head)
    return NIET_WOONPLAATS_RE.sub(' ', NIET_WOONPLAATS_RE.sub(' ', head))


def _plaats_in(text, places):
    return next((pl for pl in sorted(places, key=len, reverse=True) if f' {pl} ' in text), '')


def _ocr_cijfers(tok):
    """Duidelijke OCR-fouten in een postcode: o->0, l/i->1. Alleen als minstens 2 tekens al echte cijfers zijn."""
    if sum(ch.isdigit() for ch in tok) < 2:
        return ''
    t = tok.translate(str.maketrans({'o': '0', 'l': '1', 'i': '1'}))
    return t if re.fullmatch(r'[1-9]\d{3}', t) else ''


def _landen_in(seg):
    """Welke landen noemt dit stukje adres? Geeft (lijst van ('nl'|'be'|'buiten', plaats, sterk)).
    Een plaats gevolgd door een land/regio (Bergen Norway, Amsterdam New York) hoort bij dat land.
    Postcode + plaats moeten bij elkaar passen; een postcode alleen is maar een aanwijzing."""
    nl_be = NL_PLACES | BE_PLACES
    buiten = (FOREIGN_PLACES | INDIA_PLACES) - BUITENLAND_IGNORE - BE_PLACES
    alle = sorted(nl_be | buiten | NL_LAND | BE_LAND | FOREIGN_LANDEN, key=len, reverse=True)
    words = seg.split()
    hits, i = [], 0
    while i < len(words):
        for pl in alle:
            n = pl.count(' ') + 1
            if ' '.join(words[i:i + n]) == pl:
                hits.append((i, i + n, pl))
                i += n - 1
                break
        i += 1
    uit = []
    gebruikt = set()
    for k, (a, b, pl) in enumerate(hits):
        if k in gebruikt:
            continue
        # land/regio direct erna (max 4 woorden verder)?
        volg = next(((k2, h) for k2, h in enumerate(hits) if k2 > k and 0 <= h[0] - b <= 4), None)
        if pl in NL_LAND or pl in BE_LAND:
            uit.append(('nl' if pl in NL_LAND else 'be', pl, True))
            continue
        land = 'nl' if pl in NL_PLACES else 'be' if pl in BE_PLACES else 'buiten'
        if volg:
            q = volg[1][2]
            qland = 'nl' if q in NL_LAND or q in NL_PLACES else 'be' if q in BE_LAND or q in BE_PLACES else 'buiten'
            if qland == 'buiten' and land in ('nl', 'be') and q in FOREIGN_LANDEN:
                # 'Bergen, Norway' / 'Amsterdam, NY': het land erachter beslist. Bij een gewone NL-plaats met een
                # buitenlands land erachter is het onduidelijk -> AI.
                land = 'buiten' if pl in DUBBELE_PLAATSEN else 'onzeker'
                gebruikt.add(volg[0])
            elif q in NL_LAND | BE_LAND and qland == land:
                gebruikt.add(volg[0])
        # postcode vlak ervoor?
        voor = words[max(0, a - 2):a]
        pc = ''
        if len(voor) == 2 and re.fullmatch(r'[a-z]{2}', voor[1]) and _ocr_cijfers(voor[0]):
            pc = 'nl'                                    # 1234 AB Plaats
        elif voor and re.fullmatch(r'\d{4}[a-z]{2}', voor[-1]) and _ocr_cijfers(voor[-1][:4]):
            pc = 'nl'                                    # 1234AB Plaats
        elif voor and _ocr_cijfers(voor[-1]):
            pc = '4'                                     # 2000 Antwerpen / 8001 Zurich
        sterk = bool(pc) and ((pc == 'nl' and land == 'nl') or (pc == '4' and land in ('be', 'nl', 'buiten')))
        uit.append((land, pl, sterk or land == 'buiten' or pl not in DUBBELE_PLAATSEN))
    # Losse NL-postcode die niet bij een NL-plaats staat: aanwijzing, geen bewijs (en botst met een buitenlandse plaats).
    for m in re.finditer(r'(?<![a-z0-9])([1-9]\d{3}) ?(?!(?:to|en|tm|in|at|nu|on|of|tl|ft|pt|bv)(?![a-z]))[a-z]{2}(?![a-z0-9])', seg):
        if not any(h[2] in NL_PLACES and 0 <= len(seg[:m.start()].split()) <= h[0] <= len(seg[:m.end()].split()) + 1
                   for h in hits):
            uit.append(('nl', 'postcode', False))
            break
    return uit


def _adres_segmenten(head):
    """Kop opgeknipt in stukjes per adreslabel: [(soort, tekst)], soort = huidig/oud/neutraal.
    Datums in het stukje tellen mee: 'sinds 2022'/'heden' = huidig, 'tot 2019' = oud."""
    marks = list(ADRES_LABEL_RE.finditer(head))
    segs = [('neutraal', head[:marks[0].start() + 1] if marks else head)]
    for k, m in enumerate(marks):
        stop = marks[k + 1].start() + 1 if k + 1 < len(marks) else len(head)
        tekst = ' ' + head[m.end():min(stop, m.end() + 90)] + ' '
        soort = 'huidig' if m.group('huidig') else 'oud' if m.group('oud') else 'neutraal'
        if soort == 'neutraal':
            if re.search(r' (?:sinds|since|vanaf|from) (?:19|20)\d\d | ' + NU_RE + ' ', tekst):
                soort = 'huidig'
            else:
                t = re.search(r' (?:tot|until|till|to|t m) ((?:19|20)\d\d) ', tekst)
                if t and int(t.group(1)) < dt.date.today().year:
                    soort = 'oud'
        segs.append((soort, tekst))
    return segs


# Eigen mobiel nummer uit Spanje (+34 6/7…) of India (+91 6-9…, 10 cijfers): wens opdrachtgever = niet meenemen.
ES_IN_MOBIEL = re.compile(r'^\+(?:34[67]\d{8}|91[6-9]\d{9})$')


def eigen_es_in_nummer(snippet, phones):
    """Spaans/Indiaas MOBIEL nummer dat in de kop van het cv staat (bij naam en contactgegevens), dus van de
    kandidaat zelf. Niet: een nummer verderop (referentie, werkgever, opdrachtgever) of een vaste lijn (bedrijf)."""
    kop_cijfers = re.sub(r'\D', '', _kop(snippet))
    for p in phones or []:
        p = str(p)
        if p in BUREAU_CONTACT:
            continue                       # nummer van een bureau/recruiter (staat op cv's van 3+ mensen)
        if ES_IN_MOBIEL.match(p) and p[3:] in kop_cijfers:
            return p
    return ''


def woonplaats_bepalen(snippet, phones=(), emails=(), doc=None):
    """Huidige woonplaats: ('nl'|'buitenland'|'tegenstrijdig'|'aanwijzing_buitenland'|'', uitleg). 'nl' = NL of BE.
    - Meerdere adressen: 'huidig adres' / 'sinds 2022' wint; 'vorig adres' telt niet mee. Is niet duidelijk welk
      adres actueel is (NL/BE en buitenland door elkaar): 'tegenstrijdig' -> AI, blijft het onzeker dan door.
    - Alleen de woonplaats telt; werkplek (ook grensarbeid NL<->BE), school, geboorteplaats en nationaliteit niet.
    - Gelijknamige plaatsen (Bergen, Norway): het land erachter beslist; twijfel -> AI -> anders door.
    - Postcode moet bij de plaats passen; een postcode alleen is geen bewijs.
    - 'buitenland' alleen als het buitenlandse adres duidelijk is en NIETS op NL/BE wijst.
    Nooit op naam, afkomst of nationaliteit. Ontbrekende informatie is nooit bewijs."""
    head = _kop(snippet)
    text = ' ' + _woorden_en_cijfers(snippet or '') + ' '
    nl_be = NL_PLACES | BE_PLACES
    buiten = (FOREIGN_PLACES | INDIA_PLACES) - BUITENLAND_IGNORE - BE_PLACES
    per_soort = {'huidig': [], 'neutraal': [], 'oud': []}
    for soort, seg in _adres_segmenten(head):
        per_soort[soort] += _landen_in(seg)
    lijst = per_soort['huidig'] or per_soort['neutraal']
    if not per_soort['huidig'] and doc is not None and 'woonplaats' in doc.keys() and doc['woonplaats']:
        lijst = lijst + [('nl', doc['woonplaats'], False)]   # adresregel uit de originele tekst
    nl_hits = [h for h in lijst if h[0] in ('nl', 'be')]
    buiten_hits = [h for h in lijst if h[0] == 'buiten']
    onzeker = [h for h in lijst if h[0] == 'onzeker']
    tel_nl = any(str(p).startswith(('+31', '+32')) for p in phones or [])
    tel_buiten = [str(p) for p in phones or [] if str(p).startswith('+') and not str(p).startswith(('+31', '+32'))]
    nu = [text[m.end():m.end() + 160] + ' ' for m in re.finditer(r'(?<!\d)20[0-3]\d ' + NU_RE + r' ', text)]
    nu_nl = any(_plaats_in(w, nl_be) for w in nu)
    nu_buiten = next((_plaats_in(w, buiten) for w in nu if _plaats_in(w, buiten) and not _plaats_in(w, nl_be)), '')
    sterk_nl = [h for h in nl_hits if h[2]]
    es_in = '' if tel_nl else eigen_es_in_nummer(snippet, phones)
    land_nr = {'+34': 'Spaans', '+91': 'Indiaas'}.get(es_in[:3], '')
    if nl_hits and not buiten_hits and not onzeker:
        if es_in:
            # NL/BE-adres maar alleen een eigen Spaans/Indiaas mobiel nummer: AI kijkt mee (2 vragen eens = weg).
            return 'tegenstrijdig', f'{nl_hits[0][1]}; eigen {land_nr} mobiel nummer'
        if sterk_nl or not tel_buiten:
            return 'nl', nl_hits[0][1]
        return 'tegenstrijdig', f'{nl_hits[0][1]}; buitenlands nummer'   # bv. alleen 'Bergen' + nummer +47
    if onzeker or (nl_hits and buiten_hits):
        return 'tegenstrijdig', ', '.join(h[1] for h in (nl_hits + buiten_hits + onzeker)[:3])
    if buiten_hits:
        plek = buiten_hits[0][1]
        band = []
        if tel_nl:
            band.append('NL/BE-nummer')
        if nu_nl:
            band.append('huidige baan in NL/BE')
        if any(re.search(r'\.(?:nl|be)$', str(e)) and not GENERIC_EMAIL.match(str(e)) for e in emails or []):
            band.append('.nl/.be-mail')
        rest = nl_be_band(snippet, [doc] if doc is not None else [])
        if rest:
            band.append(rest)
        if band:
            return 'tegenstrijdig', f'{plek.title()}; ' + '; '.join(band)
        return 'buitenland', plek.title()
    if tel_nl:
        return 'nl', 'telefoon'
    if es_in and not nu_nl:
        return 'buitenland', f'eigen {land_nr} mobiel nummer ({es_in[:3]})'
    if es_in:
        return 'tegenstrijdig', f'eigen {land_nr} mobiel nummer; huidige baan in NL/BE'
    if tel_buiten or nu_buiten:
        return 'aanwijzing_buitenland', (nu_buiten.title() if nu_buiten else 'telefoon ' + tel_buiten[0][:4])
    return '', ''


def woonland(snippet, phones):
    """Oude vorm: 'nl' / 'buitenland' / '' (tegenstrijdig en losse aanwijzingen tellen niet als buitenland)."""
    uit, waar = woonplaats_bepalen(snippet, phones)
    return (uit, waar) if uit in ('nl', 'buitenland') else ('', '')


NL_BE_SCHOOL = re.compile(r'(?i)\b(?:hogeschool|universiteit|tu delft|tu eindhoven|tu/e|vrije universiteit|uva|hva|'
                          r'avans|fontys|saxion|windesheim|hanzehogeschool|inholland|haagse hogeschool|nhl|stenden|'
                          r'zuyd|han|rotterdam school|erasmus|nyenrode|tilburg university|ku leuven|ugent|vub|'
                          r'universiteit gent|universiteit antwerpen|hogent|thomas more|odisee|vives|howest|pxl|'
                          r'karel de grote|roc|mbo|hbo|vwo|havo|vmbo)\b')


def nl_be_band(snippet, docs=()):
    """Band met Nederland/België in de rest van het cv: Nederlandstalig, werk in een NL/BE-plaats, of opleiding
    op een NL/BE-school. Geeft een korte uitleg of ''."""
    redenen = []
    if cv_taal(snippet) == 'nl':
        redenen.append('Nederlandstalig cv')
    werk = ' '.join(str(d['werkplaatsen'] or '') for d in docs if 'werkplaatsen' in d.keys())
    body = ' ' + normalized_name((snippet or '')[450:]) + ' '
    plekken = [pl for pl in NL_PLACES | BE_PLACES if f' {pl} ' in ' ' + normalized_name(werk) + ' ' or f' {pl} ' in body]
    if plekken:
        redenen.append('werkte/woonde in NL/BE (' + ', '.join(sorted(plekken)[:2]) + ')')
    opl = ' '.join(str(d['opleidingen'] or '') for d in docs if 'opleidingen' in d.keys())
    if NL_BE_SCHOOL.search(opl) or NL_BE_SCHOOL.search(body):
        redenen.append('studeerde in NL/BE')
    return ' en '.join(redenen[:2])


MIN_JAREN_NL = 4    # opdrachtgevers vragen vaak 5 jaar in NL/BE wonen (screening); cv is nooit exact, dus 4 als grens


def jaren_nl_be(snippet):
    """Hoeveel jaar staat iemand volgens het cv in NL/BE (werk of opleiding met jaartallen en een NL/BE-plaats of
    -school)? Overlap telt één keer. Middelbare school/mbo in NL = hier opgegroeid (telt als 10 jaar)."""
    text = ' ' + _woorden_en_cijfers(snippet or '') + ' '
    if re.search(r' (?:havo|vwo|vmbo|mavo|mbo|roc|atheneum|gymnasium|lyceum|basisschool) ', text):
        return 10
    this_year = dt.date.today().year
    nl = sorted(NL_PLACES | BE_PLACES, key=len, reverse=True)
    jaren = set()
    marks = list(re.finditer(r'(?<!\d)(19[5-9]\d|20[0-3]\d)(?!\d)', text))
    i = 0
    while i < len(marks):
        j = i + 1 if i + 1 < len(marks) and marks[i + 1].start() - marks[i].end() <= 12 else i
        begin, end = int(marks[i].group(1)), int(marks[j].group(1))
        tail = marks[j].end()
        if re.match(r'\s*(?:heden|present|nu|now|current|today|dd)\b', text[tail:]):
            end = this_year
        nxt = marks[j + 1].start() if j + 1 < len(marks) else len(text)
        window = text[tail:min(nxt, tail + 160)] + ' '
        if any(f' {p} ' in window for p in nl) or NL_BE_SCHOOL.search(window):
            jaren |= set(range(min(begin, end), max(begin, end) + (0 if end > begin else 1)))
        i = j + 1
    return len(jaren)


def laatst_buitenland(snippet):
    """Laatste jaar waarin het cv iemand in het buitenland plaatst (werk of opleiding met jaartallen en een
    buitenlandse plaats). Geeft (jaar, plaats) of (0, ''). Geldt voor iedereen gelijk, ook Nederlanders."""
    text = ' ' + _woorden_en_cijfers(snippet or '') + ' '
    this_year = dt.date.today().year
    places = sorted((FOREIGN_PLACES | INDIA_PLACES) - BUITENLAND_IGNORE - BE_PLACES, key=len, reverse=True)
    best = (0, '')
    marks = list(re.finditer(r'(?<!\d)(19[5-9]\d|20[0-3]\d)(?!\d)', text))
    i = 0
    while i < len(marks):
        start = marks[i]
        # Een periode: '2019 2022' of '2019 heden'; anders een los jaartal.
        j = i + 1 if i + 1 < len(marks) and marks[i + 1].start() - start.end() <= 12 else i
        end_year = int(marks[j].group(1))
        tail_start = marks[j].end()
        if j == i and re.match(r'\s*(?:heden|present|nu|now|current|today|actualidad|dd)\b', text[tail_start:]):
            end_year = this_year
        nxt = marks[j + 1].start() if j + 1 < len(marks) else len(text)
        window = text[tail_start:min(nxt, tail_start + 160)]
        if re.match(r'\s*(?:heden|present|nu|now|current|today|actualidad)\b', window):
            end_year = this_year
        pl = next((p for p in places if f' {p} ' in window + ' '), '')
        if pl and not any(f' {n} ' in window + ' ' for n in NL_PLACES | BE_PLACES) and end_year > best[0]:
            best = (min(end_year, this_year), pl)
        i = j + 1
    return best


def woonplaats_from(text):
    """Woonplaats = plaats in de kop of op een contactregel (naast adres, postcode, mail of telefoon).
    Een plaats bij een baan of stage ('Projectmanager, Den Haag') is de WERKplek, niet de woonplaats."""
    lines = [l for l in (text or '').splitlines() if l.strip()][:40]
    contact = re.compile(r'(?i)@|\b\d{4}\s?[a-z]{2}\b|(?:\+31|\b0)[\s\-]?\d|woonplaats|adres|straat|laan|weg\b|plein')
    job = re.compile(r'(?i)\b(19|20)\d{2}\b|heden|present|stage|intern|bij\b|medewerker|manager|developer|engineer')
    def places(line):
        low = normalized_name(line)
        # 'Sander van Rijswijk', 'Jan van Breda': plaatsnaam als deel van de achternaam telt niet.
        return [pl for pl in NL_PLACES if re.search(r'(?<![a-z])' + re.escape(pl) + r'(?![a-z])', low)
                and not re.search(r'(?<![a-z])(?:van|de|der|den|ter|ten|uit|op|in t|v d)\s+' + re.escape(pl) + r'(?![a-z])', low)]
    # 1. Eerst een echte adresregel: 'Adres ... 2037 GJ Haarlem' of 'Woonplaats: ...'.
    for line in lines:
        if re.search(r'(?i)woonplaats|adres|\b\d{4}\s?[a-z]{2}\b', line) and places(line):
            return max(places(line), key=len)
    for k, line in enumerate(lines):
        hits = places(line)
        if not hits or SCHOOL_WORD.search(line):
            continue                      # 'Hogeschool van Amsterdam' zegt niets over waar je woont
        if (contact.search(line) and not job.search(line)) or (k < 4 and not job.search(line)):
            return max(hits, key=len)
    return ''


SCHOOL_WORD = re.compile(r'(?i)\b(?:hogeschool|universiteit|university|academie|academy|college|lyceum|gymnasium|'
                         r'atheneum|scholengemeenschap|school|roc|instituut|institute|campus)\b')


def opleidingen_from(text):
    """Alle onderwijsinstellingen uit het cv (HvA, Nova College, Het Schoter, EuroCollege ...)."""
    out = []
    in_edu = False
    for line in (text or '').splitlines()[:300]:
        line = ' '.join(line.split())
        low = normalized_name(line)
        if re.fullmatch(r'(?:opleiding(?:en)?|education|onderwijs|scholing)', low):
            in_edu = True
            continue
        if in_edu and re.fullmatch(r'(?:werkervaring|ervaring|work experience|experience|vaardigheden|skills|'
                                   r'ict vaardigheden|talen|talenkennis|languages|hobby s|interesses|profiel|stage)', low):
            in_edu = False
        if in_edu and not SCHOOL_WORD.search(line):
            # 'Het Schoter · Haarlem': in het opleidingsblok is 'naam · plaats' de school.
            m = re.match(r'^(.{3,50}?)\s*(?:·|,|\|| - | – )\s*(.+)$', line)
            if m and normalized_name(m.group(2)) in NL_PLACES and not re.search(r'\d{4}', m.group(1)):
                name = normalized_name(m.group(1))
                if name and name not in out and not re.fullmatch(r'(?:hbo|mbo|wo|havo|vwo|vmbo)', name):
                    out.append(name)
            continue
        if not SCHOOL_WORD.search(line) or len(line) > 160 or '@' in line:
            continue
        line_nodate = re.sub(r'\b(?:19|20)\d{2}\b|\bheden\b|\bpresent\b', ' ', line)
        for part in re.split(r'\s*(?:·|\||,| - | – |:)\s*', line_nodate):
            if SCHOOL_WORD.search(part):
                name = normalized_name(re.sub(r'\(.*?\)', '', part))
                name = re.sub(r'^(?:hbo|mbo|wo|havo|vwo|vmbo|bachelor|master|opleiding)\s+', '', name).strip()
                if 4 <= len(name) <= 60 and name not in ('school', 'hogeschool', 'universiteit', 'college') \
                        and name not in out:
                    out.append(name)
    return ' ; '.join(out[:6])


def werkgevers_from(text):
    """Alle werkgevers uit de werkervaring ('… bij Codewerk', '… at Codewerk', 'Functie · Bedrijf')."""
    job = re.compile(r'(?i)\b(19|20)\d{2}\b|heden|present')
    out = []
    for line in (text or '').splitlines()[:300]:
        line = ' '.join(line.split())
        if not job.search(line) or '@' in line or len(line) > 160:
            continue
        m = re.search(r'(?i)\b(?:bij|at|@)\s+([A-Z0-9][\w&.\'\- ]{1,50})', line) or \
            re.search(r'\s[·|]\s*([A-Z0-9][\w&.\'\- ]{1,50})', line)
        if m:
            org = re.split(r'\s*(?:,|\(| - | – |·|\|)\s*', m.group(1))[0].strip(' .')
            # Zin loopt door: 'Cloudig. Verantwoordelijk voor ...' -> 'Cloudig' (niet bij 'J.P. Morgan', 'B.V.').
            zin = re.search(r'(?<![A-Z])(?<![A-Z]\.[A-Z])\.\s+(?=[A-Z][a-z])', org)
            if zin and len(org[:zin.start()]) >= 3:
                org = org[:zin.start()].strip(' .')
            org = re.split(r'(?<=B\.V\.|N\.V\.)\s+(?=[A-Z][a-z])', org)[0].strip(' ')
            if len(org) >= 3 and normalized_name(org) not in NL_PLACES and org not in out:
                out.append(org)
    return ' ; '.join(out[:8])


def werkplaatsen_from(text):
    """Plaatsen die bij een baan/stage staan, bv. '2025 - heden Internship Junior Projectmanager, Den Haag'."""
    job = re.compile(r'(?i)\b(19|20)\d{2}\b|heden|present|stage|intern|\bbij\b|medewerker|manager|developer|engineer|consultant')
    out = []
    for line in (text or '').splitlines()[:300]:
        if not job.search(line) or '@' in line:
            continue
        low = normalized_name(line)
        for pl in NL_PLACES:
            if re.search(r'(?<![a-z])' + re.escape(pl) + r'(?![a-z])', low) and pl not in out:
                out.append(pl)
    return ' ; '.join(out[:4])


BIJBAAN_LINE = re.compile(r'(?i)\b(?:bijbaan(?:tje)?|bijbanen|parttime|part-time|vakantiewerk|vakantiebaan|'
                          r'weekendwerk|weekendbaan|studentenbaan|studentbaan|side job)\b')


def bijbanen_from(text):
    """Bijbanen uit het cv (alleen als het er echt als bijbaan/parttime/vakantiewerk staat)."""
    found = []
    for company in _stages_iter(text, BIJBAAN_LINE):
        if normalized_name(company) not in [normalized_name(f) for f in found]:
            found.append(company)
    return ' ; '.join(found[:4])


def _stages_iter(text, line_re=None):
    line_re = line_re or STAGE_LINE
    lines = [' '.join(l.split()) for l in text.splitlines()[:300]]
    for k, line in enumerate(lines):
        if not line_re.search(line) or len(line) > 140:
            continue
        m = re.search(r'(?i)\b(?:bij|at|@)\s+([A-Z0-9][\w&.\- ]{1,40})', line)
        if not m:
            parts = re.split(r'\s*(?:,| - | – | \| )\s*', line)
            cand = [p for p in parts[1:] if p and not line_re.search(p) and not re.search(r'\d{4}', p)]
            if not cand and k + 1 < len(lines) and lines[k+1] and not re.search(r'\d{4}', lines[k+1]):
                cand = [lines[k+1]]
            company = cand[0] if cand else ''
        else:
            company = m.group(1)
        company = re.sub(r'\(.*?\)', '', company).strip(' .,:')[:40]
        if ')' in company or '(' in company or company[:1].islower():
            continue                      # stuk zin, zoals 'detachering van IT-specialisten)'
        if normalized_name(company) in NL_PLACES:
            continue                      # 'Projectmanager, Den Haag': dat is een plaats, geen bedrijf
        if len(company) >= 3 and not re.fullmatch(r'(?i)(?:stage|stagiair|hbo|mbo|wo|bijbaan|parttime|vakantiewerk)', company):
            yield company


def linkedins_in(text):
    vals = set()
    for m in URL_RE.finditer(text):
        link = m.group(0).strip('.,;:()')
        link = re.sub(r'^https?://', '', link, flags=re.I)
        link = re.sub(r'^www\.', '', link, flags=re.I)
        vals.add('https://' + link.rstrip('/').lower())
    return sorted(vals)[:10]


def normalized_snippet(text):
    return ' '.join(re.findall(r'[a-z0-9]+', asciifold(text.lower())))[:7500]


def signature(text):
    words = re.findall(r'[a-z]{4,}', asciifold(text.lower()))
    counter = collections.Counter(w for w in words if w not in STOP_WORDS)
    return sorted((w for w, _ in counter.most_common(100)))


# Een sectietitel moet een echte losse kopregel zijn. Losse woorden middenin
# een boek, contract, rapport of vacature tellen niet mee.
CV_SECTION_PATTERNS = {
    'werkervaring': re.compile(r'(?i)^(?:(?:relevante|professionele|recente|belangrijkste|eerdere|overzicht|selectie|recent|relevant|selected|key)\s+)?'
                               r'(?:werkervaring|werk ervaring|werkzaamheden|loopbaan|loopbaanoverzicht|work experience|professional experience|'
                               r'employment history|employment record|career history|career summary|berufserfahrung|ervaring in het werk|'
                               r'experience|ervaring|relevant experience|employment|work history|career|career overview|professional background|'
                               r'professional career|opdrachten|opdrachtgevers|projectervaring|project ervaring|project experience|'
                               r'arbeidsverleden|werkgevers|werkhistorie|werk historie|carri[eè]re)'
                               r'(?:\s*\([^)]{1,30}\))?\s*:?$'),
    'opleiding': re.compile(r'(?i)^(?:(?:opleiding(?:en)?|education|onderwijs|scholing|cursussen|trainingen|training|certificeringen|'
                            r'certificaten|certifications|courses)(?:\s*(?:,|&|\+|/|\ben\b|\band\b)\s*(?:opleiding(?:en)?|education|'
                            r'cursussen|cursus|trainingen|training|certificeringen|certificaten|certifications|courses|diploma\'?s|'
                            r'diplomas))*|studie(?:s)?|qualifications|academic qualifications|diploma(?:\'?s)?|academic background|'
                            r'onderwijs en opleiding(?:en)?|gevolgde opleidingen|relevante opleidingen)(?:\s*\([^)]{1,30}\))?\s*:?$'),
    'vaardigheden': re.compile(r'(?i)^(?:vaardigheden|competenties|skills|technical skills|key skills|core skills|core competencies|competencies|'
                               r'kerncompetenties|certificeringen|certifications|talen|languages|persoonlijke vaardigheden|certificaten|'
                               r'certificaten en cursussen|talenkennis|computervaardigheden|digitale vaardigheden|ict-vaardigheden|'
                               r'kennis en vaardigheden|kennis en ervaring|kennis|technische kennis|vakkennis|expertise|werkgebieden|'
                               r'kennisgebieden|kernkwaliteiten|tools|software|methoden en technieken|methodes en technieken|'
                               r'technieken|ict-kennis|it-kennis|hard skills|soft skills)\s*:?$'),
    'profiel': re.compile(r'(?i)^(?:persoonlijk profiel|professional profile|persoonlijke samenvatting|profiel|profile|over mij|about me|'
                          r'professional summary|summary|personal profile|personal statement|personalia|persoonlijke gegevens|'
                          r'algemene gegevens|personal details|personal information|personal data|samenvatting|'
                          r'persoonlijke intro|kandidaatprofiel|profielschets)\s*:?$')
}
# Een documenttitel is sterker dan losse CV-kopjes.
# Geen generieke trefwoordencheck op de hele tekst: een échte kandidaat mag
# rapportages/handleidingen schrijven, facturatie doen of op vacatures werken.
NIET_CV_TITELS = [
    ('vacature', re.compile(
        r'(?i)^(?:vacature(?:tekst)?|vacatur|vacancy|job\s+(?:posting|advertisement|description|requirements|opening)|'
        r'functie(?:omschrijving|eisen|profiel)|inhuuraanvraag|aanvraag\s+(?:inhuur|personeel)|'
        r'eisen\s+en\s+wensen|opdrachtomschrijving)(?:\b[\s:–-]+.{0,80})?$')),
    ('factuur / administratie', re.compile(
        r'(?i)^(?:factuur|facturatie|facturering|invoice|billing|creditnota|credit\s+note|'
        r'betalingsherinnering|declaratie(?:formulier)?|onkostendeclaratie|'
        r'urenstaat|urenregistratie|kostenoverzicht)(?:\b[\s:–-]+.{0,80})?$')),
    ('handleiding / instructie', re.compile(
        r'(?i)^(?:(?:gebruikers|installatie|beheer|werk|service|praktijk)?handleiding|'
        r'gebruiksaanwijzing|user\s+(?:manual|guide)|instruction\s+manual|'
        r'werkinstructie|werkwijze|stappenplan|installatiegids|manual|'
        r'instructiedocument|instructies\s+voor|how\s+to)'
        r'(?:\b[\s:–-]+.{0,80})?$')),
    ('rapport / rapportage', re.compile(
        r'(?i)^(?:(?:onderzoeks|project|jaar|eind|voortgangs|advies|evaluatie|'
        r'beoordelings|management|stage|sollicitatie|salaris|loopbaanadvies|'
        r'kwaliteit|audit|marktonderzoeks|klant|hr[\s_-]*)?'
        r'(?:rapport|rapportage|verslag)(?:en|s)?|'
        r'interviewsamenvatting|gespreksnotulen|gespreksverslag|'
        r'kandidatenbeoordeling|evaluatieformulier|jaarverslag|'
        r'auditrapport|research\s+report|report|reporting\s+document)'
        r'(?:\b[\s:–-]+.{0,80})?$')),
    ('opdracht / eisen', re.compile(
        r'(?i)^(?:werkopdracht|aanbesteding|bestek|projectplan|'
        r'klantpresentatie|website[\s_-]*team|teamoverzicht|'
        r'projectbrief|projectbeschrijving|requirements\s+document|'
        r'specificatie|opdracht(?:beschrijving|bevestiging)|'
        r'implementatieplan|plan\s+van\s+aanpak|adviesdocument)'
        r'(?:\b[\s:–-]+.{0,80})?$')),
    ('contract / beleid', re.compile(
        r'(?i)^(?:stageovereenkomst|arbeidsovereenkomst|werkovereenkomst|'
        r'dienstverleningsovereenkomst|contract|privacybeleid|'
        r'hr[\s_-]*privacybeleid|avg[\s_-]*beleid|'
        r'intakeformulier|sollicitatieformulier|toestemmingsverklaring|'
        r'verwerkersovereenkomst|algemene\s+voorwaarden|'
        r'personeelsreglement)(?:\b[\s:–-]+.{0,80})?$')),
    ('lijst / lesmateriaal', re.compile(
        r'(?i)^(?:kandidatenlijst|kandidatenoverzicht|'
        r'cv[\s_-]*(?:bestanden|overzicht|database|lijst)|'
        r'overzicht\s+(?:van\s+)?(?:cv.?s|kandidaten|bestanden)|'
        r'database[\s_-]*export|linkedin[\s_-]*resultaat|'
        r'zoekresultaten|boekbespreking|lesmateriaal|'
        r'cursusmateriaal|trainingsmateriaal|sollicitatietips|'
        r'cv[\s_-]*checklist|checklist)(?:\b[\s:–-]+.{0,80})?$')),
    ('cv-sjabloon', re.compile(
        r'(?i)^(?:cv[\s_-]*(?:template|sjabloon|voorbeeld|voorbeeldbestand)|'
        r'(?:template|sjabloon|voorbeeld)[\s_-]*(?:cv|curriculum\s+vitae)|'
        r'curriculum\s+vitae[\s_-]*(?:template|sjabloon|voorbeeld)|'
        r'resum[eé][\s_-]*(?:template|example)|sample[\s_-]*resume)'
        r'(?:\b[\s:–-]+.{0,80})?$')),
]

# Extra niet-cv-documenten. Alleen een documenttitel of een apart woord in
# de BESTANDSNAAM is een afwijssignaal, geen functie of losse werkbeschrijving.
NIET_CV_TITELS.extend([
    ('recruiterprofiel', re.compile(
        r'(?i)^(?:recruiter[\s_-]*(?:profiel|profile|briefing|beoordeling)|'
        r'recruitment[\s_-]*(?:profiel|briefing|rapport)|'
        r'kandidaten[\s_-]*(?:profiel|advies|beoordeling)|'
        r'gewenst[\s_-]*profiel)(?:[\s:–-]+.{0,80})?$')),
    ('verslag / rapport', re.compile(
        r'(?i)^(?:stageverslag|eindverslag|voortgangsverslag|'
        r'functioneringsverslag|evaluatieverslag|adviesverslag|'
        r'klantverslag|weekverslag|maandverslag|werkverslag|'
        r'gespreksverslag|reflectieverslag|onderzoeksverslag|auditverslag|'
        r'verslag|rapport|rapportage|report|'
        r'jaarverslag|jaaroverzicht|maandrapportage|'
        r'voortgangsrapportage|projectrapportage|notulen|'
        r'meeting[\s_-]*minutes)(?:[\s:–-]+.{0,80})?$')),
    ('checklist / instructie', re.compile(
        r'(?i)^(?:checklist|controlelijst|afvinklijst|'
        r'draaiboek|praktijkhandleiding|quick[\s_-]*start[\s_-]*guide|'
        r'gebruikersgids|gebruikersinstructie)(?:[\s:–-]+.{0,80})?$')),
    ('salaris / facturatie', re.compile(
        r'(?i)^(?:salaris|salarisoverzicht|salarisstrook|loonstrook|'
        r'loonlijst|payroll[\s_-]*report|payslip|salary[\s_-]*report|'
        r'factuur|facturen|facturatie|factuur(?:overzicht|specificatie)|'
        r'facturering|invoice|invoices|billing[\s_-]*statement)'
        r'(?:[\s:–-]+.{0,80})?$')),
])

# Herken ook "CV_Jan_2026_jaarverslag.pdf", maar NIET "Senior
# Recruiter" of "Salarisadministrateur": die woorden zijn geen genre.
NIET_CV_BESTANDSNAAM_WORDEN = re.compile(
    r'(?i)(?:^|[\s_.-])(?:'
    r'checklist|controlelijst|afvinklijst|'
    r'handleiding|gebruikershandleiding|werkinstructie|gebruiksaanwijzing|'
    r'jaarverslag|jaaroverzicht|stageverslag|eindverslag|'
    r'projectverslag|projectrapport|projectrapportage|'
    r'onderzoeksrapport|onderzoeksverslag|evaluatieverslag|'
    r'voortgangsverslag|voortgangsrapportage|gespreksverslag|'
    r'rapport|rapportage|verslag|'
    r'salarisrapport|salarisoverzicht|salarisstrook|'
    r'factuur|facturen|facturatie|facturering|invoice|invoices|'
    r'kandidatenbeoordeling|stageovereenkomst|'
    r'cv[\s_.-]*(?:sjabloon|template|voorbeeld)'
    r')(?:$|[\s_.-])'
)


def niet_cv_documentsoort(text, filename=''):
    """Herken expliciet een ANDERE documentsoort aan de echte titel.

    'Rapportage' als documenttitel => geen cv. 'Rapportages gemaakt' onder
    Werkervaring => juist normaal op een cv. CV-mapcontext telt hier niet.
    """
    regels = [re.sub(r'\s+', ' ', line).strip(' :–—-\t')
              for line in (text or '')[:5000].splitlines() if line.strip()][:5]
    # Niet de functietitel direct na een echte CV-kop voor een documenttitel
    # aanzien. Een echte CV-header is sterk bewijs, geen blind vrijbrief.
    heeft_cv_kop = bool(regels and CV_HEADER.fullmatch(regels[0]))
    titels = regels[:1] if heeft_cv_kop else regels[:2]
    basis = str(filename or '').replace('\\', '/').rsplit('/', 1)[-1]
    basis = re.sub(r'(?i)\.(?:pdf|docx?|txt|rtf|odt|pages)$', '', basis)
    basis = re.sub(r'^(?:\d{1,5}[\s_.-]+)+', '', basis)
    basis = re.sub(r'(?i)^(?:cv|c\.v\.)[\s_.-]+', '', basis)
    basis = basis.replace('_', ' ').replace('-', ' ')
    titels.append(basis.strip())
    if NIET_CV_BESTANDSNAAM_WORDEN.search(basis):
        # Bestandsnamen met een expliciete documentsoort hebben voorrang;
        # een functienaam als 'Senior_Recruiter' geeft nooit deze match.
        return 'niet-cv-bestandsnaam'
    for soort, pattern in NIET_CV_TITELS:
        if any(pattern.fullmatch(title.strip()) for title in titels if title.strip()):
            return soort
    return ''


CV_HEADER = re.compile(r'(?i)^(?:europass\s+)?(?:curriculum vitae|resume|résumé|cv)'
                       r'(?:(?:\s*[-–—:]\s*|\s+(?:van|of|von|de)\s+|\s+)[a-zÀ-ÿ .-]{3,50})?$')
NON_CV_LINES = re.compile(r'(?i)\b(?:wij zoeken|solliciteer nu|vacature|functieomschrijving|de kandidaat dient|stageovereenkomst|onderzoeksrapport|inhoudsopgave|algemene voorwaarden|referaat|studieboek)\b')


# Loopbaanperiode = 2 verschillende jaartallen of een lopende functie.
# Losse datums of '2026-06-01' worden hierdoor NIET als periode beschouwd.
# Ook met maanden: 'jan 2010 – dec 2012', '01-2010 / 03-2012', 'sep. 2015 - heden'.
YEAR_RANGE = re.compile(
    r'(?i)(?<!\d)(?:19[5-9]\d|20[0-3]\d)\s*(?:[–—-]|/|\btot\b|\bto\b|\bt/m\b|\btill\b|\buntil\b)\s*'
    r'(?:(?:[a-z]{3,9}\.?|\d{1,2}\s*[-/.])\s*)?'
    r'(?:19[5-9]\d|20[0-3]\d|heden|present|current|huidig|nu|now|today|dd)(?!\w)')
YEAR_PRESENT = re.compile(
    r'(?i)(?<!\d)(?:19[5-9]\d|20[0-3]\d)\s*(?:[–—-]|\btot\b|\bto\b|\bt/m\b)\s*'
    r'(?:heden|present|current|nu|now|today)(?!\w)')


def experience_periods(lines):
    """Tel alleen werkperiodes onder het losse kopje Werkervaring."""
    spans=[]
    for idx, line in enumerate(lines[:230]):
        clean=line.strip(' :•*-\t')
        if CV_SECTION_PATTERNS['werkervaring'].fullmatch(clean):
            # Kap af bij een volgende CV-kop, en beperk het aantal regels.
            for following in lines[idx+1:idx+65]:
                normalized = following.strip(' :•*-\t')
                if any(rx.fullmatch(normalized) for kind,rx in CV_SECTION_PATTERNS.items() if kind != 'werkervaring'):
                    break
                for match in YEAR_RANGE.finditer(following):
                    # '2026-06-01' is geen range van jaren; ook niet via truc met spaties.
                    years = re.findall(r'(?:19[5-9]\d|20[0-3]\d)', match.group(0))
                    if len(years)==2 and int(years[0])>int(years[1]):
                        continue
                    spans.append(match.group(0))
    return list(dict.fromkeys(spans))[:5]


def cv_bestandsnaam_hint(filename):
    """Een losse CV-aanduiding in de bestandsnaam, ook 'C.V. Jacques ...'."""
    base = str(filename or '').replace('\\', '/').rsplit('/', 1)[-1]
    return bool(re.search(r'(?i)(?:^|[\s_.-])(?:c\.?v\.?|resume|résumé|curriculum[\s_-]*vitae)'
                          r'(?=$|[\s_.-])', base))


def cv_map_hint(source_path):
    """CV-map in een echt bestandspad; niet blind 'cv' ergens midden in een woord."""
    path = str(source_path or '').replace('\\', '/')
    folders = path.split('/')[:-1]
    # Een testpakket of scannermap heeft soms CV in de mapnaam zonder dat
    # het een recruiter-map is. Alleen echte CV-mappen als hint gebruiken.
    folders = [folder for folder in folders if not re.search(
        r'(?i)(?:scanner|stresstest|testbestanden|testdata|testset|testcases)', folder)]
    return any(re.search(r"(?i)(?:^|[\s_.-])(?:cv|cvs|cv['’]s|c\.?v\.?|resumes?|curriculum[\s_-]*vitae)"
                         r"(?=$|[\s_.-])", folder) for folder in folders)


def cv_signals(text, filename):
    lines = [' '.join(line.strip().split()) for line in text[:160000].splitlines()]
    heads = set()
    for line in lines[:220]:
        clean = line.strip(' :•*-\t')
        if not (2 <= len(clean) <= 55):
            continue
        for kind, expression in CV_SECTION_PATTERNS.items():
            if expression.fullmatch(clean):
                heads.add(kind)
    cv_header = any(CV_HEADER.fullmatch(line.strip()) for line in lines[:9])
    cv_filename = cv_bestandsnaam_hint(filename)
    non_cv = sum(bool(NON_CV_LINES.search(line)) for line in lines[:80])
    enormous = len(text) > 50000
    periods = experience_periods(lines) if 'werkervaring' in heads else []
    return heads, cv_header, cv_filename, non_cv, enormous, periods


GENERIC_EMAIL = re.compile(r'(?i)^(?:info|contact|office|hr|recruitment|recruiting|jobs|support|admin|sales|helpdesk|noreply|no-reply|team|personeelszaken)@')


# Kenmerken van vacatures en inhuuraanvragen (komen in een cv niet voor).
VACANCY_MARKERS = [
    r'aanvraagnummer', r'einde inschrijfdatum', r'inschrijfdatum', r'opties? verlenging', r'uren per week',
    r'uur per week', r'das opdracht', r'opleidings?niveau', r'wat ga je doen', r'wat vragen wij',
    r'wat bieden wij', r'wij bieden', r'wij zoeken', r'wie zoeken wij', r'wie ben jij', r'functie-?eisen',
    r'functieomschrijving', r'solliciteer', r'vacature', r'knock-?out', r'eisen en wensen', r'gunningscriteria',
    r'(?:maximaal |max\.? )?uurtarief', r'standplaats', r'dienstverband', r'salaris(?:indicatie|schaal)?',
    r'detacheringsprofiel', r'inhuur', r'functieschaal', r'reageren kan', r'interesse\?', r'jouw profiel',
    r'arbeidsvoorwaarden', r'aanvraagdocument', r'inzetduur', r'competentie-?eisen']
VACANCY_RE = [re.compile(r'(?i)\b' + m) for m in VACANCY_MARKERS]


def is_vacancy(text):
    """Minstens 3 verschillende vacature-/aanvraagkenmerken bovenin = vacature, geen cv."""
    top = '\n'.join(text.splitlines()[:150])
    return sum(bool(rx.search(top)) for rx in VACANCY_RE) >= 3


CV_NAME_HINT = re.compile(r'(?i)(?:^|[\s_.-])(cv|resume|r[eé]sum[eé]|curriculum|sollicitatie)(?:[\s_.-]|$)')


# Schoolportfolio's en reflecties. Alleen echte SCHOOL-woorden: Scrum-termen (sprint, product owner,
# retrospective, opdrachtgever) en 'beroepsproduct' staan ook gewoon in ICT-cv's en tellen niet.
PORTFOLIO_MARKERS = [r'leeruitkomst', r'reflectie(?:verslag)?', r'leerdoel', r'beoordelings?(?:criteria|formulier)',
                     r'studiepunten', r'peer ?feedback', r'feedback (?:van|gekregen van) (?:de )?docent',
                     r'zelfreflectie', r'ontwikkelpunt', r'competentie(?:s)? aangetoond', r'bewijsstuk']
PORTFOLIO_RE = [re.compile(r'(?i)\b' + m) for m in PORTFOLIO_MARKERS]


def is_school_doc(text, filename, strong_cv=False):
    """Portfolio / reflectie: 'portfolio' of 'reflectie' in de bestandsnaam, of 3+ echte schoolwoorden.
    Een document met duidelijke cv-opbouw (werkervaring + opleiding + contact) blijft altijd een cv."""
    if CV_NAME_HINT.search(filename or ''):
        return False
    if re.search(r'(?i)portfolio|reflectie', filename or ''):
        return True          # bestandsnaam zegt het al: ook met cv-opbouw is het een portfolio
    if strong_cv:
        return False
    top = '\n'.join(text.splitlines()[:200])
    return sum(bool(rx.search(top)) for rx in PORTFOLIO_RE) >= 3




PLACEHOLDER = re.compile(r'\[[^\]\n]{2,40}\]')


def all_periods(text):
    """Loopbaanperiodes overal in het document (ook in tabellen zonder kopjes)."""
    found = []
    for line in text[:60000].splitlines()[:260]:
        for match in YEAR_RANGE.finditer(line):
            years = re.findall(r'(?:19[5-9]\d|20[0-3]\d)', match.group(0))
            if len(years) == 2 and int(years[0]) > int(years[1]):
                continue
            found.append(match.group(0))
    return list(dict.fromkeys(found))


BLANK_FIELD = re.compile(r'_{3,}|\.{5,}|…{2,}')


def is_template(text):
    """Leeg cv-sjabloon of leeg formulier: veel [invulvelden] zoals [Naam], of invulvakken
    (________ / ...........) zoals bij een intake- of sollicitatieformulier."""
    if len(PLACEHOLDER.findall(text[:8000])) >= 3:
        return True
    lines = [l for l in (text or '')[:8000].splitlines() if l.strip()]
    blanks = sum(1 for l in lines if BLANK_FIELD.search(l))
    if blanks >= 3 and blanks >= 0.2 * max(1, len(lines)):
        return True
    # Lege cv-formulieren: drie of meer echte CV-kopjes, maar nagenoeg
    # geen ingevulde sectie-inhoud (ook zonder [Naam]-placeholders).
    sections = []
    for i, line in enumerate(lines[:120]):
        clean = line.strip(' \t:•*-')
        if any(rx.fullmatch(clean) for rx in CV_SECTION_PATTERNS.values()):
            sections.append(i)
    if len(sections) >= 3:
        empty = 0
        for n, ix in enumerate(sections):
            stop = sections[n+1] if n+1 < len(sections) else min(ix+6, len(lines))
            contents = [v.strip() for v in lines[ix+1:stop] if v.strip()]
            useful = [v for v in contents if len(v) >= 3 and not re.search(
                r'(?i)(?:\b20xx\b|\bxxxx\b|\bvul\s+(?:hier|in)\b|'
                r'\bin\s+te\s+vullen\b|\bvoorbeeld(?:naam|bedrijf|opleiding|werkgever)?\b|'
                r'\bplaceholder\b|\benter\s+(?:your|here)\b|'
                r'\b(?:your|jouw)\s+(?:name|naam|opleiding|werkervaring)\b|'
                r'_{3,}|\.{5,})', v)]
            if not useful:
                empty += 1
        if empty >= max(2, len(sections) - 1):
            return True
    if len(re.findall(r'(?i)\b(?:naam\s+voorbeeld|voorbeeldnaam|'
                      r'vul\s+(?:hier|in)|20xx|voorbeeldwerkgever|'
                      r'your\s+name\s+here|enter\s+your\s+name)\b', text[:8000])) >= 2:
        return True
    return False


def name_on_top(text, name):
    """De naam moet echt bovenaan het document staan (of als Naam:-veld)."""
    target = normalized_name(name)
    if not target:
        return False
    top = ' '.join(normalized_name(l) for l in text.splitlines()[:8])
    return target in top or bool(name_parts(text))


def headingless_cv(text, name, emails, phones, links, non_cv, enormous):
    """CV zonder herkenbare kopjes (bv. in een tabel): naam bovenaan, eigen
    contactgegevens, minstens 2 loopbaanperiodes, kort en geen ander documenttype."""
    personal = [e for e in emails if not GENERIC_EMAIL.match(e)]
    return (bool(name) and name_on_top(text, name) and personal_contact(emails, phones, links)
            and len(personal) <= 2 and len(all_periods(text)) >= 2
            and not non_cv and not enormous and len(text) < 15000
            and not is_template(text))


PERSONAL_HOSTS = ('github.com/', 'gitlab.com/', 'github.io', 'gitlab.io', 'netlify.app', 'vercel.app',
                  'wixsite.com', 'carrd.co', 'about.me', 'webflow.io', 'pages.dev', 'behance.net', 'dribbble.com')


def own_site_in(text, name):
    """Heeft het cv een eigen GitHub/portfolio/website (bv. pietervandam.nl)? Telt als contactgegeven."""
    words = [w for w in normalized_name(name).split() if len(w) >= 4 and w not in NAME_FILLERS]
    for site in websites_in(text):
        low = site.lower()
        if any(h in low for h in PERSONAL_HOSTS):
            return True
        host = urllib.parse.urlparse(site).netloc.replace('-', '')
        if words and any(w in host for w in words):
            return True
    return False


def personal_contact(emails, phones, links):
    # Een generieke bedrijfsmail is geen bewijs dat de gegevens bij de naam horen.
    return any(not GENERIC_EMAIL.match(e) for e in emails) or bool(phones or links)


def classify(text, filename, name, emails, phones, links, layout_bonus=0, source_path=''):
    if not text.strip():
        return 0, 'geen leesbare tekst; OCR of handmatige controle nodig'
    heads, cv_header, cv_filename, non_cv, enormous, periods = cv_signals(text, filename)
    contact = personal_contact(emails, phones, links) or (bool(name) and own_site_in(text, name))
    structure = ('werkervaring' in heads and
                 ('opleiding' in heads or 'vaardigheden' in heads or 'profiel' in heads))
    rich = len(heads) >= 3 and 'werkervaring' in heads
    in_cv_map = cv_map_hint(source_path)
    explicit = cv_header or cv_filename or in_cv_map
    score = 12 * len(heads) + 17 * bool(name) + 14 * contact + 10 * cv_header + 5 * cv_filename
    score += 10 if structure else 0
    score += 8 if in_cv_map and structure else 0
    score += min(18, 9*len(periods)) if structure and name else 0
    score += min(5, max(0, layout_bonus)) if structure else 0
    score -= 30 if non_cv else 0
    score -= 25 if enormous else 0
    loose = headingless_cv(text, name, emails, phones, links, non_cv, enormous)
    if loose:
        score = max(score, 75)
    ander_document = niet_cv_documentsoort(text, filename)
    vier_banen = gelijktijdige_werkgevers(text) >= 4
    contract = is_stagecontract(text)
    if is_template(text) or ander_document or vier_banen or contract:
        score = min(score, 20)
    if not name:
        score = min(score, 49)
    if not structure and not (explicit and len(heads)>=2) and not loose:
        score = min(score, 49)
    if not contact and not (cv_header and rich):
        score = min(score, 54)
    if non_cv and not (cv_header and rich and contact):
        score = min(score, 49)
    if enormous and not cv_header:
        score = min(score, 49)
    reason = ('kopjes: ' + (', '.join(sorted(heads)) or 'geen') +
              f'; naam: {"ja" if name else "nee"}; contact: {"ja" if contact else "nee"}; '
              f'CV-kop: {"ja" if cv_header else "nee"}; werkperiodes: {len(periods)}' +
              ('; CV-bestandsnaam: ja' if cv_filename else '') +
              ('; mogelijk andere documentsoort' if non_cv or enormous else '') +
              ('; cv zonder kopjes (naam, contact en periodes)' if loose and not structure else '') +
              ('; leeg sjabloon' if is_template(text) else '') +
              ('; vacature/inhuuraanvraag' if is_vacancy(text) else '') +
              (f'; niet-cv-document: {ander_document}' if ander_document else '') +
              ('; minstens 4 gelijktijdige werkgevers' if vier_banen else '') +
              ('; stagecontract: 3+ contractsignalen' if contract else '') +
              ('; CV-map: ja' if in_cv_map else ''))
    return max(0,min(100,score)), reason


STAGE_CONTRACT_MARKERS = (
    re.compile(r'(?i)\b(?:stagegever|stageverlener|stagebedrijf|stagebegeleider)\b'),
    re.compile(r'(?i)\b(?:ondergetekenden|ondertekende partijen|beide partijen|partijen verklaren)\b'),
    re.compile(r'(?i)\b(?:stageovereenkomst|deze overeenkomst|de overeenkomst|stagecontract)\b'),
    re.compile(r'(?i)\b(?:handtekening(?:en)?|ondertekening|ondertekend door)\b'),
    re.compile(r'(?i)\b(?:artikel\s+\d+|art\.\s*\d+|contractduur|contractperiode)\b'),
    re.compile(r'(?i)\b(?:namens de organisatie|ondertekend te|datum ondertekening)\b'),
)


def is_stagecontract(text):
    """Stagecontract, geen CV: meerdere ONAFHANKELIJKE contractsignalen nodig.
    Naam, school, stageperiode of het woord 'overeenkomst' alleen zijn
    onvoldoende: ze kunnen ook in een echt CV staan.
    """
    top = (text or '')[:12000]
    if not top.strip():
        return False
    markers = {i for i, rx in enumerate(STAGE_CONTRACT_MARKERS) if rx.search(top)}
    if len(markers) < 3:
        return False
    # Een echte contractrelatie EN een handtekening/artikel/partijen.
    return 2 in markers and bool(markers & {1, 3, 4, 5})


def gelijktijdige_werkgevers(text):
    """Vier verschillende werkgevers met minimaal één volledig overlappend jaar.
    Twee gelijktijdige banen / dezelfde startdag bewijzen NIET dat het geen cv is.
    Alleen concrete 'bij X / at X' werkperiodes tellen mee.
    """
    jobs = werk_periodes({'snippet': normalized_snippet(text or '')})
    jobs = {(w, start, end) for w, start, end in jobs if w and not w.startswith('*')}
    if len({w for w, _, _ in jobs}) < 4:
        return 0
    # Vier verschillende werkgevers moeten écht tegelijk actief zijn;
    # alleen vier verschillende functies in verschillende jaren is geen signaal.
    years = {y for _, start, end in jobs for y in (start, end)}
    for year in sorted(years):
        active = {w for w, start, end in jobs if start <= year and end >= year + 1}
        if len(active) >= 4:
            return len(active)
    return 0


def decide_status(text, filename, name, emails, phones, links, score, threshold=70, source_path=''):
    if not text.strip():
        return 'other'        # gescand/afbeelding en ook de OCR vond niets: geen cv (staat wel in Bestanden en status)
    if is_template(text) or is_vacancy(text) or niet_cv_documentsoort(text, filename) or is_stagecontract(text):
        return 'other'
    if gelijktijdige_werkgevers(text) >= 4:
        # Een persoonlijke consultant kan meerdere parallelle opdrachten hebben.
        # Bij een sterke CV-identiteit op review zetten; anders uitsluiten.
        persoonlijk = (bool(name and reasonable_name(name))
                       and any(CV_HEADER.fullmatch(line.strip()) for line in text.splitlines()[:7])
                       and any(email_matches_name(name, e) and not GENERIC_EMAIL.match(e)
                               for e in emails))
        return 'review' if persoonlijk else 'other'
    # Vacatures/inhuuraanvragen zijn géén twijfel-cv's. Een expliciete
    # vacaturekop of bestandsnaam wordt ook zonder drie losse trefwoorden afgewezen.
    if re.match(r'(?i)^\s*(?:vacature|vacancy|inhuuraanvraag|job opening)\b',
                str(filename or '').replace('\\', '/').rsplit('/', 1)[-1]):
        return 'other'
    if any(re.match(r'(?i)^\s*(?:vacature|vacancy|inhuuraanvraag|job opening)\b', line)
           for line in text.splitlines()[:8]):
        return 'other'
    _h, _hdr, _fn, _nc, _en, _p = cv_signals(text, filename)
    strong_cv = ('werkervaring' in _h and 'opleiding' in _h
                 and (personal_contact(emails, phones, links) or (bool(name) and own_site_in(text, name))))
    if is_school_doc(text, filename, strong_cv) and not CV_HEADER.search('\n'.join(text.splitlines()[:9])):
        return 'other'
    heads, cv_header, cv_filename, non_cv, enormous, periods = cv_signals(text,filename)
    contact = personal_contact(emails, phones, links) or (bool(name) and own_site_in(text, name))
    # Echte CV-signalen moeten zwaarder wegen dan de afwezigheid van een
    # e-mailadres of datum. Minimaal twee inhoudelijke kopjes, waarvan Opleiding,
    # plus een CV-kop, een CV-bestandsnaam of een CV-map.
    in_cv_map = cv_map_hint(source_path)
    cv_context = cv_header or cv_filename or in_cv_map
    edu_met_inhoud = 'opleiding' in heads and bool(heads & {'werkervaring', 'profiel', 'vaardigheden'})
    betrouwbare_naam = bool(name and reasonable_name(name))
    if not non_cv and not enormous:
        # Jacques van Nes: 'Curriculum Vitae' in de tekst + naam + contact;
        # ook als afzonderlijke kopjes niet leesbaar uit een PDF komen.
        if betrouwbare_naam and cv_header and contact:
            return 'cv'
        # Ook zonder contact: Opleiding + Werkervaring/Profiel/Vaardigheden
        # en een herkenbare naam in een duidelijke CV-context is voldoende.
        if betrouwbare_naam and cv_context and edu_met_inhoud:
            return 'cv'
        # Een écht CV-document zonder gevonden kandidaatnaam. Geen naam raden:
        # het bestand is een CV, maar gaat NIET als geverifieerde kandidaat
        # de ontdubbeling in voordat er een persoonsnaam is gevonden.
        if (not betrouwbare_naam and cv_context and contact
                and {'opleiding', 'werkervaring'} <= heads and len(heads) >= 3
                and len(periods) >= 2):
            return 'cv'
    if score >= threshold and headingless_cv(text, name, emails, phones, links, non_cv, enormous):
        return 'cv'
    contact = personal_contact(emails, phones, links) or (bool(name) and own_site_in(text, name))
    structure = 'werkervaring' in heads and bool(heads & {'opleiding','vaardigheden','profiel'})
    # Werkperiodes versterken een echt CV maar mogen een verslag of contract
    # nooit op zichzelf in een automatisch uniek persoonsrecord veranderen.
    strong = structure and (len(heads)>=3 or cv_header or cv_filename)
    # Cv zonder contactgegevens (bv. geanonimiseerd): naam + werkervaring + opleiding +
    # 2 werkperiodes + 'cv' in bestandsnaam of bovenaan, en geen ander documenttype.
    if (name and 'werkervaring' in heads and 'opleiding' in heads and len(periods) >= 2
            and (cv_header or cv_filename or len(heads) >= 3
                 or all(w in normalized_name(filename) for w in normalized_name(name).split() if len(w) >= 3))
            and not non_cv and not enormous):
        return 'cv'
    if name and score >= threshold and ((contact and strong) or (cv_header and strong)) and not (non_cv and not cv_header) and not enormous:
        return 'cv'
    # Naam + eigen contactgegevens + de kopjes Werkervaring én Opleiding: dat is een cv
    # (een brief, verslag of vacature heeft die combinatie niet).
    if name and contact and 'werkervaring' in heads and 'opleiding' in heads and not non_cv and not enormous \
            and not is_vacancy(text) and not is_template(text):
        return 'cv'
    # 'Curriculum Vitae' bovenaan het document (niet alleen in de bestandsnaam) + naam + eigen contactgegevens
    # + minstens 2 cv-kopjes: dat is een cv, ook zonder het kopje Werkervaring.
    if (name and cv_header and contact and len(heads) >= 2 and not non_cv and not enormous
            and not is_vacancy(text) and not is_template(text)):
        return 'cv'
    if name and (structure or cv_header or cv_filename) and (contact or len(heads)>=2 or periods):
        return 'review'
    if not name and (structure or (cv_header and len(heads)>=1)):
        return 'review'
    # CV-bestand of CV-map met beperkte aanwijzingen, maar zonder sterk
    # bewijs: niet goedkeuren en niet weggooien; alleen beoordelen.
    if (cv_filename or in_cv_map) and heads and (betrouwbare_naam or len(heads) >= 2):
        return 'review'
    return 'other'

def textutil_extract(path):
    # -encoding UTF-8: anders faalt textutil op sommige oude .doc's ('text encoding isn't applicable').
    proc = subprocess.run(['textutil', '-convert', 'txt', '-encoding', 'UTF-8', '-stdout', str(path)],
                          capture_output=True, timeout=45, check=False)
    if proc.returncode == 0 and proc.stdout:
        return proc.stdout.decode('utf-8', errors='replace')
    raise RuntimeError((proc.stderr or b'textutil leverde geen tekst').decode('utf-8', errors='replace')[:180])


def docx_hyperlinks(path):
    urls = []
    try:
        with zipfile.ZipFile(path) as z:
            relfile = 'word/_rels/document.xml.rels'
            if relfile in z.namelist():
                root = ET.fromstring(z.read(relfile))
                for rel in root:
                    href = rel.attrib.get('Target', '')
                    if 'linkedin.com/in/' in href.lower():
                        urls.append(href)
    except Exception:
        pass
    return '\n'.join(urls)


def docx_extract(path):
    """Haal alinea's, tabellen, headers en footers uit DOCX zonder Microsoft Word."""
    ns = '{http://schemas.openxmlformats.org/wordprocessingml/2006/main}'
    fragments = []
    with zipfile.ZipFile(path) as z:
        candidates = [n for n in z.namelist() if n == 'word/document.xml' or
                      re.fullmatch(r'word/(?:header|footer)\d+\.xml', n)]
        for member in candidates:
            root = ET.fromstring(z.read(member))
            for para in root.iter(ns + 'p'):
                bits = []
                for node in para.iter():
                    if node.tag in (ns + 't', ns + 'delText') and node.text:
                        bits.append(node.text)
                    if node.tag == ns + 'tab':
                        bits.append(' ')
                if bits:
                    fragments.append(''.join(bits))
    return '\n'.join(fragments)


def odt_extract(path):
    ns = '{urn:oasis:names:tc:opendocument:xmlns:text:1.0}'
    with zipfile.ZipFile(path) as z:
        root = ET.fromstring(z.read('content.xml'))
    lines = []
    for item in root.iter():
        if item.tag in (ns + 'p', ns + 'h'):
            phrase = ' '.join(''.join(item.itertext()).split())
            if phrase:
                lines.append(phrase)
    return '\n'.join(lines)


def decode_office_text(raw):
    """Decode output of Word SaveAs Unicode/Text, including UTF-16LE *without BOM*."""
    if not raw:
        return ''
    if raw.startswith((b'\xff\xfe', b'\xfe\xff')):
        return raw.decode('utf-16', errors='replace')
    if raw.startswith(b'\xef\xbb\xbf'):
        return raw.decode('utf-8-sig', errors='replace')
    if len(raw) >= 4 and len(raw) % 2 == 0:
        pair_count = min(len(raw) // 2, 2000)
        odd_nulls = sum(raw[i] == 0 for i in range(1, pair_count * 2, 2))
        even_nulls = sum(raw[i] == 0 for i in range(0, pair_count * 2, 2))
        if odd_nulls / pair_count >= 0.35:
            return raw.decode('utf-16le', errors='replace')
        if even_nulls / pair_count >= 0.35:
            return raw.decode('utf-16be', errors='replace')
    try:
        return raw.decode('utf-8')
    except UnicodeDecodeError:
        return raw.decode('cp1252', errors='replace')


def office_text_with_libreoffice(path):
    """Optional Office fallback; LibreOffice is not required for other formats."""
    soffice = shutil.which('soffice') or shutil.which('libreoffice')
    if not soffice and sys.platform == 'win32':
        for loc in (r'C:\Program Files\LibreOffice\program\soffice.exe',
                    r'C:\Program Files (x86)\LibreOffice\program\soffice.exe'):
            if Path(loc).is_file():
                soffice = loc
                break
    if not soffice:
        raise RuntimeError('LibreOffice is niet geïnstalleerd')
    with tempfile.TemporaryDirectory(prefix='cv_lo_') as tmp:
        target = Path(tmp) / (path.stem + '.txt')
        proc = subprocess.run(
            [soffice, '-env:UserInstallation=file:///' + (Path(tmp) / 'profile').as_posix().lstrip('/'),
             '--headless', '--convert-to', 'txt:Text (encoded):UTF8', '--outdir', tmp, str(path)],
            capture_output=True, timeout=60)
        if proc.returncode or not target.exists():
            raise RuntimeError('LibreOffice kon bestand niet converteren')
        return decode_office_text(target.read_bytes())


def doc_legacy_windows(path):
    """Lees een oud .doc alleen als Microsoft Word op deze pc geïnstalleerd is.

    Word-automatisering met macrobeveiliging op uitgeschakeld voor macros; het
    bronbestand wordt alleen-lezen geopend en naar tijdelijke tekst geconverteerd.
    """
    if sys.platform != 'win32':
        raise RuntimeError('Oud .doc-bestand vereist op dit systeem Microsoft Word of een converter')
    script = r'''
    $ErrorActionPreference = 'Stop'
    $word = $null
    $doc = $null
    try {
      $word = New-Object -ComObject Word.Application
      $word.Visible = $false
      $word.DisplayAlerts = 0
      $word.AutomationSecurity = 3
      $doc = $word.Documents.Open($env:CV_DOC_SOURCE, $false, $true, $false)
      $doc.SaveAs2($env:CV_DOC_DEST, 7)
    } finally {
      if ($null -ne $doc) { $doc.Close($false) }
      if ($null -ne $word) { $word.Quit() }
    }
    '''
    with tempfile.TemporaryDirectory(prefix='cv_doc_') as tmp:
        target = Path(tmp) / 'doc_text.txt'
        env = os.environ.copy()
        env['CV_DOC_SOURCE'] = str(path.resolve())
        env['CV_DOC_DEST'] = str(target)
        encoded = base64.b64encode(script.encode('utf-16le')).decode('ascii')
        proc = subprocess.run(['powershell.exe', '-NoProfile', '-NonInteractive',
                               '-EncodedCommand', encoded], capture_output=True,
                              text=True, errors='replace', timeout=65, env=env)
        if proc.returncode or not target.exists():
            raise RuntimeError('Oud .doc kan niet worden gelezen. Microsoft Word vereist; '
                               + (proc.stderr or 'Word-conversie mislukt')[-240:])
        return decode_office_text(target.read_bytes())


def read_problem_docx(path):
    """Recover mislabeled .docx without blindly assuming every file is ZIP."""
    head = path.open('rb').read(80)
    if head.lstrip().lower().startswith(b'{\\rtf'):
        from rtf_reader import rtf_to_text
        return rtf_to_text(path.read_text(encoding='latin-1', errors='replace')[:700000]), 'RTF-inhoud onder .docx-bestandsnaam'
    if head.lstrip().lower().startswith((b'<!doctype html', b'<html')):
        from html.parser import HTMLParser
        class Stripper(HTMLParser):
            def __init__(self):
                super().__init__()
                self.fragments = []
            def handle_data(self, data):
                self.fragments.append(data)
        parser = Stripper()
        parser.feed(path.read_text(encoding='utf-8', errors='replace')[:700000])
        return '\n'.join(parser.fragments), 'HTML-inhoud onder .docx-bestandsnaam'
    if head.startswith((b'\xff\xfe', b'\xfe\xff', b'\xef\xbb\xbf')):
        return decode_office_text(path.read_bytes()[:400000]), 'Tekstinhoud onder .docx-bestandsnaam'
    if not head.startswith((b'PK\x03\x04', b'PK\x05\x06', b'PK\x07\x08')):
        raw = path.read_bytes()[:400000]
        if raw and b'\x00' not in raw[:1000] and sum((32 <= b <= 126 or b in (9,10,13) or b >= 128) for b in raw[:1000]) / min(1000,len(raw)) > .97:
            return decode_office_text(raw), 'Tekstinhoud onder .docx-bestandsnaam'
    # Tiny / truncated ZIPs cannot hold the required Office XML files. Avoid
    # spending up to a minute launching Word for an unrecoverable stub.
    if head.startswith(b'PK') and path.stat().st_size < 256:
        raise ValueError('Beschadigd .docx-bestand: ZIP is te klein / onvolledig. '
                         'Controleer het origineel of herstel een eerdere versie.')
    # Legacy binary .doc misnamed .docx or damaged ZIP: Word or LibreOffice can
    # sometimes recover it; otherwise leave file in human-review errors.
    errors = []
    if sys.platform == 'darwin':
        # Mac: textutil leest oude Word (.doc), Word-XML en meer; geef het een kopie met de juiste extensie.
        kind = ('.doc' if head.startswith(OLE_MAGIC) else
                '.xml' if head.lstrip().startswith(b'<?xml') else '.docx')
        try:
            with tempfile.TemporaryDirectory(prefix='cv_docx_') as tmp:
                copy = Path(tmp) / ('document' + kind)
                shutil.copyfile(path, copy)
                text = textutil_extract(copy)
            if text.strip():
                return text, 'Gelezen met macOS (bestand was eigenlijk ' + {'.doc': 'een oud Word-bestand', '.xml': 'Word-XML'}.get(kind, 'afwijkend') + ')'
        except Exception as exc:
            errors.append(f'macOS: {str(exc)[:120]}')
    if sys.platform == 'win32':
        try:
            return doc_legacy_windows(path), 'Word-conversie van afwijkend .docx-formaat'
        except Exception as exc:
            errors.append(f'Word: {str(exc)[:150]}')
    try:
        return office_text_with_libreoffice(path), 'LibreOffice-conversie van afwijkend .docx-formaat'
    except Exception as exc:
        errors.append(f'LibreOffice: {str(exc)[:120]}')
    raise ValueError('Ongeldig of beschadigd .docx-bestand (geen geldig Word-ZIP); '
                     'niet veilig te lezen. ' + ' | '.join(errors))


OLE_MAGIC = b'\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1'   # oud Word (.doc) / Office 97-2003


def read_document(path, use_ocr=True, ocr_lang='eng', ocr_pages=5):
    ext = path.suffix.lower()
    try:
        with path.open('rb') as fh:
            head = fh.read(4096)
    except OSError:
        head = b''
    if head and not head.strip(b'\x00'):
        raise ValueError('Bestand is beschadigd: de inhoud bestaat alleen uit nullen '
                         '(waarschijnlijk niet goed gedownload of gekopieerd). Open het origineel om te controleren.')
    if head.startswith(OLE_MAGIC) and ext in ('.pdf', '.rtf', '.txt', '.odt'):
        # Oud Word-bestand met de verkeerde extensie ('cv.pdf' dat eigenlijk een .doc is).
        with tempfile.TemporaryDirectory(prefix='cv_doc_') as tmp:
            copy = Path(tmp) / 'document.doc'
            shutil.copyfile(path, copy)
            text, note, bonus = read_document(copy, use_ocr, ocr_lang, ocr_pages)
        return text, ('Eigenlijk een oud Word-bestand (.doc). ' + note).strip(), bonus
    # Kijk naar de echte inhoud: een PDF die '.docx' heet, is nog steeds een PDF.
    if ext != '.pdf':
        try:
            with path.open('rb') as fh:
                if fh.read(1024).lstrip().startswith(b'%PDF'):
                    ext = '.pdf'
        except OSError:
            pass
    if ext in {'.txt'}:
        raw = path.read_bytes()[:400000]
        for enc in ('utf-8-sig', 'utf-16', 'cp1252', 'latin-1'):
            try:
                return raw.decode(enc)[:180000], '', 0
            except UnicodeDecodeError:
                continue
        return raw.decode('utf-8', errors='replace')[:180000], '', 0
    if ext == '.pages':
        from pages_reader import pages_text
        try:
            text, preview = pages_text(path)
        except zipfile.BadZipFile:
            raise RuntimeError('Pages-bestand is beschadigd of met een wachtwoord beveiligd')
        text = re.sub(r'\n{3,}', '\n\n', text)
        if len(text.strip()) < 80 and preview:
            # Oud of afwijkend Pages-bestand: lees de ingebouwde pdf-voorvertoning.
            with tempfile.TemporaryDirectory(prefix='cv_pages_') as tmp:
                pdf = Path(tmp) / 'voorvertoning.pdf'
                pdf.write_bytes(preview)
                ptext, pnote, pages = read_document(pdf, use_ocr, ocr_lang, ocr_pages)
            return ptext, ('Gelezen via voorvertoning in het Pages-bestand. ' + pnote).strip(), pages
        if not text.strip():
            raise RuntimeError('Geen tekst in Pages-bestand (mogelijk met wachtwoord beveiligd)')
        return text[:180000], '', 0
    if ext in {'.doc', '.docx', '.rtf', '.odt'}:
        if ext == '.docx':
            try:
                text = docx_extract(path) + '\n' + docx_hyperlinks(path)
                note = ''
            except (zipfile.BadZipFile, KeyError, ET.ParseError, RuntimeError, ValueError):
                text, note = read_problem_docx(path)
            return text[:180000], note, 0
        elif ext == '.odt':
            text = odt_extract(path)
        elif ext == '.rtf':
            from rtf_reader import rtf_to_text
            text = rtf_to_text(path.read_text(encoding='latin-1', errors='replace')[:700000])
        elif sys.platform == 'darwin':
            text = textutil_extract(path)
        else:
            try:
                text = doc_legacy_windows(path)
            except Exception as word_exc:
                try:
                    text = office_text_with_libreoffice(path)
                except Exception:
                    raise word_exc
        return text[:180000], '', 0
    if ext == '.pdf':
        # pypdf is een pure-Python lezer die met de app wordt meegeleverd;
        # voorkomt incompatibele PyMuPDF-builds onder free-threaded Python.
        from pypdf import PdfReader
        reader = PdfReader(str(path), strict=False)
        if reader.is_encrypted:
            try:
                if reader.decrypt('') == 0:
                    raise ValueError('PDF is met een wachtwoord beveiligd')
            except Exception as exc:
                raise ValueError('PDF is met een wachtwoord beveiligd') from exc
        pages = []
        note = ''
        layout_bonus = 0
        ocr_possible = bool(use_ocr and shutil.which('tesseract') and shutil.which('pdftoppm'))
        big = path.stat().st_size > 30 * 1024 * 1024
        max_pages = 7 if big else 30           # groot bestand: een cv staat vooraan (eerste 7 pagina's)
        for i, page in enumerate(reader.pages):
            if i >= max_pages:
                note = f'Alleen eerste {max_pages} pagina’s onderzocht' + (' (groot bestand)' if big else '')
                break
            content = page.extract_text() or ''
            if i == 0 and len(content.strip()) > 120:
                # Opmaakafgeleide inschatting uit koppen en sectieverdeling.
                lines = [line.strip() for line in content.splitlines() if line.strip()]
                if lines and 3 <= len(lines) <= 250 and len(lines[0]) < 65:
                    layout_bonus += 2
                if sum(bool(re.match(r'(?i)^(werkervaring|opleiding|vaardigheden|skills|education|experience)\s*:?$', line))
                       for line in lines) >= 2:
                    layout_bonus += 3
            # Link-annotaties bevatten vaak LinkedIn, zelfs zonder zichtbare URL.
            try:
                for ref in page.annotations or []:
                    ann = ref.get_object()
                    action = ann.get('/A')
                    if action is not None:
                        action = action.get_object()
                        uri = action.get('/URI')
                        if uri: content += '\n' + str(uri)
            except (AttributeError, ValueError, KeyError):
                pass
            if len(content.strip()) < 45:
                if ocr_possible and i < ocr_pages:
                    # Poppler kan een PDF-pagina naar PNG converteren;
                    # Tesseract leest die PNG. Beiden zijn optioneel.
                    with tempfile.TemporaryDirectory(prefix='cv_pdf_ocr_') as tmp:
                        dest = str(Path(tmp) / 'page')
                        render = subprocess.run(
                            ['pdftoppm', '-f', str(i + 1), '-l', str(i + 1),
                             '-singlefile', '-scale-to', '1600', '-png', str(path), dest],
                            capture_output=True, timeout=75, check=False)
                        png = Path(dest + '.png')
                        if render.returncode == 0 and png.exists():
                            ocr = subprocess.run(['tesseract', str(png), 'stdout', '-l', ocr_lang],
                                                 capture_output=True, timeout=90, check=False)
                            if ocr.returncode == 0:
                                recognised = ocr.stdout.decode('utf-8', errors='replace')
                                if recognised.strip():
                                    content += '\n' + recognised
                                else:
                                    note = 'Geen tekst via OCR gevonden; controle aanbevolen'
                            else:
                                note = 'OCR gaf een fout; controle aanbevolen'
                        else:
                            note = 'PDF-rendering voor OCR gaf een fout; controle aanbevolen'
                else:
                    note = ('Een of meer pagina’s zijn een afbeelding (geen tekst); de rest is wel gelezen'
                            if sys.platform in ('win32', 'darwin') and use_ocr else
                            'Gescande PDF mogelijk; OCR vereist Tesseract én Poppler (pdftoppm) in PATH' if use_ocr else
                            'Gescande PDF mogelijk; OCR uitgeschakeld')
            pages.append(content)
            if sum(len(p) for p in pages) > 180000:
                break
        joined = '\n'.join(pages)
        # Geen Tesseract? Gebruik de ingebouwde OCR van Windows of macOS (geen installatie nodig).
        if use_ocr and not ocr_possible and len(joined.strip()) < 100 and sys.platform in ('win32', 'darwin'):
            try:
                recognised = native_ocr(path, ocr_pages)
                if recognised.strip():
                    joined = recognised
                    note = ('Gelezen met ingebouwde ' + ('Windows' if sys.platform == 'win32' else 'macOS') +
                            '-OCR (gescande pdf); controleer de tekst')
                else:
                    note = 'Gescande pdf: ingebouwde OCR vond geen tekst'
            except Exception as exc:
                note = f'Gescande pdf: ingebouwde OCR mislukt ({type(exc).__name__}: {str(exc)[:120]})'
        return joined[:180000], note, layout_bonus
    raise ValueError('Bestandstype niet ondersteund')


WINDOWS_OCR_PS = r'''
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
try {
Add-Type -AssemblyName System.Runtime.WindowsRuntime
$null = [Windows.Globalization.Language,Windows.Globalization,ContentType=WindowsRuntime]
$null = [Windows.Storage.StorageFile,Windows.Storage,ContentType=WindowsRuntime]
$null = [Windows.Data.Pdf.PdfDocument,Windows.Data.Pdf,ContentType=WindowsRuntime]
$null = [Windows.Media.Ocr.OcrEngine,Windows.Foundation,ContentType=WindowsRuntime]
$null = [Windows.Graphics.Imaging.BitmapDecoder,Windows.Graphics,ContentType=WindowsRuntime]
$null = [Windows.Storage.Streams.InMemoryRandomAccessStream,Windows.Storage.Streams,ContentType=WindowsRuntime]
$methods = [System.WindowsRuntimeSystemExtensions].GetMethods() | Where-Object { $_.Name -eq 'AsTask' -and $_.GetParameters().Count -eq 1 }
$asTaskOp = $methods | Where-Object { $_.GetParameters()[0].ParameterType.Name -eq 'IAsyncOperation`1' } | Select-Object -First 1
$asTaskAct = $methods | Where-Object { $_.GetParameters()[0].ParameterType.Name -eq 'IAsyncAction' } | Select-Object -First 1
function Await($op, [Type]$type) { $t = $asTaskOp.MakeGenericMethod($type).Invoke($null, @($op)); $t.Wait(-1) | Out-Null; $t.Result }
function AwaitAction($act) { $t = $asTaskAct.Invoke($null, @($act)); $t.Wait(-1) | Out-Null }
$engine = [Windows.Media.Ocr.OcrEngine]::TryCreateFromUserProfileLanguages()
if ($null -eq $engine) { $engine = [Windows.Media.Ocr.OcrEngine]::TryCreateFromLanguage([Windows.Globalization.Language]::new('en-US')) }
if ($null -eq $engine) { throw 'Geen OCR-taal beschikbaar in Windows' }
$file = Await ([Windows.Storage.StorageFile]::GetFileFromPathAsync($env:CV_OCR_PDF)) ([Windows.Storage.StorageFile])
$pdf = Await ([Windows.Data.Pdf.PdfDocument]::LoadFromFileAsync($file)) ([Windows.Data.Pdf.PdfDocument])
$max = [Math]::Min([int]$pdf.PageCount, [int]$env:CV_OCR_PAGES)
$sb = New-Object System.Text.StringBuilder
for ($i = 0; $i -lt $max; $i++) {
  $page = $pdf.GetPage([uint32]$i)
  $stream = New-Object Windows.Storage.Streams.InMemoryRandomAccessStream
  $opts = New-Object Windows.Data.Pdf.PdfPageRenderOptions
  $opts.DestinationWidth = 1700
  AwaitAction ($page.RenderToStreamAsync($stream, $opts))
  $stream.Seek(0)
  $decoder = Await ([Windows.Graphics.Imaging.BitmapDecoder]::CreateAsync($stream)) ([Windows.Graphics.Imaging.BitmapDecoder])
  $bitmap = Await ($decoder.GetSoftwareBitmapAsync()) ([Windows.Graphics.Imaging.SoftwareBitmap])
  $result = Await ($engine.RecognizeAsync($bitmap)) ([Windows.Media.Ocr.OcrResult])
  foreach ($line in $result.Lines) { [void]$sb.AppendLine($line.Text) }
}
[System.IO.File]::WriteAllText($env:CV_OCR_OUT, $sb.ToString(), [System.Text.Encoding]::UTF8)
} catch {
  $msg = 'OCRFOUT: ' + $_.Exception.Message + ' | regel ' + $_.InvocationInfo.ScriptLineNumber + ': ' + $_.InvocationInfo.Line.Trim()
  [System.IO.File]::WriteAllText($env:CV_OCR_OUT + '.err', $msg, [System.Text.Encoding]::UTF8)
  exit 3
}
'''

MAC_OCR_JXA = r'''
ObjC.import('Foundation'); ObjC.import('Quartz'); ObjC.import('Vision'); ObjC.import('AppKit');
function run(argv) {
  var doc = $.PDFDocument.alloc.initWithURL($.NSURL.fileURLWithPath(argv[0]));
  if (!doc || doc.isNil()) { throw 'PDF kon niet worden geopend'; }
  var n = Math.min(doc.pageCount, parseInt(argv[1]));
  var out = [];
  for (var i = 0; i < n; i++) {
    var img = doc.pageAtIndex(i).thumbnailOfSizeForBox($.NSMakeSize(1700, 2200), 0);
    var rep = $.NSBitmapImageRep.imageRepWithData(img.TIFFRepresentation);
    var cg = rep.CGImage;
    var req = $.VNRecognizeTextRequest.alloc.init;
    req.recognitionLevel = 0; req.usesLanguageCorrection = true;
    try { req.recognitionLanguages = $(['nl-NL', 'en-US']); } catch (e) {}
    var handler = $.VNImageRequestHandler.alloc.initWithCGImageOptions(cg, $.NSDictionary.dictionary);
    var err = $();
    handler.performRequestsError($([req]), err);
    var res = req.results;
    for (var j = 0; j < res.count; j++) {
      var cand = res.objectAtIndex(j).topCandidates(1);
      if (cand.count > 0) { out.push(ObjC.unwrap(cand.objectAtIndex(0).string)); }
    }
  }
  return out.join('\n');
}
'''


def native_ocr(path, max_pages=5, timeout=80):
    # Ingebouwde OCR: Windows.Media.Ocr (Windows 10/11) of Apple Vision (macOS). Geen installatie nodig.
    with tempfile.TemporaryDirectory(prefix='cv_ocr_') as tmp:
        if sys.platform == 'win32':
            target = Path(tmp) / 'ocr.txt'
            env = os.environ.copy()
            env.update(CV_OCR_PDF=str(Path(path).resolve()), CV_OCR_PAGES=str(max_pages), CV_OCR_OUT=str(target))
            encoded = base64.b64encode(WINDOWS_OCR_PS.encode('utf-16le')).decode('ascii')
            proc = subprocess.run(['powershell.exe', '-NoLogo', '-NoProfile', '-NonInteractive',
                                   '-ExecutionPolicy', 'Bypass', '-EncodedCommand', encoded],
                                  stdin=subprocess.DEVNULL, capture_output=True, text=True, errors='replace',
                                  timeout=timeout, env=env, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
            log = os.environ.get('CV_OCR_LOG')
            if log:
                try:
                    with open(log, 'a', encoding='utf-8') as fh:
                        fh.write(f'\n==== {dt.datetime.now().isoformat(timespec="seconds")} {path}\n'
                                 f'returncode={proc.returncode} uitvoer={target.exists() and target.stat().st_size}\n'
                                 f'STDERR:\n{(proc.stderr or "")[-4000:]}\n')
                        if Path(str(target) + '.err').exists():
                            fh.write('ERR-BESTAND: ' + Path(str(target) + '.err').read_text(encoding='utf-8-sig', errors='replace') + '\n')
                except OSError:
                    pass
            err_file = Path(str(target) + '.err')
            if err_file.exists():
                raise RuntimeError(err_file.read_text(encoding='utf-8-sig', errors='replace').strip()[:400])
            # PowerShell crasht soms pas bij het AFSLUITEN (na het lezen). Is de tekst
            # al weggeschreven, dan is de OCR gelukt en gebruiken we die gewoon.
            if target.exists() and target.stat().st_size > 0:
                return target.read_text(encoding='utf-8-sig', errors='replace')
            if proc.returncode or not target.exists():
                clean = re.sub(r'<[^>]+>', ' ', proc.stderr or '')
                clean = re.sub(r'#< CLIXML|Preparing modules for first use\.?|\s+', ' ', clean).strip()
                raise RuntimeError((clean or f'Windows-OCR stopte met code {proc.returncode}')[-300:])
            return target.read_text(encoding='utf-8-sig', errors='replace')
        if sys.platform == 'darwin':
            script = Path(tmp) / 'ocr.js'
            script.write_text(MAC_OCR_JXA, encoding='utf-8')
            proc = subprocess.run(['osascript', '-l', 'JavaScript', str(script), str(Path(path).resolve()), str(max_pages)],
                                  capture_output=True, text=True, errors='replace', timeout=timeout)
            if proc.returncode:
                raise RuntimeError((proc.stderr or 'macOS-OCR mislukt').strip()[-200:])
            return proc.stdout
    raise RuntimeError('Ingebouwde OCR alleen op Windows en macOS')



def fallback_pdf_via_poppler(path, timeout_seconds=60):
    """Try a different PDF engine if a reader crashes. Optional Poppler tool."""
    executable = shutil.which('pdftotext')
    if not executable:
        raise RuntimeError('Alternatieve PDF-lezer pdftotext niet geïnstalleerd')
    cmd = [executable, '-f', '1', '-l', '30', '-layout', '-enc', 'UTF-8', str(path), '-']
    proc = subprocess.run(cmd, capture_output=True,
                          timeout=timeout_seconds, check=False)
    if proc.returncode:
        raise RuntimeError('pdftotext kon deze PDF niet converteren: ' +
                           proc.stderr.decode('utf-8', errors='replace')[-150:])
    text = proc.stdout.decode('utf-8', errors='replace')[:180000]
    note = ('Alternatieve PDF-lezer gebruikt na leesfout' if text.strip() else
            'Geen tekst: mogelijk gescande PDF; controle/OCR nodig')
    return text, note, 0


_CRYPTO_OK = None


def crypto_usable():
    """Test eenmalig in een apart proces of 'cryptography' veilig laadt.

    pypdf gebruikt cryptography (nodig voor AES-beveiligde PDF's) zodra het
    geinstalleerd is. In sommige Python-builds (o.a. free-threaded 3.13t)
    crasht dat pakket bij het laden, waardoor ELKE PDF faalt. Werkt het,
    dan gebruiken we het; crasht het, dan schakelen we het uit.
    """
    global _CRYPTO_OK
    if _CRYPTO_OK is None and getattr(sys, 'frozen', False):
        try:   # in de .exe/.app: geen losse python beschikbaar, dus direct proberen
            from cryptography.hazmat.primitives.ciphers import Cipher  # noqa: F401
            _CRYPTO_OK = True
        except Exception:
            _CRYPTO_OK = True   # niet meegeleverd: pypdf valt zelf terug (zoals exit 1)
    if _CRYPTO_OK is None:
        try:
            probe = subprocess.run(
                [sys.executable, '-c',
                 'from cryptography.hazmat.primitives.ciphers import Cipher\n'
                 'from cryptography.hazmat.primitives import padding'],
                capture_output=True, timeout=60, check=False)
            # Exit 1 = niet geinstalleerd (pypdf valt dan zelf terug): ook veilig.
            _CRYPTO_OK = probe.returncode in (0, 1)
        except (OSError, subprocess.TimeoutExpired):
            _CRYPTO_OK = False
    return _CRYPTO_OK


def crypto_status_text():
    if crypto_usable():
        return ('CRYPTOGRAPHY: WERKT - alle PDF\'s worden gelezen, '
                'ook AES-beveiligde.')
    return ('CRYPTOGRAPHY: WERKT NIET in deze Python (' + sys.executable + '). '
            'Gewone PDF\'s worden wel gelezen; AES-beveiligde PDF\'s komen bij Leesfouten. '
            'Oplossing: gebruik de gewone Python, niet de free-threaded 3.13t.')


def looks_like_pdf(path):
    if path.suffix.lower() == '.pdf':
        return True
    try:
        with path.open('rb') as fh:
            return fh.read(1024).lstrip().startswith(b'%PDF')
    except OSError:
        return False


def safe_read_document(path, use_ocr, ocr_lang, ocr_pages, timeout_seconds=90):
    """Read each document in a separate Python process.

    A native failure in PDF, OCR or Word processing must not terminate the
    whole crawler. Communication stays strictly on the local machine.
    """
    if getattr(sys, 'frozen', False):
        cmd = [sys.executable, '--reader-worker', '--file', str(path), '--ocr-lang', ocr_lang,
               '--ocr-pages', str(ocr_pages)]
    else:
        worker = Path(__file__).resolve().parent / 'reader_worker.py'
        if not worker.is_file():
            raise RuntimeError('reader_worker.py ontbreekt: pak het volledige ZIP-bestand uit')
        cmd = [sys.executable, '-u', '-X', 'faulthandler', str(worker),
           '--file', str(path), '--ocr-lang', ocr_lang,
           '--ocr-pages', str(ocr_pages)]
    if not use_ocr:
        cmd.append('--no-ocr')
    try:
        env = os.environ.copy()
        if looks_like_pdf(path) and not crypto_usable():
            env['CV_BLOCK_CRYPTOGRAPHY'] = '1'
        result = subprocess.run(cmd, stdout=subprocess.PIPE,
                                stderr=subprocess.PIPE, timeout=timeout_seconds,
                                encoding='utf-8', errors='replace', check=False,
                                env=env)
    except subprocess.TimeoutExpired as exc:
        # Traag (vaak OCR van veel plaatjes): probeer snel alleen de tekst, zonder OCR.
        if looks_like_pdf(path):
            try:
                return fallback_pdf_via_poppler(path, timeout_seconds=60)
            except (OSError, RuntimeError, subprocess.TimeoutExpired):
                pass
        if use_ocr and not getattr(safe_read_document, '_retry', False):
            safe_read_document._retry = True
            try:
                text, note, bonus = safe_read_document(path, False, ocr_lang, ocr_pages, timeout_seconds=60)
                return text, ('Gelezen zonder OCR (bestand was te traag). ' + (note or '')).strip(), bonus
            except Exception:
                pass
            finally:
                safe_read_document._retry = False
        raise TimeoutError(f'Bestand lezen duurde langer dan {timeout_seconds} seconden') from exc
    if result.returncode != 0:
        if looks_like_pdf(path):
            try:
                return fallback_pdf_via_poppler(path, timeout_seconds=min(timeout_seconds, 60))
            except (OSError, RuntimeError, subprocess.TimeoutExpired):
                pass  # Keep detailed original error for review and a later retry.
        # Windows crash exit statuses may be unsigned 32-bit ints.
        status = result.returncode & 0xffffffff
        diagnosis = (' (Windows-toegangsfout 0xC0000005)' if status == 0xC0000005 else '')
        details = (result.stderr or '').strip().splitlines()
        tail = ' | '.join(details[-3:])[:250] if details else 'Geen verdere foutdetails'
        raise RuntimeError(f'Leesproces stopte met code {result.returncode}'
                           f'{diagnosis}: {tail}' +
                           (' | Alternatieve PDF-lezer pdftotext niet gevonden of niet bruikbaar; handmatige controle nodig'
                            if looks_like_pdf(path) else ''))
    try:
        result_obj = json.loads(result.stdout)
        if (not isinstance(result_obj, list) or len(result_obj) != 3 or
            not isinstance(result_obj[0], str) or
            not isinstance(result_obj[1], str) or
            not isinstance(result_obj[2], int)):
            raise ValueError('onjuist formaat')
        return tuple(result_obj)
    except (ValueError, TypeError) as exc:
        raise RuntimeError('Leesproces gaf geen geldige documenttekst terug') from exc


def copy_document(src, target):
    """Kopie naar het archief. Een Pages-pakket (map) wordt één .pages-bestand (zip), zoals Pages zelf doet."""
    if src.is_dir():
        with zipfile.ZipFile(target, 'w', zipfile.ZIP_DEFLATED) as z:
            for f in sorted(x for x in src.rglob('*') if x.is_file()):
                rel = str(f.relative_to(src)).replace(os.sep, '/')
                if rel == 'Index.zip':          # nieuwere pakketten: inhoud van Index.zip uitpakken
                    with zipfile.ZipFile(f) as inner:
                        for name in inner.namelist():
                            z.writestr(name, inner.read(name))
                else:
                    z.write(f, rel)
    else:
        shutil.copy2(src, target)


def hash_file(path):
    h = hashlib.sha256()
    if path.is_dir():                     # Pages-document dat als map (pakket) is opgeslagen
        for f in sorted(x for x in path.rglob('*') if x.is_file()):
            h.update(str(f.relative_to(path)).encode('utf-8'))
            h.update(f.read_bytes())
        return h.hexdigest()
    with path.open('rb') as f:
        for piece in iter(lambda: f.read(1048576), b''):
            h.update(piece)
    return h.hexdigest()


def db_open(out):
    out.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(out / 'cv_index.sqlite')
    db.row_factory = sqlite3.Row
    db.execute('PRAGMA journal_mode=WAL')
    db.execute('''CREATE TABLE IF NOT EXISTS docs (
      path TEXT PRIMARY KEY, mtime_ns INTEGER, size INTEGER, sha TEXT, ext TEXT,
      status TEXT, score INTEGER, reason TEXT, note TEXT, name TEXT,
      emails TEXT, phones TEXT, links TEXT, snippet TEXT, tokens TEXT,
      year TEXT, employer TEXT, role TEXT, archive TEXT, error TEXT)''')
    current = {row[1] for row in db.execute('PRAGMA table_info(docs)')}
    if 'classifier_version' not in current:
        db.execute('ALTER TABLE docs ADD COLUMN classifier_version INTEGER NOT NULL DEFAULT 0')
    if 'name_verified' not in current:
        db.execute('ALTER TABLE docs ADD COLUMN name_verified INTEGER NOT NULL DEFAULT 0')
    if 'stage' not in current:
        db.execute("ALTER TABLE docs ADD COLUMN stage TEXT NOT NULL DEFAULT ''")
    if 'gezien_ns' not in current:
        db.execute('ALTER TABLE docs ADD COLUMN gezien_ns INTEGER NOT NULL DEFAULT 0')
    if 'werkgevers' not in current:
        db.execute("ALTER TABLE docs ADD COLUMN werkgevers TEXT NOT NULL DEFAULT ''")
    if 'opleidingen' not in current:
        db.execute("ALTER TABLE docs ADD COLUMN opleidingen TEXT NOT NULL DEFAULT ''")
    if 'werkplaatsen' not in current:
        db.execute("ALTER TABLE docs ADD COLUMN werkplaatsen TEXT NOT NULL DEFAULT ''")
    if 'woonplaats' not in current:
        db.execute("ALTER TABLE docs ADD COLUMN woonplaats TEXT NOT NULL DEFAULT ''")
    if 'bijbanen' not in current:
        db.execute("ALTER TABLE docs ADD COLUMN bijbanen TEXT NOT NULL DEFAULT ''")
    if 'received_ns' not in current:
        db.execute('ALTER TABLE docs ADD COLUMN received_ns INTEGER NOT NULL DEFAULT 0')
    if 'websites' not in current:
        db.execute("ALTER TABLE docs ADD COLUMN websites TEXT NOT NULL DEFAULT '[]'")
    if 'leverancier' not in current:
        db.execute("ALTER TABLE docs ADD COLUMN leverancier TEXT NOT NULL DEFAULT ''")
    if 'taal' not in current:
        db.execute("ALTER TABLE docs ADD COLUMN taal TEXT NOT NULL DEFAULT ''")
    db.execute('CREATE INDEX IF NOT EXISTS docs_sha ON docs(sha)')
    # Eigen API-sleutels mogen nooit in de index of Excel staan (oude versies lazen ze soms mee).
    db.execute("DELETE FROM docs WHERE path LIKE '%_sleutel.txt' OR path LIKE '%cv-scanner-sleutels%' OR path LIKE '%.cv-scanner-windows%' "
               "OR path LIKE '%CV_Crawler_Resultaten%.pdf'")
    db.commit()
    return db



def cloud_placeholder(stat_result):
    """Windows cloud-files that are still remote; reading would trigger on-demand download.

    Not every reparse point is cloud-only, so check recall/offline flags only.
    All other platforms safely return False.
    """
    if getattr(stat_result, 'st_flags', 0) & 0x40000000:     # macOS SF_DATALESS: alleen in iCloud
        return True
    flags = getattr(stat_result, 'st_file_attributes', 0)
    FILE_ATTRIBUTE_OFFLINE = 0x00001000
    FILE_ATTRIBUTE_RECALL_ON_OPEN = 0x00040000
    FILE_ATTRIBUTE_RECALL_ON_DATA_ACCESS = 0x00400000
    return bool(flags & (FILE_ATTRIBUTE_OFFLINE | FILE_ATTRIBUTE_RECALL_ON_OPEN |
                         FILE_ATTRIBUTE_RECALL_ON_DATA_ACCESS))


def error_category(details):
    """Human-readable classes; do not confuse all review points with actual read errors."""
    t = str(details).casefold()
    if '0xc0000005' in t or '3221225477' in t:
        return 'Lezer gecrasht (Windows 0xC0000005)'
    if 'timed out' in t or 'timeout' in t or 'duurde langer' in t:
        return 'Leestijd overschreden'
    if 'invalid argument' in t or 'winerror 87' in t or 'errno 22' in t:
        return 'Ongeldige bestands-/toegangsoperatie'
    if 'badzipfile' in t or 'not a zip file' in t or 'ongeldig of beschadigd .docx' in t:
        return 'Ongeldig/beschadigd Word-bestand'
    if 'unicode' in t or 'codec' in t or 'bom' in t:
        return 'Tekstcodering niet herkend'
    if 'permission' in t or 'access is denied' in t or 'toegang geweigerd' in t or 'winerror 5' in t:
        return 'Geen leestoegang'
    if 'wachtwoord' in t or 'encrypted' in t:
        return 'Wachtwoordbeveiligd document'
    if 'no such file' in t or 'file not found' in t or 'winerror 2' in t:
        return 'Bestand verplaatst/verwijderd tijdens scan'
    return 'Overige leesfout'


def lightweight_pdf_warning(file_path, stat_result):
    """Informative only; large PDFs are still tried, never silently discarded."""
    if file_path.suffix.lower() == '.pdf' and stat_result.st_size > 18 * 1024 * 1024:
        return 'Grote PDF; tekstlezer heeft een maximale verwerkingstijd'
    return ''

def windows_roots(include_extra=False):
    """Alle interne schijven. USB-/netwerkschijven alleen als aangevinkt."""
    if sys.platform != 'win32':
        return []
    try:
        import ctypes
        kernel = ctypes.windll.kernel32
        kernel.GetDriveTypeW.argtypes = [ctypes.c_wchar_p]
        kernel.GetDriveTypeW.restype = ctypes.c_uint
        mask = kernel.GetLogicalDrives()
        paths = []
        for number in range(26):
            if mask & (1 << number):
                drive = chr(ord('A') + number) + ':\\'
                kind = kernel.GetDriveTypeW(drive)
                if kind == 3 or (include_extra and kind in (2, 4)):
                    paths.append(Path(drive))
        return paths
    except (AttributeError, OSError):
        return [Path.home()]


def paths_to_scan(args):
    home = Path.home()
    if args.root:
        roots = [Path(p).expanduser() for p in args.root]
    elif sys.platform == 'win32':
        roots = windows_roots(args.include_volumes)
        # Ook OneDrive mappen onder een afwijkende schijfletter/locatie.
        roots += [Path(os.environ[k]) for k in ('OneDrive', 'OneDriveCommercial', 'OneDriveConsumer')
                  if os.environ.get(k)]
        roots.append(home)
    else:
        roots = [home, home / 'Library' / 'Mobile Documents', home / 'Library' / 'CloudStorage', Path('/Users/Shared')]
        if args.include_volumes:
            roots.append(Path('/Volumes'))
    result = []
    for p in roots:
        if p.exists():
            resolved = p.resolve()
            if not any(resolved == earlier or earlier in resolved.parents for earlier in result):
                result.append(resolved)
    return result


SKIPPED_SPECIAL = {'icloud': [], 'pages_map': []}


def _onedrive_pin_download(path, wait=120):
    """Windows: OneDrive blokkeert soms dat een programma een 'alleen online'-bestand zelf downloadt
    (Errno 22). Dan vragen we OneDrive het bestand op te halen door het tijdelijk vast te pinnen
    (attrib +P = 'Altijd behouden op dit apparaat'), wachten tot het lokaal staat en zetten het
    vastpinnen daarna weer uit (het bestand blijft gewoon in OneDrive)."""
    if sys.platform != 'win32':
        return False
    flags = getattr(subprocess, 'CREATE_NO_WINDOW', 0)
    try:
        subprocess.run(['attrib', '+P', '-U', str(path)], capture_output=True, timeout=30, creationflags=flags)
    except (OSError, subprocess.TimeoutExpired):
        return False
    ok = False
    end = time.time() + wait
    while time.time() < end:
        try:
            if not cloud_placeholder(os.stat(path)):
                hash_file(path)
                ok = True
                break
        except OSError:
            pass
        time.sleep(1)
    try:
        subprocess.run(['attrib', '-P', str(path)], capture_output=True, timeout=30, creationflags=flags)
    except (OSError, subprocess.TimeoutExpired):
        pass
    return ok


def download_from_cloud(path, stat, args, summary):
    """Bestand staat alleen online (OneDrive/iCloud/Google Drive/Dropbox): download het door het
    te lezen. Windows en macOS halen het dan zelf binnen. Geeft True als het nu lokaal leesbaar is."""
    if not getattr(args, 'cloud_download', False) or stat.st_size > args.max_mb * 1024 * 1024:
        return False
    print(f'DOWNLOADEN (stond alleen online): {path}', flush=True)
    try:
        hash_file(path)                  # volledig lezen = downloaden
    except OSError as exc:
        if _onedrive_pin_download(path):
            print('  gedownload via OneDrive (tijdelijk vastgezet en weer losgemaakt)', flush=True)
            summary['gedownload_uit_cloud'] += 1
            return True
        print(f'  downloaden mislukt ({type(exc).__name__}: {str(exc)[:80]}). Mogelijke oorzaak: OneDrive staat '
              'uit of niet ingelogd, geen internet, of Windows blokkeert automatische downloads '
              '(Instellingen > Privacy > Automatische bestandsdownloads).', flush=True)
        return False
    summary['gedownload_uit_cloud'] += 1
    return True


def icloud_fetch(real, wait=90):
    """macOS: '.CV.pdf.icloud' is een iCloud-plaatshouder. Laat iCloud het echte bestand downloaden."""
    if sys.platform != 'darwin' or os.environ.get('CV_CLOUD_DOWNLOAD') != '1':
        return False
    try:
        subprocess.run(['brctl', 'download', str(real)], capture_output=True, timeout=30)
    except (OSError, subprocess.TimeoutExpired):
        return False
    end = time.time() + wait
    while time.time() < end:
        if real.exists():
            return True
        time.sleep(0.5)
    return False


def mac_access_check():
    """macOS blokkeert Bureaublad/Documenten/Downloads/iCloud tot je toestemming geeft."""
    if sys.platform != 'darwin':
        return []
    home = Path.home()
    blocked = []
    for sub in ('Desktop', 'Documents', 'Downloads', 'Library/Mobile Documents'):
        d = home / sub
        if d.exists():
            try:
                os.listdir(d)
            except PermissionError:
                blocked.append(str(d))
    return blocked


def report_skipped_special():
    icloud = sorted(set(SKIPPED_SPECIAL['icloud']))
    if icloud:
        print(f'ICLOUD: {len(icloud)} document(en) staan alleen in iCloud en zijn niet gelezen. '
              'Vink "Ook cv\'s die alleen in OneDrive/iCloud staan" aan en scan opnieuw:', flush=True)
        for f in icloud[:30]:
            print('  ' + f, flush=True)
    SKIPPED_SPECIAL['icloud'].clear()
    SKIPPED_SPECIAL['pages_map'].clear()


def iter_files(roots, out, deep=False):
    visited = set()
    out = out.resolve()
    cvdir = cv_folder(out).resolve()
    naamloosdir = naamloze_cv_folder(out).resolve()
    roots = list(roots) + ([cvdir] if cvdir.exists() else [])   # eigen cv-map altijd meenemen
    def excluded(d):
        r = d.resolve()
        in_out = r == out or out in r.parents
        in_cv = r == cvdir or cvdir in r.parents
        in_naamloos = r == naamloosdir or naamloosdir in r.parents
        return in_naamloos or (in_out and not in_cv)
    for root in roots:
        if excluded(root):
            continue
        if root.is_file():
            if root.suffix.lower() in (SUPPORTED | ARCHIVES) and root != out:
                yield root
            continue
        for base, dirs, names in os.walk(root, topdown=True, followlinks=False):
            parent = Path(base)
            for d in sorted(d for d in dirs if d.lower().endswith('.pages')):
                b, key = parent / d, str(parent / d)
                looks_pages = any((b / n).exists() for n in ('Index.zip', 'Index', 'index.xml', 'index.xml.gz'))
                if looks_pages and key not in visited:
                    visited.add(key)
                    yield b
            dirs[:] = sorted(d for d in dirs if d not in SKIP_DIRS and not d.lower().endswith('.pages')
                             and d not in ('.cv-scanner-windows', '.cv-scanner')   # API-sleutels: nooit lezen
                             and (deep or not d.startswith('.'))
                             and (deep or d not in {'AppData', 'WindowsApps'})
                             and (deep or not (d == 'Library' and parent == Path.home()))
                             and not (parent / d).is_symlink()
                             and not (getattr((parent / d), 'is_junction', lambda: False)())
                             and not excluded(parent / d))
            for fname in sorted(names):
                p = parent / fname
                if fname.startswith('.') and fname.endswith('.icloud') and \
                        Path(fname[1:-7]).suffix.lower() in (SUPPORTED | ARCHIVES):
                    real = parent / fname[1:-7]
                    if str(real) in visited or real.exists():
                        continue          # al gedownload, staat ook in names
                    print(f'DOWNLOADEN (stond alleen in iCloud): {real}', flush=True)
                    if icloud_fetch(real):
                        visited.add(str(real))
                        yield real
                    else:
                        SKIPPED_SPECIAL['icloud'].append(str(real))
                    continue
                if fname.lower().endswith('_sleutel.txt') or fname.lower() == 'cv-scanner-sleutels.txt':
                    continue                  # API-sleutel van de scanner zelf
                if fname.lower().startswith(('cv_crawler_resultaten', 'cv-scanner-')):
                    continue                  # eigen resultaat (Excel/pdf-export) of download van de scanner zelf
                if (p.suffix.lower() not in (SUPPORTED | ARCHIVES) or p.is_symlink()
                    or fname.startswith('~$') or fname.startswith('._')):
                    continue
                key = str(p)
                if key not in visited:
                    visited.add(key)
                    yield p


def pick_lang():
    if not shutil.which('tesseract'):
        return 'eng'
    try:
        p = subprocess.run(['tesseract', '--list-langs'], capture_output=True, text=True, timeout=5)
        langs = p.stdout + p.stderr
        return 'nld+eng' if 'nld' in langs and 'eng' in langs else ('nld' if 'nld' in langs else 'eng')
    except Exception:
        return 'eng'


def year_from(text, filename):
    vals = re.findall(r'\b(?:19\d\d|20[0-3]\d)\b', filename)
    if vals:
        return max(vals)
    # Niet uit werkervaring gokken dat dit het CV-jaar is.
    return ''


def field_value(text, labels):
    for line in text.splitlines()[:100]:
        for label in labels:
            m = re.match(r'^\s*' + label + r'\s*:\s*(.{2,80})\s*$', line, re.I)
            if m:
                return m.group(1).strip()
    return ''


def received_ns(stat):
    """Moment dat het bestand op deze pc kwam (Windows: 'gemaakt'-datum)."""
    birth = getattr(stat, 'st_birthtime_ns', None)
    if birth is None and sys.platform == 'win32':
        birth = stat.st_ctime_ns
    return int(birth or 0)


def newest_key(d):
    """Nieuwste versie = laatst gewijzigd OF laatst op de pc gekomen (de recentste van de twee)."""
    keys = d.keys() if hasattr(d, 'keys') else d
    rec = d['received_ns'] if 'received_ns' in keys else 0
    return max(d['mtime_ns'] or 0, rec or 0)


def write_doc(db, data):
    names = ','.join(data)
    placeholders = ','.join('?' for _ in data)
    columns = ','.join(f'{k}=excluded.{k}' for k in data if k != 'path')
    db.execute(f'INSERT INTO docs ({names}) VALUES ({placeholders}) ON CONFLICT(path) DO UPDATE SET {columns}', tuple(data.values()))


LAST_SEEN = set()


def ingest(args, db, out, roots=None, label=None):
    roots = roots if roots is not None else paths_to_scan(args)
    if not roots:
        raise RuntimeError('Geen bestaande mappen opgegeven')
    print(label or ('Zoekt in: ' + ', '.join(map(str, roots))), flush=True)
    if not label and getattr(args, 'force', False):
        print('Vorige sessie telt niet mee: elk bestand wordt opnieuw bekeken (geen hergebruik van Excel of eerdere scan).',
              flush=True)
    for d in mac_access_check():
        print('TOEGANG GEWEIGERD: ' + d, flush=True)
    print(crypto_status_text(), flush=True)
    print('OCR:', 'uitgeschakeld' if args.no_ocr else
          'aan (Tesseract en Poppler gevonden)' if shutil.which('tesseract') and shutil.which('pdftoppm')
          else 'aan (ingebouwde Windows-OCR, geen installatie nodig)' if sys.platform == 'win32'
          else 'aan (ingebouwde macOS-OCR, geen installatie nodig)' if sys.platform == 'darwin'
          else 'niet beschikbaar (Tesseract plus Poppler nodig)', flush=True)
    summary = collections.Counter()
    lang = pick_lang()
    archive = out / 'cv_archief'
    archive.mkdir(exist_ok=True)
    seen_paths = LAST_SEEN
    scan_start = time.time_ns()
    os.environ['CV_OCR_LOG'] = str(out / 'ocr_log.txt')
    ocr_on = not getattr(args, 'no_ocr', False) and (sys.platform in ('win32', 'darwin') or bool(shutil.which('tesseract')))
    ocr_file = out / 'ocr_geprobeerd.json'
    try:
        ocr_tried = set(json.loads(ocr_file.read_text(encoding='utf-8')))
    except (OSError, ValueError):
        ocr_tried = set()
    if not label:
        print('Bestanden tellen…', flush=True)
        print(f'TOTAAL: {sum(1 for _ in iter_files(roots, out, deep=args.deep))}', flush=True)
    for p in iter_files(roots, out, deep=args.deep):
        summary['gevonden_ondersteund'] += 1
        if not label:
            print(f'BEZIG: {p}', flush=True)
        seen_paths.add(str(p))
        try:
            stat = p.stat()
            if p.suffix.lower() in ARCHIVES:
                ingest_zip(p, stat, args, db, archive, lang, summary, seen_paths)
                continue
            saved = db.execute('SELECT mtime_ns,size,status,classifier_version,reason,name FROM docs WHERE path=?', (str(p),)).fetchone()
            ocr_key = f'{p}|{stat.st_mtime_ns}'
            note_now = (db.execute('SELECT note FROM docs WHERE path=?', (str(p),)).fetchone() or [''])[0] or ''
            retry_ocr = bool(saved and ocr_on and saved['status'] == 'other'
                             and 'geen leesbare tekst' in (saved['reason'] or '')
                             and (ocr_key not in ocr_tried or 'OCR mislukt' in note_now))
            if retry_ocr:
                ocr_tried.add(ocr_key)     # gescande pdf die eerder zonder OCR is gelezen: één keer opnieuw mét OCR
            if (not retry_ocr and saved and saved['mtime_ns'] == stat.st_mtime_ns and
                saved['size'] == stat.st_size and saved['classifier_version'] == CLASSIFIER_VERSION and saved['status'] not in ('error', 'deferred')
                and not args.force):
                summary['hergebruikt'] += 1
                continue
            if (not retry_ocr and saved and saved['mtime_ns'] == stat.st_mtime_ns and saved['size'] == stat.st_size
                    and saved['status'] == 'other' and not maybe_cv(saved['reason'])
                    and (saved['classifier_version'] or 0) >= KEEP_FROM_VERSION
                    and not args.force):
                # Nieuwere herkenning, maar dit document was al zeker cv of zeker geen cv: niet opnieuw lezen.
                # (Een cv-naam wordt wel opgeschoond: 'Naam Jan de Vries' -> 'Jan de Vries'.)
                db.execute('UPDATE docs SET classifier_version=?, name=? WHERE path=?',
                           (CLASSIFIER_VERSION, keep_cv_name(saved['name'], saved['reason']) if saved['status'] == 'cv' else saved['name'], str(p)))
                summary['hergebruikt'] += 1
                continue
            deferred_reason = ''
            if cloud_placeholder(stat) and download_from_cloud(p, stat, args, summary):
                stat = p.stat()
            elif cloud_placeholder(stat):
                deferred_reason = ('Staat alleen online (OneDrive/iCloud). Vink "Ook cv\'s die alleen in OneDrive/iCloud staan" aan '
                                   'en scan opnieuw om het te downloaden en te lezen.')
            elif stat.st_size > limiet_mb(p.suffix, args) * 1024*1024:
                # Grote pdf's (tot 500 MB) worden wél gelezen, maar alleen de eerste pagina's (zie read_document).
                deferred_reason = (f'Bestand groter dan scanlimiet ({args.max_mb} MB); '
                                   'niet gelezen, handmatig beoordelen.')
                summary['te_groot'] += 1
            if deferred_reason:
                # Existing CV data is kept if a formerly local file becomes cloud-only.
                if saved and saved['status'] == 'cv':
                    summary['behouden_cv'] += 1
                else:
                    write_doc(db, dict(path=str(p),mtime_ns=stat.st_mtime_ns,size=stat.st_size,
                        sha='',ext=p.suffix.lower(),status='deferred',score=0,reason='',
                        note=deferred_reason,name='',emails='[]',phones='[]',links='[]',
                        snippet='',tokens='[]',year='',employer='',role='',archive='',error='',
                        classifier_version=CLASSIFIER_VERSION,name_verified=0))
                    summary['uitgesteld'] += 1
                if summary['gevonden_ondersteund'] % 25 == 0:
                    db.commit()
                continue
            sha = hash_file(p)
            # Identieke bestanden op verschillende plekken hoeven niet nogmaals
            # te worden geparsed; dezelfde SHA256 is de sterkste overeenkomst.
            duplicate = db.execute("SELECT * FROM docs WHERE sha=? AND status IN ('cv','other','review') AND classifier_version=? AND NOT (status='other' AND reason LIKE '%geen leesbare tekst%') LIMIT 1",
                                   (sha, CLASSIFIER_VERSION)).fetchone()
            if duplicate and not args.force:
                data = {key: duplicate[key] for key in duplicate.keys()}
                data.update(path=str(p),mtime_ns=stat.st_mtime_ns,size=stat.st_size,received_ns=received_ns(stat),
                            ext=p.suffix.lower(),year=year_from('',p.stem) or duplicate['year'])
                write_doc(db,data)
                summary[duplicate['status']] += 1
                summary['exacte_kopie'] += 1
            else:
                # Per-file subprocess is essential: a crashed PDF can't bring down the scan.
                text, note, layout = safe_read_document(
                    p, not args.no_ocr, lang, args.ocr_pages,
                    timeout_seconds=args.reader_timeout)
                note = ' | '.join(t for t in (note, lightweight_pdf_warning(p,stat)) if t)
                emails, phones, links = emails_in(text), phones_in(text), linkedins_in(text)
                name = guess_name(text, p.stem, emails, links)
                score, reason = classify(text, p.stem, name, emails, phones, links, layout, source_path=str(p))
                status = decide_status(text, p.stem, name, emails, phones, links, score, args.threshold,
                                       source_path=str(p))
                if p.suffix.lower() == '.pdf' and not text.strip():
                    status = 'other'   # gescand en ook OCR vond geen tekst: geen cv
                dst = ''
                if status == 'cv':
                    target = archive / f'{sha}{p.suffix.lower()}'
                    if not target.exists():
                        copy_document(p, target)
                    dst = str(target)
                data = dict(path=str(p), mtime_ns=stat.st_mtime_ns, size=stat.st_size, sha=sha, ext=p.suffix.lower(),
                            received_ns=received_ns(stat),
                            status=status, score=score, reason=reason, note=note, name=name,
                            emails=json.dumps(emails, ensure_ascii=False), phones=json.dumps(phones),
                            links=json.dumps(links), snippet=normalized_snippet(text),
                            tokens=json.dumps(signature(text)), year=year_from(text,p.stem),
                            employer=field_value(text,['werkgever','huidige werkgever','employer','company']) or current_job(text)[1],
                            role=field_value(text,['functie','huidige functie','job title','position']) or current_job(text)[0],
                            websites=json.dumps(websites_in(text)), stage=stage_from(text), bijbanen=bijbanen_from(text), woonplaats=woonplaats_from(text), werkplaatsen=werkplaatsen_from(text), opleidingen=opleidingen_from(text), werkgevers=werkgevers_from(text), leverancier=leverancier_from(text), taal=cv_hoofdtaal(text) or '-',
                            archive=dst, error='', classifier_version=CLASSIFIER_VERSION,
                            name_verified=int(bool(name) and status == 'cv'))
                write_doc(db, data)
                summary[status] += 1
                if status == 'cv':
                    print(f'CV GEVONDEN: {name or "(naam onbekend)"} – {p.name}', flush=True)
        except Exception as exc:
            summary['fouten'] += 1
            print(f'LEESFOUT bij {p.name}: {type(exc).__name__}: {str(exc)[:300]}', flush=True)
            try:
                st = p.stat()
                write_doc(db, dict(path=str(p), mtime_ns=st.st_mtime_ns,size=st.st_size,sha='',ext=p.suffix.lower(),
                      status='error', score=0, reason='', note='', name='', emails='[]',phones='[]',
                      links='[]', snippet='', tokens='[]', year='',employer='',role='',archive='',
                      error=f'{type(exc).__name__}: {str(exc)[:350]}',
                      classifier_version=CLASSIFIER_VERSION, name_verified=0))
            except Exception:
                pass
        if summary['gevonden_ondersteund'] % 25 == 0:
            db.commit()
            if label:
                print(f"  herlezen: {summary['gevonden_ondersteund']} bestanden", flush=True)
            else:
                print(f"  {summary['gevonden_ondersteund']} documenten gezien | {summary['cv']} CV's | {summary['review']} twijfel | {summary['fouten']} fouten", flush=True)
    db.commit()
    # Opruimen: bestanden binnen de gescande mappen die niet meer bestaan
    # (verplaatst/verwijderd) uit de database halen. Alleen na een volledige scan.
    prefixes = [os.path.normcase(str(r)).rstrip('\\/') + os.sep for r in roots if r.is_dir()]
    stale = [row['path'] for row in db.execute('SELECT path FROM docs')
             if row['path'] not in seen_paths
             and any(os.path.normcase(row['path']).startswith(pre) for pre in prefixes)]
    for start in range(0, len(stale), 500):
        chunk = stale[start:start+500]
        db.execute(f"DELETE FROM docs WHERE path IN ({','.join('?' for _ in chunk)})", chunk)
    db.commit()
    if stale:
        summary['opgeruimd_verplaatst_of_verwijderd'] = len(stale)
        print(f'Opgeruimd: {len(stale)} oude regels van bestanden die niet meer op die plek staan.', flush=True)
    if label:
        # Tweede ronde (oude leesfouten opnieuw): eigen regel, zodat de teller
        # 'Bestanden gezien' van de hoofdscan niet wordt overschreven.
        print('Opnieuw gelezen:', dict(summary), flush=True)
    else:
        print('Scan afgerond:', dict(summary), flush=True)
        report_skipped_special()
        # Onthoud wat DEZE scan heeft gezien, zodat 'Twijfelgevallen' alleen deze scan toont.
        try:
            for sp in list(seen_paths):
                prefix = sp + ZIP_SEP
                db.execute('UPDATE docs SET gezien_ns=? WHERE path=? OR substr(path, 1, ?)=?',
                           (scan_start, sp, len(prefix), prefix))
            db.commit()
            (out / 'laatste_scan.json').write_text(json.dumps({'start_ns': scan_start, 'scope': list(SCOPE)}),
                                                   encoding='utf-8')
        except (sqlite3.Error, OSError) as exc:
            print('WAARSCHUWING: scanmoment niet opgeslagen:', exc, flush=True)
        print(crypto_status_text(), flush=True)
    try:
        ocr_file.write_text(json.dumps(sorted(ocr_tried)), encoding='utf-8')
    except OSError:
        pass
    return summary


def _error_row(path_str, stat, ext, message):
    return dict(path=path_str, mtime_ns=stat.st_mtime_ns, size=stat.st_size, sha='', ext=ext,
                status='error', score=0, reason='', note='', name='', emails='[]', phones='[]',
                links='[]', snippet='', tokens='[]', year='', employer='', role='', archive='',
                error=message[:400], classifier_version=CLASSIFIER_VERSION, name_verified=0)


def ingest_zip(zpath, zstat, args, db, archive, lang, summary, seen_paths):
    """Lees documenten IN een zip. Het zip-bestand zelf wordt alleen gelezen:
    niets uitpakken in de map, niets wijzigen, niets verwijderen. Elk document
    gaat via een tijdelijke kopie door dezelfde lezer/herkenning als losse
    bestanden. Regels krijgen de datum/grootte van de zip, zodat een
    ongewijzigde zip bij een volgende scan wordt hergebruikt."""
    prefix = str(zpath) + ZIP_SEP
    like = prefix.replace('\\', '\\\\').replace('%', '\\%').replace('_', '\\_') + '%'
    old = db.execute("SELECT path,mtime_ns,size,status,classifier_version,reason,name FROM docs WHERE path LIKE ? ESCAPE '\\'",
                     (like,)).fetchall()
    if (old and not args.force and all(
            r['mtime_ns'] == zstat.st_mtime_ns and r['size'] == zstat.st_size
            and ((r['classifier_version'] == CLASSIFIER_VERSION
                  and r['status'] not in ('error', 'deferred'))
                 or ((r['classifier_version'] or 0) >= KEEP_FROM_VERSION
                     and r['status'] == 'other' and not maybe_cv(r['reason'])))
            for r in old)):
        for r in old:
            seen_paths.add(r['path'])
            db.execute('UPDATE docs SET classifier_version=?, name=? WHERE path=?',
                       (CLASSIFIER_VERSION, keep_cv_name(r['name'], r['reason']) if r['status'] == 'cv' else r['name'], r['path']))
        summary['hergebruikt'] += len(old)
        return
    for r in old:   # zip is veranderd: oude regels van deze zip vervangen
        db.execute('DELETE FROM docs WHERE path=?', (r['path'],))
    if cloud_placeholder(zstat) and download_from_cloud(zpath, zstat, args, summary):
        zstat = zpath.stat()
    elif cloud_placeholder(zstat):
        summary['uitgesteld'] += 1
        write_doc(db, dict(_error_row(str(zpath), zstat, '.zip', ''), status='deferred', error='',
                  note='OneDrive/cloudbestand staat mogelijk alleen online. Kies in Verkenner: Altijd behouden op dit apparaat.'))
        return
    try:
        zf = zipfile.ZipFile(zpath)
    except (zipfile.BadZipFile, OSError, ValueError) as exc:
        raise ValueError(f'Zip-bestand kan niet worden geopend (beschadigd?): {exc}') from exc
    with zf, tempfile.TemporaryDirectory(prefix='cv_zip_') as tmp:
        members = [i for i in zf.infolist() if not i.is_dir()
                   and Path(i.filename).suffix.lower() in SUPPORTED
                   and not Path(i.filename).name.startswith(('~$', '._'))
                   and '__MACOSX/' not in i.filename
                   # zelfde mappen overslaan als bij gewone mappen (venv, node_modules, .git, ...)
                   and not any(part in SKIP_DIRS or part.startswith('.')
                               for part in Path(i.filename).parts[:-1])]
        if not members:
            return
        summary['zips_met_documenten'] += 1
        total = 0
        for n, info in enumerate(members):
            vpath = prefix + info.filename
            seen_paths.add(vpath)
            ext = Path(info.filename).suffix.lower()
            summary['in_zip'] += 1
            if n >= ZIP_MAX_MEMBERS:
                write_doc(db, _error_row(vpath, zstat, ext, 'Overgeslagen: te veel bestanden in deze zip.'))
                summary['fouten'] += 1
                continue
            problem = ''
            if info.flag_bits & 0x1:
                problem = 'Bestand in zip is met een wachtwoord beveiligd; niet gelezen.'
            elif info.file_size > limiet_mb(ext, args) * 1024 * 1024:
                problem = f'Bestand in zip groter dan scanlimiet ({limiet_mb(ext, args)} MB); niet gelezen.'
            elif info.compress_size and info.file_size / info.compress_size > ZIP_MAX_RATIO and info.file_size > 5 * 1024 * 1024:
                problem = 'Verdacht sterk gecomprimeerd bestand (mogelijke zip-bom); niet gelezen.'
            elif total + info.file_size > ZIP_MAX_TOTAL_MB * 1024 * 1024:
                problem = f'Zip te groot om volledig te lezen (meer dan {ZIP_MAX_TOTAL_MB} MB uitgepakt).'
            if problem:
                write_doc(db, _error_row(vpath, zstat, ext, problem))
                summary['fouten'] += 1
                print(f'LEESFOUT bij {zpath.name}{ZIP_SEP}{info.filename}: {problem}', flush=True)
                continue
            total += info.file_size
            tmpfile = Path(tmp) / f'doc_{n}{ext}'
            try:
                # Gestreamd kopieren met harde limiet; de zip blijft onaangeroerd.
                limit = limiet_mb(ext, args) * 1024 * 1024
                written = 0
                with zf.open(info) as src, tmpfile.open('wb') as dst:
                    for chunk in iter(lambda: src.read(1048576), b''):
                        written += len(chunk)
                        if written > limit:
                            raise ValueError('bestand in zip groter dan opgegeven')
                        dst.write(chunk)
                process_document(tmpfile, vpath, Path(info.filename).stem, ext, zstat, args, db,
                                 archive, lang, summary)
            except Exception as exc:
                summary['fouten'] += 1
                print(f'LEESFOUT bij {zpath.name}{ZIP_SEP}{info.filename}: {type(exc).__name__}: {str(exc)[:300]}', flush=True)
                write_doc(db, _error_row(vpath, zstat, ext, f'{type(exc).__name__}: {str(exc)[:350]}'))
            finally:
                try:
                    tmpfile.unlink()   # alleen de eigen tijdelijke kopie
                except OSError:
                    pass
            if summary['in_zip'] % 25 == 0:
                db.commit()


def process_document(real_file, path_str, stem, ext, stat, args, db, archive, lang, summary):
    """Zelfde lees- en herkenningsstappen als voor losse bestanden."""
    sha = hash_file(real_file)
    duplicate = db.execute("SELECT * FROM docs WHERE sha=? AND status IN ('cv','other','review') AND classifier_version=? AND NOT (status='other' AND reason LIKE '%geen leesbare tekst%') LIMIT 1",
                           (sha, CLASSIFIER_VERSION)).fetchone()
    if duplicate and not args.force:
        data = {key: duplicate[key] for key in duplicate.keys()}
        data.update(path=path_str, mtime_ns=stat.st_mtime_ns, size=stat.st_size, received_ns=received_ns(stat),
                    ext=ext, year=year_from('', stem) or duplicate['year'])
        write_doc(db, data)
        summary[duplicate['status']] += 1
        summary['exacte_kopie'] += 1
        return
    text, note, layout = safe_read_document(real_file, not args.no_ocr, lang, args.ocr_pages,
                                            timeout_seconds=args.reader_timeout)
    emails, phones, links = emails_in(text), phones_in(text), linkedins_in(text)
    name = guess_name(text, stem, emails, links)
    score, reason = classify(text, stem, name, emails, phones, links, layout, source_path=path_str)
    status = decide_status(text, stem, name, emails, phones, links, score, args.threshold,
                           source_path=path_str)
    if ext == '.pdf' and not text.strip():
        status = 'other'
    dst = ''
    if status == 'cv':
        target = archive / f'{sha}{ext}'
        if not target.exists():
            shutil.copy2(real_file, target)
        dst = str(target)
    write_doc(db, dict(path=path_str, mtime_ns=stat.st_mtime_ns, size=stat.st_size, sha=sha, ext=ext,
              received_ns=received_ns(stat),
              status=status, score=score, reason=reason, note=note, name=name,
              emails=json.dumps(emails, ensure_ascii=False), phones=json.dumps(phones),
              links=json.dumps(links), snippet=normalized_snippet(text),
              tokens=json.dumps(signature(text)), year=year_from(text, stem),
              employer=field_value(text, ['werkgever','huidige werkgever','employer','company']) or current_job(text)[1],
              role=field_value(text, ['functie','huidige functie','job title','position']) or current_job(text)[0],
              websites=json.dumps(websites_in(text)), stage=stage_from(text), bijbanen=bijbanen_from(text), woonplaats=woonplaats_from(text), werkplaatsen=werkplaatsen_from(text), opleidingen=opleidingen_from(text), werkgevers=werkgevers_from(text), leverancier=leverancier_from(text), taal=cv_hoofdtaal(text) or '-',
              archive=dst, error='', classifier_version=CLASSIFIER_VERSION,
              name_verified=int(bool(name) and status == 'cv')))
    summary[status] += 1
    if status == 'cv':
        print(f'CV GEVONDEN: {name or "(naam onbekend)"} – {container_path(path_str).name}{ZIP_SEP}{stem}{ext}', flush=True)


class DSU:
    def __init__(self, n):
        self.parent = list(range(n))
    def find(self, i):
        while self.parent[i] != i:
            self.parent[i] = self.parent[self.parent[i]]
            i = self.parent[i]
        return i
    def union(self, a, b):
        a, b = self.find(a), self.find(b)
        if a != b:
            self.parent[max(a,b)] = min(a,b)


# Contactgegevens van bureaus/recruiters (staan bij 3+ verschillende namen, of info@-achtig): tellen nooit als
# bewijs om twee cv's samen te voegen. Wordt per scan gevuld in deduplicate().
BUREAU_CONTACT = set()
GESPLITST = []   # (oud persoon-id, bestanden) van kandidaten die bij deze scan zijn gesplitst


def _zelfde_naam_andere_volgorde(an, bn):
    """'Hans-Georg Bork' en 'Bork Hansgeorg': zelfde letters, alleen andere volgorde/spaties."""
    x, y = an.replace(' ', ''), bn.replace(' ', '')
    return bool(x) and len(x) == len(y) and x != y and y in x + x


def naam_variant(an, bn):
    """Zelfde naam in een andere schrijfwijze: 'Michel Bisschof' / 'Michael Bisschoff' (elk woord lijkt sterk)."""
    a = [w for w in an.split() if w not in NAME_FILLERS]
    b = [w for w in bn.split() if w not in NAME_FILLERS]
    return (len(a) == len(b) >= 2 and a != b and a[0][0] == b[0][0]
            and all(difflib.SequenceMatcher(None, x, y).ratio() >= .85 for x, y in zip(a, b)))


def _voornaam_botst(an, bn):
    """Twee volledige, verschillende voornamen (Jan / Piet, Jan / Jana) = verschillende personen.
    Geen botsing bij een voorletter (J / Jan), een korte vorm (Chris / Christopher, Ron / Ronald) of één naam leeg."""
    if not an or not bn or _zelfde_naam_andere_volgorde(an, bn):
        return False
    a0, b0 = an.split()[0], bn.split()[0]
    if a0 == b0 or len(a0) <= 2 or len(b0) <= 2 or a0 in NAME_FILLERS or b0 in NAME_FILLERS:
        return False
    kort, lang = sorted((a0, b0), key=len)
    if lang.startswith(kort) and len(lang) - len(kort) >= 3 and len(kort) >= 3:
        return False                      # Chris / Christopher, Ron / Ronald
    if min(len(a0), len(b0)) >= 5 and a0[0] == b0[0] and difflib.SequenceMatcher(None, a0, b0).ratio() >= .9:
        return False                      # schrijfwijze: Michel / Michael, Mohamed / Mohammed
    return True


def naam_verzoenen(naam_a, naam_b, teksten):
    """'Botwin Wytze' en 'Botwin Sijtsma': staat ergens in de cv's 'Botwin Wytze Sijtsma'? Dan is het één persoon met
    meerdere voornamen en geeft dit de volledige naam terug. Anders ''. Alleen bij dezelfde eerste voornaam."""
    a = [w for w in normalized_name(naam_a).split()]
    b = [w for w in normalized_name(naam_b).split()]
    if not a or not b or a[0] != b[0]:
        return ''
    nodig = {w for w in a + b if w not in NAME_FILLERS}
    for t in teksten:
        t = ' ' + (t or '')[:5000] + ' '
        for m in re.finditer(r' ' + re.escape(a[0]) + r'((?: [a-z]+){1,5}) ', t):
            reeks = [a[0]] + m.group(1).split()
            # kortste stuk vanaf de voornaam dat alle naamwoorden bevat
            for n in range(2, len(reeks) + 1):
                deel = reeks[:n]
                if len(set(deel)) < len(deel):
                    break                     # 'botwin wytze botwin sijtsma' = twee namen achter elkaar, niet één
                if nodig <= set(deel) and all(w in nodig or w in NAME_FILLERS for w in deel):
                    return tidy_name(' '.join(w if w in NAME_FILLERS else w.capitalize() for w in deel))
    return ''


def zelfde_naam_eigen_email(a, b):
    """Eén kandidaat als de volledige naam exact gelijk is en in beide CV's
    eenzelfde persoonlijk e-mailadres staat. Geen recruiter-, team- of
    generiek adres. Deze expliciete gebruikersregel weegt bij meerdere
    CV-versies zwaarder dan een veranderde opleiding of baan.
    """
    name_a, name_b = normalized_name(a['name']), normalized_name(b['name'])
    if not name_a or name_a != name_b or len(name_a.split()) < 2:
        return False
    try:
        shared = set(json.loads(a['emails'] or '[]')) & set(json.loads(b['emails'] or '[]'))
    except (ValueError, TypeError):
        return False
    return any(
        e not in BUREAU_CONTACT and not GENERIC_EMAIL.match(e)
        and email_matches_name(a['name'], e)
        for e in shared
    )


def harde_blokkade(a, b):
    """Redenen waarom twee cv's NOOIT dezelfde persoon kunnen zijn (ook niet als de AI 'zelfde' zegt)."""
    an, bn = normalized_name(a['name']), normalized_name(b['name'])
    if _voornaam_botst(an, bn):
        return 'andere voornaam'
    if loopbaan_botst(a, b):
        return 'andere loopbaan (andere werkgevers in dezelfde jaren)'
    # Een aantoonbaar tegenstrijdige opleiding betekent twee afzonderlijke kandidaten, niet 'mogelijk dubbel'.
    # Zelfde naam, mail, telefoon of AI-akkoord mogen dit niet opheffen. Bachelor -> master is geen botsing;
    # een andere woonplaats zonder opleidingsconflict evenmin.
    if opleiding_botst(a, b):
        return 'tegenstrijdige opleiding (niveau, jaren of opleidingsplaats)'
    aw, bw = [w for w in an.split() if w not in NAME_FILLERS], [w for w in bn.split() if w not in NAME_FILLERS]
    if len(aw) >= 2 and len(bw) >= 2 and not _zelfde_naam_andere_volgorde(an, bn):
        la, lb = aw[-1], bw[-1]
        if (la != lb and la not in lb and lb not in la and difflib.SequenceMatcher(None, la, lb).ratio() < .75
                and not (set(aw) <= set(bw) or set(bw) <= set(aw))):
            return 'andere achternaam'
    keys = a.keys() if hasattr(a, 'keys') else a
    if 'path' in keys and 'path' in (b.keys() if hasattr(b, 'keys') else b):
        fa, fb = naam_uit_bestandsnaam(a['path'], a['snippet']), naam_uit_bestandsnaam(b['path'], b['snippet'])
        wa = {w for w in normalized_name(fa).split() if w not in NAME_FILLERS}
        wb = {w for w in normalized_name(fb).split() if w not in NAME_FILLERS}
        # Schrijfwijze telt niet als ander persoon: 'Bisschof Michel' / 'Bisschoff Michael'.
        lijkt = any(difflib.SequenceMatcher(None, x, y).ratio() >= .85 for x in wa for y in wb if len(x) >= 4 and len(y) >= 4)
        if wa and wb and not (wa & wb) and not lijkt:
            return 'andere naam in bestandsnaam'
    return ''


def _werkgever_kern(d):
    """Eerste woord van elke werkgever, plus werkperiodes. Functietitels blijven erbuiten."""
    found = set()
    keys = d.keys() if hasattr(d, 'keys') else ()
    raw = d['werkgevers'] if 'werkgevers' in keys else ''
    for x in str(raw or '').split(' ; '):
        stuk = normalized_name(x).split()
        if stuk:
            found.add(stuk[0])
    try:
        for w, _b, _e in werk_periodes(d):
            found.add(str(w).lstrip('*'))
    except Exception:
        pass
    return {w for w in found if len(w) >= 4 and w not in COMPANY_WORDS and not w.endswith(JOB_ENDINGS)}


def mail_verliest_van_conflict(a, b):
    """Zelfde mailadres koppelt niet als de studie én de werkgever botsen.

    Daan en Sasha in de stresstest hebben dezelfde naam en hetzelfde adres,
    maar een andere opleiding én een andere baan. Dat zijn twee personen.
    Een nieuwe cv-versie met dezelfde werkgever blijft wél één kandidaat.
    """
    if not opleiding_botst(a, b):
        return False
    oa, ob = _werkgever_kern(a), _werkgever_kern(b)
    if oa and ob and not (oa & ob):
        return True
    return bool(loopbaan_botst(a, b))


def safe_pair(a, b):
    """Geeft (auto, review, motivatie); automatisch uitsluitend met sterk bewijs."""
    an, bn = normalized_name(a['name']), normalized_name(b['name'])
    if _zelfde_naam_andere_volgorde(an, bn):
        bn = an                                        # 'Bork Hansgeorg' = 'Hans-Georg Bork'
    if an and bn and an != bn and [w for w in an.split() if w not in NAME_FILLERS] == [w for w in bn.split() if w not in NAME_FILLERS] \
            and len([w for w in an.split() if w not in NAME_FILLERS]) >= 2 \
            and (not [w for w in an.split() if w in NAME_FILLERS] or not [w for w in bn.split() if w in NAME_FILLERS]):
        # Alleen als in één naam het tussenvoegsel ontbreekt; 'de Vries' en 'van der Vries' blijven verschillend.
        bn = an                                        # 'Erwin Boven' = 'Erwin van Boven'
    same_name = bool(an and bn and an == bn)
    name_sim = difflib.SequenceMatcher(None, an, bn).ratio() if an and bn else 0
    conflict = bool(an and bn and name_sim < .70)
    ae, be = set(json.loads(a['emails'])) - BUREAU_CONTACT, set(json.loads(b['emails'])) - BUREAU_CONTACT
    ap, bp = set(json.loads(a['phones'])) - BUREAU_CONTACT, set(json.loads(b['phones'])) - BUREAU_CONTACT
    al, bl = set(json.loads(a['links'])) - BUREAU_CONTACT, set(json.loads(b['links'])) - BUREAU_CONTACT
    ae = {e for e in ae if not GENERIC_EMAIL.match(e)}
    be = {e for e in be if not GENERIC_EMAIL.match(e)}
    strong = bool((ae & be) or (ap & bp) or (al & bl))
    if a['sha'] and a['sha'] == b['sha']:
        return True, False, 'exact dezelfde documenthash'
    # Zelfde persoonsnaam en hetzelfde eigen e-mailadres => één kandidaat,
    # behalve als studie én werkgever aantoonbaar botsen (twee verschillende mensen,
    # hetzelfde mailadres in de test of op een bureau-cv).
    if zelfde_naam_eigen_email(a, b) and not mail_verliest_van_conflict(a, b):
        return True, False, 'zelfde volledige naam en persoonlijk e-mailadres: één kandidaat; nieuwste CV wint'
    def _werkgever_tokens(d):
        found = set()
        raw = d['werkgevers'] if 'werkgevers' in (d.keys() if hasattr(d, 'keys') else d) else ''
        for x in str(raw or '').split(' ; '):
            stuk = normalized_name(x).split()
            if stuk:
                found.add(stuk[0])
        for w, _b, _e in werk_periodes(d):
            found.add(str(w).lstrip('*'))
        for _b, _e, venster, *_rest in periodes_uit_cv(d['snippet'] or ''):
            first = (venster.split() or [''])[0]
            if len(first) >= 4 and first not in WERK_STOP and first not in NL_PLACES:
                found.add(first)
        snippet = d['snippet'] if 'snippet' in (d.keys() if hasattr(d, 'keys') else d) else ''
        words = str(snippet or '').split()
        for i, w in enumerate(words[:-1]):
            if w.endswith(JOB_ENDINGS):
                found.add(words[i + 1])
        for m in re.finditer(r'\b(?:bij|at)\s+([a-z]{4,})', str(snippet or '')):
            found.add(m.group(1))
        def _bedrijf(w):
            w = str(w).lstrip('*')
            if len(w) < 4 or w in WERK_STOP or w in NL_PLACES or w in COMPANY_WORDS or w.endswith(JOB_ENDINGS):
                return ''
            if w in {'heden', 'present', 'support', 'analyse', 'voor', 'met', 'van', 'the', 'and', 'voor'}:
                return ''
            return w
        return {x for x in (_bedrijf(w) for w in found) if x}
    # Harde blokkades: andere voornaam (ook gezinsleden), andere persoon in de bestandsnaam (bureau-cv's).
    blok = harde_blokkade(a, b)
    # Zelfde volle naam zonder twee verschillende persoonlijke mails: één kandidaat.
    # Opleiding of loopbaan op een lastige/oude CV mag dat niet splitsen (nieuwste CV wint).
    # Echte naamgenoten met andere werkgevers blijven via de token-check hieronder apart.
    if (blok.startswith(('tegenstrijdige opleiding', 'andere loopbaan'))
            and same_name and len(an.split()) >= 2
            and not (ae and be and not (ae & be))
            and not mail_verliest_van_conflict(a, b)):
        blok = ''
    if blok:
        if blok == 'andere voornaam' and ae & be and all(
                not email_matches_name(a['name'], e) and not email_matches_name(b['name'], e) for e in ae & be):
            return False, False, ''         # mailadres van geen van beiden (recruiter): gewoon apart, geen twijfel
        if blok == 'andere voornaam' and ((ae & be) or (ap & bp) or (al & bl)):
            # 'Marjoke' / 'Joke van Alphen' met hetzelfde mailadres: roepnaam? Niet automatisch, wel laten zien.
            return False, True, 'zelfde contactgegevens, maar andere voornaam (roepnaam?)'
        if blok == 'andere achternaam' and ((ae & be) or (ap & bp) or (al & bl)):
            # Bv. 'Botwin Wytze' / 'Botwin Sijtsma' met hetzelfde mailadres. Naam opnieuw controleren: staat
            # 'Botwin Wytze Sijtsma' in een van de cv's (meerdere voornamen)? En klopt het cv ook (zelfde werkgever met
            # dezelfde jaren of zelfde opleiding, geen botsende loopbaan)? Pas dan samen; anders twijfelgeval.
            volledig = naam_verzoenen(a['name'], b['name'], [a['snippet'], b['snippet']])
            if volledig and (ae & be) and (ap & bp) and not loopbaan_botst(a, b) and (
                    zelfde_werk(a, b) or any(x == 'zelfde opleiding' for x in _bewijs_stukken(a, b))):
                return True, False, f'zelfde persoon met meerdere voornamen ({volledig}); zelfde mail, telefoon en loopbaan'
            return False, True, 'zelfde contactgegevens, maar andere achternaam'
        return False, False, ''
    # Zelfde volle naam, geen twee verschillende persoonlijke mails, geen harde blokkade: één kandidaat.
    # Excel toont het nieuwste CV; ontbrekende mail of telefoon komt van de andere CV.
    # Heeft het nieuwste CV beide, dan worden oude mail en nummer niet gebruikt.
    # Naamgenoot met 1 gedeelde werkgever en verder andere banen blijft apart.
    if same_name and len(an.split()) >= 2 and not (ae and be and not (ae & be)):
        oa, ob = _werkgever_tokens(a), _werkgever_tokens(b)
        # Precies één gedeelde werkgever én aan beide kanten andere banen: naamgenoot.
        # Geen overlap (lastige CV met andere werkgever) is dezelfde persoon.
        naamgenoot = bool(oa and ob and (oa - ob) and (ob - oa) and len(oa & ob) == 1)
        try:
            gedeeld = set(json.loads(a['emails'] or '[]')) & set(json.loads(b['emails'] or '[]'))
        except (ValueError, TypeError):
            gedeeld = set()
        alleen_bureau = bool(gedeeld) and all(
            GENERIC_EMAIL.match(e) or e in BUREAU_CONTACT for e in gedeeld)
        # Zelfde naam op twee bureau-cv's met alleen jobs@/info@ en een andere loopbaan: niet één persoon.
        # Het gedeelde 020-nummer is dan het recruiternummer, geen privénummer.
        if alleen_bureau and oa and ob and oa != ob:
            return False, False, ''
        if not naamgenoot:
            return True, False, 'zelfde volledige naam: één kandidaat; nieuwste CV wint, ontbrekende mail/telefoon aanvullen'
    # Beide cv's zonder bruikbare naam, maar hetzelfde persoonlijke mailadres ÉN telefoonnummer: één persoon.
    if not an and not bn and (ae & be) and (ap & bp):
        return True, False, 'geen naam, wel zelfde persoonlijk e-mailadres en telefoon'
    # Eén cv zonder bruikbare naam ('Persoonlijke Gegevens'), het andere met naam: samen als e-mail ÉN telefoon
    # gelijk zijn en het mailadres bij die naam past (frank.jansen@ bij Frank Jansen). Niet bij info@/bureau-adressen.
    if bool(an) != bool(bn) and (ap & bp):
        naam = an or bn
        if any(not GENERIC_EMAIL.match(e) and email_matches_name(naam, e) for e in ae & be):
            return True, False, 'zelfde e-mail en telefoon; mailadres past bij de naam'
    # Zelfde persoonlijk e-mailadres + zelfde voornaam, en het adres past bij beide namen:
    # 'Botwin Wytze' en 'Botwin Sijtsma' met botwin.sijtsma@ = één persoon. Niet bij gezinsleden
    # ('Piet Jansen' / 'Marie Jansen' met jansen@): de voornaam moet gelijk zijn.
    for e in ae & be:
        if GENERIC_EMAIL.match(e) or not an or not bn:
            continue
        local = re.sub(r'[^a-z]', '', e.split('@')[0].lower())
        stukken = [x for x in re.split(r'[._\-0-9]+', e.split('@')[0].lower()) if x]
        aw1, bw1 = an.split(), bn.split()
        def achternaam(ws):
            # Achternaam = laatste woord van de naam (na 'van', 'de' …), geen bedrijfs-, vak- of functiewoord.
            echte = [w for w in ws[1:] if w not in NAME_FILLERS]
            w = echte[-1] if echte else ''
            if len(w) < 3 or w in COMPANY_WORDS or w in NAME_BLOCKLIST or w.endswith(JOB_ENDINGS) or w in NAAM_STAART:
                return ''
            return w
        def staat_in_adres(w):
            # Als los stuk (jan.bakker / jan_bakker) of als voornaam/voorletter + achternaam (janbakker / jbakker /
            # jvandijk), niet ergens middenin ('it' in 'itbureau', 'mans' in 'janbosmans').
            if not w:
                return False
            if w in stukken:
                return True
            for st in stukken:
                if st.endswith(w):
                    voor = st[:-len(w)]
                    voor = re.sub(r'(?:van|de|der|den|vd|v|ter|ten|het|t)+$', '', voor)
                    if voor in ('', aw1[0], aw1[0][:1]):
                        return True
            return False
        # Alleen een privé-adres (gmail, hotmail, ziggo …): een bureau of recruiter mailt vanaf een bedrijfsdomein.
        # En de achternaam van minstens één van beide namen moet in het adres staan (niet alleen 'jan@').
        prive = e.split('@')[-1].lower() in PRIVE_MAILDOMEINEN
        a_in, b_in = staat_in_adres(achternaam(aw1)), staat_in_adres(achternaam(bw1))
        zelfde_voornaam = aw1[0] == bw1[0] and len(aw1[0]) >= 3 and aw1[0] in local
        # 'Jan Bakker' en 'J. Bakker': voorletter past bij de voornaam en de achternaam is gelijk.
        voorletter = (min(len(aw1[0]), len(bw1[0])) == 1 and aw1[0][0] == bw1[0][0]
                      and achternaam(aw1) and achternaam(aw1) == achternaam(bw1))
        if prive and (zelfde_voornaam or voorletter) and (a_in or b_in):
            # Beide achternamen passen bij het adres -> zeker dezelfde persoon. Past er maar één ('Jan de Vries'
            # met jan.bakker@ – bv. een referentie), dan alleen samen als ook het telefoonnummer gelijk is.
            if (a_in and b_in) or (ap & bp):
                if opleiding_botst(a, b) and not (ap & bp):
                    # Zelfde mailadres, maar dezelfde soort opleiding in heel andere jaren (geen vervolgstudie) en een
                    # ander nummer: niet blind samen, als mogelijk dubbel laten zien.
                    return False, True, 'zelfde e-mailadres, maar andere opleiding in andere jaren'
                return True, False, 'zelfde persoonlijk e-mailadres en voornaam'
    aw0, bw0 = set(an.split()), set(bn.split())
    if conflict and strong and not (min(len(aw0), len(bw0)) >= 2 and (aw0 <= bw0 or bw0 <= aw0)):
        return False, True, 'gedeelde contactgegevens maar afwijkende namen'
    # 'Marcel Hilboezen' en 'Marcel Hilboezen Linkit': de kortste naam zit helemaal in de langste.
    aw, bw = set(an.split()), set(bn.split())
    contained = bool(an and bn) and min(len(aw), len(bw)) >= 2 and (aw <= bw or bw <= aw)
    if strong and an and bn and not contained and len(an.split()) >= 2 and len(bn.split()) >= 2:
        # 'Jeroen It' en 'Jeroen Smit' lijken als tekst op elkaar, maar de achternaam verschilt echt.
        la, lb = an.split()[-1], bn.split()[-1]
        if la != lb and difflib.SequenceMatcher(None, la, lb).ratio() < .75:
            return False, True, 'gedeelde contactgegevens maar andere achternaam'
        if la != lb and not (ap & bp) and not (al & bl):
            # 'Johan Smit' / 'Johan Smits' met één gedeeld mailadres: misschien een typfout, misschien niet.
            # Alleen samen als ook telefoon of LinkedIn gelijk is; anders als mogelijk dubbel laten zien.
            return False, True, 'zelfde e-mail, achternaam net anders'
    # Dezelfde naam + exact dezelfde volledige opleiding of specifieke werkgever met beginjaar:
    # ook zonder dezelfde mail/telefoon kunnen dit verschillende CV-versies zijn.
    if same_name and not strong:
        opl = opleiding_overeenkomsten(a, b)
        werk = zelfde_werk(a, b)
        if opl and (werk or len(opl) >= 2):
            return True, False, 'zelfde volledige opleiding (' + opl[0] + ')' + (
                ' en werkgever (' + werk[0] + ')' if werk else ' (meerdere opleidingen)')
        if werk and len(werk) >= 2:
            return True, False, 'zelfde twee werkgevers met beginjaar (' + ', '.join(werk[:2]) + ')'
    if strong:
        return (True, False, 'zelfde e-mail, telefoon of LinkedIn én naam') if (an and bn and (name_sim >= .89 or contained)) \
            else (False, True, 'gedeelde contactgegevens; namen niet bevestigd')
    if not an or not bn:
        return False, False, ''
    if name_sim < .91:
        return False, False, ''
    atext, btext = (a['snippet'] or ''), (b['snippet'] or '')
    text_sim = difflib.SequenceMatcher(None, atext[:6000], btext[:6000]).ratio() if atext and btext else 0
    at, bt = set(json.loads(a['tokens'])), set(json.loads(b['tokens']))
    overlap = len(at & bt)
    jacc = overlap / max(1, len(at | bt))
    if same_name and (text_sim >= .87 or (overlap >= 18 and jacc >= .32)):
        # Gelijke tekst kan ook komen door hetzelfde sjabloon (bureau). Alleen samen met nog een hard bewijs.
        if (ae & be) or (ap & bp) or (al & bl) or zelfde_werk(a, b) or text_sim >= .93:
            return True, False, 'zelfde naam en zeer sterk gelijkende CV-inhoud'
        return False, True, 'zelfde naam en gelijkende tekst, maar geen gedeeld contact of werkgever+jaren'
    if same_name:
        # Nieuwere versie van hetzelfde cv (bv. er is een baan bijgekomen): zelfde naam
        # + minstens 1 STERK gedeeld kenmerk (zelfde specifieke werkgever/stagebedrijf)
        # + minstens 1 ander gedeeld kenmerk (zelfde school, woonplaats of werkplaats).
        def orgs(d):
            vals = []
            for col in ('employer', 'stage', 'werkgevers'):
                if col in d.keys() and d[col]:
                    vals += [_clean_org(x) for x in str(d[col]).split(' ; ')]
            return {v for v in vals if len(v) >= 3 and v not in NL_PLACES and not VAGUE_EMPLOYERS.match(v)
                    and not BIG_EMPLOYERS.match(v)}
        def weak(d):
            vals = set()
            for col in ('opleidingen', 'werkplaatsen'):
                if col in d.keys() and d[col]:
                    vals |= {f'{col}:{x.strip()}' for x in str(d[col]).split(' ; ') if x.strip()}
            if 'woonplaats' in d.keys() and d['woonplaats']:
                vals.add('woonplaats:' + d['woonplaats'])
            return vals
        def shared(xs, ys, fuzzy_ok=lambda v: True):
            out = set()
            for x in xs:
                for y in ys:
                    if x == y or (fuzzy_ok(x) and phrase_like(x, y)):
                        out.add(x)
            return out
        shared_strong = shared(orgs(a), orgs(b))
        # Plaatsnamen exact (Leiden ≠ Weiden); opleidingen mogen een typfout hebben.
        shared_weak = shared(weak(a), weak(b), lambda v: v.startswith('opleidingen:'))
        if shared_strong and shared_weak and zelfde_werk(a, b):
            return True, False, ('zelfde naam + zelfde ' + ', '.join(sorted(shared_strong)) + ' + ' +
                                 ', '.join(sorted(x.split(':', 1)[1] for x in shared_weak)) + ' (nieuwere cv-versie)')
    if (same_name or naam_variant(an, bn)) and len(an.split()) >= 2:
        # Nieuwe versie zonder gedeeld contact (ander mailadres én nummer): zelfde volledige naam + minstens 2
        # verschillende (niet-grote) werkgevers met dezelfde jaren + geen botsende loopbaan = dezelfde persoon.
        zw = zelfde_werk(a, b)
        if len(zw) >= 2 and not loopbaan_botst(a, b):    # 2 perioden (werkgever + beginjaar) precies gelijk
            return True, False, 'zelfde naam + zelfde werkgevers met dezelfde jaren (' + ', '.join(zw[:2]) + ')'
    if same_name or (name_sim >= .91 and (overlap >= 9 or text_sim >= .60)):
        return False, True, f'gelijke naam; inhoudsoverlap {overlap} termen; tekstovereenkomst {text_sim:.2f}'
    return False, False, ''


def cv_id_of(sha):
    """Uniek, vast cv-ID op basis van de inhoud (zelfde bestand = zelfde ID, ook na verplaatsen).
    Voorvoegsel SNCV zodat het nooit lijkt op een Loxo-nummer."""
    return 'SNCV-' + (sha or '')[:10].upper() if sha else ''


CV_FOLDER = 'Alle_cvs'
OUT_DIR = None          # gezet in main()


def cv_folder(out=None):
    """Waar de cv's bij 'Opschonen' komen: standaard <export>/Alle_cvs, of de plek die je koos
    (altijd een eigen submap 'Alle_cvs', zodat er nooit iets anders in die map wordt aangeraakt)."""
    out = Path(out or OUT_DIR or Path.home() / 'CV_Crawler_Export')
    try:
        chosen = (out / 'cv_map.txt').read_text(encoding='utf-8').strip()
    except OSError:
        chosen = ''
    return (Path(chosen) / CV_FOLDER) if chosen else (out / CV_FOLDER)


NAAMLOZE_FOLDER = 'Naamloze_cvs'


def naamloze_cv_folder(out=None):
    """Eigen map naast Alle_cvs, ook bij een zelfgekozen CV-bestandslocatie."""
    return cv_folder(out).parent / NAAMLOZE_FOLDER


def kopieer_naamloze_cvs(out, naamloze):
    """Kopieer overgebleven naamloze CV-kandidaten na AI; wijzig geen originelen.
    Voor ZIP-documenten wordt uitsluitend het betreffende ZIP-lid gekopieerd.
    Maak geen duplicaten en verwijder bestaande kopieën nooit.
    """
    folder = naamloze_cv_folder(out)
    try:
        if folder.is_symlink():
            print('NAAMLOZE CVS: doelmap is een verwijzing; overgeslagen.', flush=True)
            return 0
        folder.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        print(f'NAAMLOZE CVS: map niet beschikbaar ({type(exc).__name__}).', flush=True)
        return 0
    copied, seen = 0, set()
    for row in naamloze:
        sha = row.get('sha256') or ''
        location = row.get('oorspronkelijk_bestand') or ''
        if not re.fullmatch(r'[a-fA-F0-9]{64}', sha) or not location or sha in seen:
            continue
        seen.add(sha)
        vname = location.split(ZIP_SEP)[-1].replace('\\', '/').rsplit('/', 1)[-1]
        ext = Path(vname).suffix.lower()
        if ext not in SUPPORTED:
            continue
        target = folder / f'{cv_id_of(sha)}_{safe_part(Path(vname).stem, 45)}{ext}'
        part = folder / (target.name + '.bezig')
        try:
            if target.exists():
                if target.is_file() and not target.is_symlink() and (ext == '.pages' or hash_file(target) == sha):
                    row['kopie_in_naamloze_map'] = str(target)
                continue   # nooit een andere bestaande kopie overschrijven
            if part.exists():
                part.unlink()       # eigen tijdelijke kopie na afgebroken export
            if ZIP_SEP in location:
                pathzip, member = location.split(ZIP_SEP, 1)
                with zipfile.ZipFile(pathzip) as z:
                    info = z.getinfo(member)
                    if info.is_dir() or info.flag_bits & 1 or info.file_size > GROOT_MB * 1024 * 1024:
                        continue
                    with z.open(info) as source, part.open('wb') as dest:
                        shutil.copyfileobj(source, dest, 1048576)
            else:
                source = Path(location)
                if not source.exists() or source.is_symlink():
                    continue
                copy_document(source, part)
            if ext != '.pages' and hash_file(part) != sha:
                print('NAAMLOZE CVS: kopie komt niet overeen met origineel:', vname, flush=True)
                continue
            part.replace(target)
            row['kopie_in_naamloze_map'] = str(target)
            copied += 1
        except (OSError, ValueError, RuntimeError, KeyError, zipfile.BadZipFile) as exc:
            print(f'NAAMLOZE CVS: kopiëren mislukt ({type(exc).__name__}): {vname}', flush=True)
        finally:
            if part.exists():
                try:
                    part.unlink()
                except OSError:
                    pass
    print(f'NAAMLOZE CVS: {copied} nieuwe kopieën in {folder}; originelen blijven staan.', flush=True)
    return copied


def cv_rel_link(rel):
    """Link vanuit de Excel naar een cv in de cv-map (relatief als de map naast de Excel staat)."""
    folder = cv_folder()
    target = folder / rel
    try:
        return target.resolve().relative_to(Path(OUT_DIR).resolve()).as_posix()
    except (ValueError, TypeError, OSError):
        return file_link(str(target))


def safe_part(text, limit=50):
    text = re.sub(r'[^\w\-]+', '_', text or '', flags=re.UNICODE).strip('_')
    return text[:limit] or 'onbekend'


def cv_target_name(person, sha, ext):
    return f"{person['kandidaat_id']}_{safe_part(person['naam'])}_{cv_id_of(sha)}{ext}"


def _ext_of(d):
    return (Path(d['archive']).suffix if d['archive'] else
            Path(str(d['path']).split(ZIP_SEP)[-1]).suffix).lower() or '.bin'


OLDER_DIR = 'oudere_versies'
DUP_DIR = 'dubbel'


def desired_rel(person, sha, ext):
    """Nieuwste cv van een persoon in de hoofdmap, oudere versies in 'oudere_versies'."""
    name = cv_target_name(person, sha, ext)
    return name if sha == person.get('cv_sha') else f'{OLDER_DIR}/{name}'


def _free_dup_target(folder, name):
    stem, ext = os.path.splitext(name)
    n = 2
    t = folder / DUP_DIR / f'{stem}__{n}{ext}'
    while t.exists():
        n += 1
        t = folder / DUP_DIR / f'{stem}__{n}{ext}'
    return t


REGISTER = 'alle_cvs_register.json'


def load_register(out):
    try:
        return json.loads((out / REGISTER).read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return {}


def save_register(out, reg):
    try:
        (out / REGISTER).write_text(json.dumps(reg, indent=0), encoding='utf-8')
    except OSError:
        pass


def in_cv_folder(path, out):
    sp = str(path)
    if ZIP_SEP in sp:
        return False
    try:
        r = Path(sp).resolve()
        root = cv_folder(out).resolve()
        return r == root or root in r.parents
    except OSError:
        return False


def collect_cvs(out, people, docs, docs_to_id, db=None):
    """Alle_cvs bevat ALLEEN wat met 'Opschonen' is verplaatst (of uit een zip is gekopieerd).
    Hier wordt die map alleen geordend: nieuwste cv per kandidaat in de hoofdmap, oudere versies
    in 'oudere_versies', exact dezelfde extra bestanden in 'dubbel'. Er wordt niets bij-gekopieerd;
    losse kopieën van cv's die nog op hun eigen plek staan worden opgeruimd."""
    folder = cv_folder(out)
    if not folder.exists():
        return {}
    reg = load_register(out)
    by_pid = {p['persoon_id']: p for p in people}
    outside = collections.defaultdict(list)
    for d in docs:
        if not in_cv_folder(d['path'], out) and (ZIP_SEP in str(d['path']) or Path(str(d['path'])).exists()):
            outside[d['sha']].append(d)
    by_sha = {}
    renamed = removed = 0

    def move_db(old, new):
        if db is not None:
            db.execute('DELETE FROM docs WHERE path=?', (str(new),))
            db.execute('UPDATE docs SET path=? WHERE path=?', (str(new), str(old)))

    for d in sorted(docs, key=lambda d: (DUP_DIR in Path(str(d['path'])).parts, str(d['path']))):
        sp = str(d['path'])
        if not in_cv_folder(sp, out) or not d['sha']:
            continue
        path = Path(sp)
        if not path.exists():
            continue
        if d['sha'] not in reg:
            if outside.get(d['sha']):
                # Kopie van een cv dat nog gewoon op zijn eigen plek staat: weg ermee.
                try:
                    path.unlink()
                    removed += 1
                    if db is not None:
                        db.execute('DELETE FROM docs WHERE path=?', (sp,))
                except OSError:
                    pass
                continue
            reg[d['sha']] = 'verplaatst'      # staat alleen hier: bewaren
        if DUP_DIR in path.parts:
            continue
        p = by_pid.get(docs_to_id.get(sp, ''))
        if not p:
            continue
        rel = desired_rel(p, d['sha'], path.suffix.lower())
        target = folder / rel
        try:
            if d['sha'] in by_sha or (path.resolve() != target.resolve() and target.exists()):
                dup = _free_dup_target(folder, target.name)
                dup.parent.mkdir(exist_ok=True)
                path.rename(dup)
                move_db(path, dup)
                continue
            if path.resolve() != target.resolve():
                target.parent.mkdir(exist_ok=True)
                path.rename(target)
                move_db(path, target)
                renamed += 1
        except OSError as exc:
            print('LET OP: kon niet ordenen:', path.name, '-', exc, flush=True)
            continue
        by_sha[d['sha']] = rel
    if db is not None:
        db.commit()
    save_register(out, reg)
    newest = sum(1 for r in by_sha.values() if '/' not in r)
    print(f'CV-MAP: {newest} nieuwste cv\'s in {folder}, {len(by_sha) - newest} oudere versies'
          + (f', {removed} overbodige kopieën opgeruimd' if removed else '')
          + (f', {renamed} hernoemd' if renamed else '') + '.', flush=True)
    return by_sha


def mac_archive_link(archive):
    """Mac: Excel mag (sandbox) alleen bestanden openen in mappen waar je toestemming voor gaf.
    Daarom op de Mac naar de kopie in cv_archief linken: dan is één keer toestemming voor de map
    CV_Crawler_Export genoeg voor alle cv's, in plaats van een vraag per map."""
    if not archive or not Path(archive).exists() or not OUT_DIR:
        return ''
    try:
        return Path(archive).resolve().relative_to(Path(OUT_DIR).resolve()).as_posix()
    except (ValueError, OSError):
        return ''


def save_kandidaten_json(out, cols, rows):
    """Zelfde tabel als het eerste Excel-tabblad, voor het venster 'Kandidaten' in de scanner
    (op de Mac loopt Excel soms vast op links; de scanner opent cv's zelf).
    Links worden een volledig pad of een webadres."""
    def cell(v):
        url = getattr(v, 'url', '')
        if not url:
            return str(v if v is not None else '')
        if url.startswith(('http://', 'https://')):
            return {'tekst': str(v), 'url': url}
        if url.startswith('file:'):
            target = urllib.parse.unquote(url.split('file:', 1)[1].lstrip('/'))
            if not re.match(r'^[A-Za-z]:', target):
                target = '/' + target
        else:
            target = str(Path(out) / url)
        return {'tekst': str(v), 'pad': target}
    data = {'kolommen': cols, 'rijen': [{c: cell(r.get(c, '')) for c in cols} for r in rows]}
    tmp = Path(out) / 'kandidaten.bezig.json'
    try:
        tmp.write_text(json.dumps(data, ensure_ascii=False), encoding='utf-8')
        os.replace(tmp, Path(out) / 'kandidaten.json')
    except OSError as exc:
        print('LET OP: kandidatenlijst voor de scanner niet opgeslagen:', exc, flush=True)


def file_link(path, archive=''):
    """Klikbare link naar een bestand op de pc (cv uit zip: de archiefkopie)."""
    sp = str(path or '')
    if ZIP_SEP in sp:
        sp = archive if archive and Path(archive).exists() else sp.split(ZIP_SEP)[0]
    if not sp:
        return ''
    return 'file:///' + urllib.parse.quote(sp.replace('\\', '/'), safe='/:')


CLEAN_LOG = 'opschonen_logboek.csv'


CLOUD_MOVE = False   # True: cv's ook UIT OneDrive/iCloud verplaatsen (gebruiker koos 'weghalen')
CLOUD_HINTS = ('onedrive', 'mobile documents', 'icloud drive', 'cloudstorage', 'dropbox', 'google drive', 'googledrive')


def in_cloud_folder(path_str):
    """Staat in een map die met de cloud synchroniseert: verplaatsen = weg uit de cloud."""
    low = str(path_str).lower().replace('\\', '/')
    return any(h in low for h in CLOUD_HINTS)


def cleanup_plan(db, out):
    """Welke losse cv's worden verplaatst. Zips en OneDrive blijven staan."""
    merges = out / 'samenvoegen_correcties.csv'
    docs, people, docs_to_id, _r = deduplicate(db, merges if merges.exists() else None)
    by_pid = {p['persoon_id']: p for p in people}
    folder = cv_folder(out)
    root_res = folder.resolve() if folder.exists() else None
    plan, skipped = [], collections.Counter()
    main_taken = {}
    zips = []
    reg_now = load_register(out)
    # Wat al in de map staat als verplaatst origineel telt als 'hoofdversie'.
    for d in docs:
        sp = str(d['path'])
        path = Path(sp)
        if ZIP_SEP not in sp and root_res and (path.resolve() == root_res or root_res in path.resolve().parents) \
                and DUP_DIR not in path.parts and d['sha'] in reg_now and path.exists():
            main_taken[d['sha']] = path
    for d in docs:
        sp = str(d['path'])
        p = by_pid.get(docs_to_id.get(sp, ''))
        if not p:
            continue
        if ZIP_SEP in sp:
            zips.append((d, p))
            continue
        path = Path(sp)
        if root_res and (path.resolve() == root_res or root_res in path.resolve().parents):
            continue
        if not path.exists():
            continue
        cloud = in_cloud_folder(sp) and not CLOUD_MOVE
        if cloud and d['sha'] in reg_now:
            continue                      # al eerder gekopieerd; origineel blijft in de cloud
        rel = desired_rel(p, d['sha'], path.suffix.lower())
        if d['sha'] in main_taken:
            stem, ext = os.path.splitext(Path(rel).name)
            n = 2
            target = folder / DUP_DIR / f'{stem}__{n}{ext}'
            while target.exists() or any(t == target for _o, t, _k in plan):
                n += 1
                target = folder / DUP_DIR / f'{stem}__{n}{ext}'
            if cloud:
                skipped['dubbel in cloud (blijft staan)'] += 1
                continue
            plan.append((path, target, 'dubbel'))
        else:
            main_taken[d['sha']] = folder / rel
            plan.append((path, folder / rel, 'kopie uit cloud' if cloud else
                         ('verplaatsen' if '/' not in rel else 'oudere versie')))
    for d, p in zips:
        # Cv in een zip: het cv gaat UIT de zip naar de cv-map. De rest van de zip blijft staan.
        sp = str(d['path'])
        zpath = container_path(sp)
        if sp.count(ZIP_SEP) > 1:
            skipped['in een zip binnen een zip (blijft staan)'] += 1
            continue
        if in_cloud_folder(zpath):
            skipped['zip in OneDrive/iCloud (blijft staan)'] += 1
            continue
        if not zpath.exists():
            continue
        rel = desired_rel(p, d['sha'], _ext_of(d))
        if d['sha'] in main_taken:
            stem, ext = os.path.splitext(Path(rel).name)
            n = 2
            target = folder / DUP_DIR / f'{stem}__{n}{ext}'
            while target.exists() or any(t == target for _o, t, _k in plan):
                n += 1
                target = folder / DUP_DIR / f'{stem}__{n}{ext}'
        else:
            target = folder / rel
            main_taken[d['sha']] = target
        plan.append((d, target, 'uit zip'))
    return plan, skipped


def _move_out_of_zips(db, out, items, writer, reg):
    """Haal cv's uit hun zip. Eerst een backup van de zip; zitten er nog andere bestanden in,
    dan blijft de zip (zonder de cv's) staan; zaten er alleen cv's in, dan gaat de zip weg
    (de backup blijft bewaard, dus 'Terugzetten' kan altijd)."""
    done = 0
    by_zip = collections.defaultdict(list)
    for d, target in items:
        by_zip[container_path(str(d['path']))].append((d, target))
    backup_dir = out / 'zip_backups'
    backup_dir.mkdir(exist_ok=True)
    for zpath, entries in by_zip.items():
        try:
            with zipfile.ZipFile(zpath) as z:
                infos = z.infolist()
                if any(i.flag_bits & 0x1 for i in infos):
                    print('ZIP OVERGESLAGEN (beveiligd met wachtwoord):', zpath, flush=True)
                    continue
                stamp = dt.datetime.now().strftime('%Y%m%d_%H%M%S')
                backup = backup_dir / f'{stamp}_{zpath.name}'
                shutil.copy2(zpath, backup)
                inner_names = set()
                for d, target in entries:
                    inner = str(d['path']).split(ZIP_SEP, 1)[1]
                    info = z.getinfo(inner)
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_bytes(z.read(info))
                    ts = dt.datetime(*info.date_time).timestamp()
                    os.utime(target, (ts, ts))
                    inner_names.add(inner)
                rest = [i for i in infos if i.filename not in inner_names and not i.is_dir()]
                if rest:
                    tmp = zpath.with_name(zpath.name + '.nieuw')
                    with zipfile.ZipFile(tmp, 'w') as nz:
                        for i in infos:
                            if i.filename in inner_names:
                                continue
                            nz.writestr(i, z.read(i) if not i.is_dir() else b'', compress_type=i.compress_type)
            if rest:
                os.replace(tmp, zpath)
                zip_state = 'zip blijft staan zonder cv'
            else:
                zpath.unlink()
                zip_state = 'zip bevatte alleen cv\'s: verwijderd (backup bewaard)'
        except (OSError, zipfile.BadZipFile, KeyError, RuntimeError) as exc:
            print('ZIP NIET AANGEPAST:', zpath, '-', exc, flush=True)
            continue
        for d, target in entries:
            db.execute('DELETE FROM docs WHERE path=?', (str(target),))
            db.execute("UPDATE docs SET path=?, archive='' WHERE path=?", (str(target), str(d['path'])))
            reg[d['sha']] = 'uit zip'
            writer.writerow([dt.datetime.now().isoformat(timespec='seconds'), str(d['path']), str(target),
                             'uit zip', d['sha'], str(backup)])
            done += 1
        db.commit()
        print(f'UIT ZIP: {len(entries)} cv(\'s) uit {zpath.name} – {zip_state}.', flush=True)
    return done


def run_cleanup(db, out, dry_run=False):
    plan, skipped = cleanup_plan(db, out)
    moves = sum(1 for _o, _t, k in plan if k == 'verplaatsen')
    older = sum(1 for _o, _t, k in plan if k == 'oudere versie')
    dubbel = sum(1 for _o, _t, k in plan if k == 'dubbel')
    zipc = sum(1 for _o, _t, k in plan if k == 'uit zip')
    cloudc = sum(1 for _o, _t, k in plan if k == 'kopie uit cloud')
    extra = ', '.join(f'{n} {k}' for k, n in skipped.items())
    print(f'OPSCHOON-PLAN: {moves} nieuwste cv\'s naar {cv_folder(out)}, {older} oudere versies en '
          f'{dubbel} dubbele kopieën weggehaald (bewaard in cv_archief, terugzetten kan), {zipc} cv\'s uit zips gehaald, '
          f'{cloudc} cv\'s uit OneDrive/iCloud gekopieerd'
          + (f'; overgeslagen: {extra}' if extra else ''), flush=True)
    if dry_run or not plan:
        return 0
    log = out / CLEAN_LOG
    new_log = not log.exists()
    done = 0
    reg = load_register(out)
    cols = [c[1] for c in db.execute('PRAGMA table_info(docs)')]
    with log.open('a', encoding='utf-8-sig', newline='') as f:
        w = csv.writer(f, delimiter=';')
        if new_log:
            w.writerow(['datum', 'oude_plek', 'nieuwe_plek', 'soort', 'sha', 'backup'])
        done += _move_out_of_zips(db, out, [(o, t) for o, t, k in plan if k == 'uit zip'], w, reg)
        for old, target, kind in plan:
            if kind == 'uit zip':
                continue
            try:
                target.parent.mkdir(parents=True, exist_ok=True)
                if False:
                    pass
                else:
                    if target.exists() and kind in ('verplaatsen', 'oudere versie'):
                        target.unlink()       # oude kopie; het origineel komt ervoor in de plaats
                    sha = (db.execute('SELECT sha FROM docs WHERE path=?', (str(old),)).fetchone() or [''])[0]
                    arch = out / 'cv_archief' / f'{sha}{old.suffix.lower()}' if sha else None
                    if (kind in ('oudere versie', 'dubbel') and arch and arch.exists()
                            and arch.stat().st_size == old.stat().st_size):
                        # Samengevoegde kandidaat: alleen het nieuwste cv blijft. Oudere versie / exacte kopie weg;
                        # er staat een kopie in cv_archief, dus 'Terugzetten' kan hem terugzetten.
                        old.unlink()
                        db.execute('DELETE FROM docs WHERE path=?', (str(old),))
                        kind, target = 'verwijderd: ' + kind, arch
                    elif kind == 'kopie uit cloud':
                        # Origineel blijft in OneDrive/iCloud staan; alleen een kopie in de cv-map.
                        if target.exists():
                            target.unlink()
                        copy_document(old, target)
                    else:
                        shutil.move(str(old), str(target))
                        db.execute('DELETE FROM docs WHERE path=?', (str(target),))
                        db.execute('UPDATE docs SET path=? WHERE path=?', (str(target), str(old)))
            except OSError as exc:
                print('NIET VERPLAATST:', old, '-', exc, flush=True)
                continue
            db.commit()
            if sha:
                reg[sha] = kind
            w.writerow([dt.datetime.now().isoformat(timespec='seconds'), str(old), str(target), kind, sha, ''])
            f.flush()
            done += 1
    save_register(out, reg)
    print(f'OPGESCHOOND: {done} bestanden verplaatst, gekopieerd of (als oudere versie/dubbel) weggehaald. Terugzetten kan met "Opschonen terugzetten" '
          f'(zie {CLEAN_LOG}).', flush=True)
    return done


def undo_cleanup(db, out):
    """Alles terug zoals vóór 'Opschonen': originelen naar hun oude plek, kopieën uit zips weg."""
    log = out / CLEAN_LOG
    if not log.exists():
        print('TERUGZETTEN: niets om terug te zetten.', flush=True)
        return 0
    with log.open(encoding='utf-8-sig', newline='') as f:
        rows = list(csv.DictReader(f, delimiter=';'))
    reg = load_register(out)
    keep, done, removed = [], 0, 0
    restored_backups = set()
    for r in reversed(rows):
        if r.get('soort') == 'uit zip':
            # Zip terugzetten uit de backup (één keer per zip) en het losse cv weghalen.
            new = Path(r['nieuwe_plek'])
            sha = r.get('sha', '')
            if not new.exists() and sha:
                for (cand,) in db.execute('SELECT path FROM docs WHERE sha=?', (sha,)).fetchall():
                    if in_cv_folder(cand, out) and Path(cand).exists():
                        new = Path(cand)
                        break
            backup = Path(r.get('backup') or '')
            zpath = container_path(r['oude_plek'])
            try:
                if r.get('backup') and backup.exists() and str(backup) not in restored_backups:
                    shutil.copy2(backup, zpath)
                    restored_backups.add(str(backup))
                if new.exists():
                    new.unlink()
                db.execute('DELETE FROM docs WHERE path=?', (r['oude_plek'],))
                db.execute("UPDATE docs SET path=? WHERE path=?", (r['oude_plek'], str(new)))
                db.commit()
                reg.pop(sha, None)
                done += 1
            except OSError as exc:
                print('TERUGZETTEN (zip) mislukt:', zpath, '-', exc, flush=True)
                keep.append(r)
            continue
        if (r.get('soort') or '').startswith('verwijderd'):
            # Weggehaalde oudere versie / dubbele kopie: terugzetten uit cv_archief.
            old, bron = Path(r['oude_plek']), Path(r['nieuwe_plek'])
            try:
                if not old.exists() and bron.exists():
                    old.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(bron, old)
                    done += 1
                elif not bron.exists():
                    keep.append(r)
            except OSError as exc:
                print('TERUGZETTEN mislukt:', old, '-', exc, flush=True)
                keep.append(r)
            continue
        old, new = Path(r['oude_plek']), Path(r['nieuwe_plek'])
        sha = r.get('sha') or (db.execute('SELECT sha FROM docs WHERE path=?', (str(new),)).fetchone() or [''])[0]
        if not new.exists() and sha:
            # Intussen hernoemd of naar oudere_versies/dubbel verschoven: zoek hem op inhoud.
            for (cand,) in db.execute('SELECT path FROM docs WHERE sha=?', (sha,)).fetchall():
                if in_cv_folder(cand, out) and Path(cand).exists():
                    new = Path(cand)
                    break
        if not new.exists():
            continue
        if r.get('soort') in ('kopie uit zip', 'kopie uit cloud'):
            try:
                new.unlink()
                db.execute('DELETE FROM docs WHERE path=?', (str(new),))
                db.commit()
                removed += 1
                reg.pop(sha, None)
            except OSError as exc:
                print('Kopie niet verwijderd:', new, '-', exc, flush=True)
                keep.append(r)
            continue
        if old.exists():
            print('TERUGZETTEN overgeslagen (er staat al iets op de oude plek):', old, flush=True)
            keep.append(r)
            continue
        try:
            old.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(new), str(old))
        except OSError as exc:
            print('TERUGZETTEN mislukt:', new, '-', exc, flush=True)
            keep.append(r)
            continue
        db.execute('DELETE FROM docs WHERE path=?', (str(old),))
        db.execute('UPDATE docs SET path=? WHERE path=?', (str(old), str(new)))
        db.commit()
        if sha and not any(in_cv_folder(c, out) and Path(c).exists()
                           for (c,) in db.execute('SELECT path FROM docs WHERE sha=?', (sha,)).fetchall()):
            reg.pop(sha, None)
        done += 1
    with log.open('w', encoding='utf-8-sig', newline='') as f:
        w = csv.DictWriter(f, fieldnames=['datum', 'oude_plek', 'nieuwe_plek', 'soort', 'sha', 'backup'],
                           delimiter=';', extrasaction='ignore')
        w.writeheader()
        w.writerows(reversed(keep))
    save_register(out, reg)
    print(f'TERUGGEZET: {done} bestanden staan weer op hun oude plek'
          + (f', {removed} kopieën (uit zips/cloud) verwijderd' if removed else '') + '.', flush=True)
    return done


DECISIONS_FILE = 'twijfel_beslissingen.json'


def apply_decisions(db, out):
    """Jouw keuzes bij twijfelgevallen ('wel cv' / 'geen cv') gaan altijd voor de automatische herkenning."""
    try:
        decisions = json.loads((out / DECISIONS_FILE).read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return 0
    for sha, dec in decisions.items():
        if dec.get('status') == 'cv':
            db.execute("UPDATE docs SET status='cv', name_verified=1, name=COALESCE(NULLIF(?, ''), name), "
                       "reason='door gebruiker als cv gemarkeerd' WHERE sha=? AND status!='error'",
                       (dec.get('naam', ''), sha))
        elif dec.get('status') == 'geen':
            db.execute("UPDATE docs SET status='other', reason='door gebruiker als geen cv gemarkeerd' "
                       "WHERE sha=?", (sha,))
    db.commit()
    return len(decisions)


SCOPE = []          # gevuld bij 'Kies één map': Excel toont dan alleen die map(pen)


_ORIGINS = None


def _origin_of(path):
    """Voor een opgeschoond cv: waar stond het vóór 'Opschonen'? (uit het opschoon-logboek)"""
    global _ORIGINS
    if _ORIGINS is None:
        _ORIGINS = {}
        try:
            with (Path(OUT_DIR) / CLEAN_LOG).open(encoding='utf-8-sig', newline='') as f:
                for r in csv.DictReader(f, delimiter=';'):
                    _ORIGINS[r.get('sha', '')] = r.get('oude_plek', '')
        except (OSError, TypeError, csv.Error):
            pass
    return _ORIGINS


def in_scope(path, sha=''):
    if not SCOPE:
        return True
    if OUT_DIR and in_cv_folder(path, OUT_DIR):
        # Opgeschoond cv: hoort bij deze map als het daar oorspronkelijk vandaan kwam.
        path = _origin_of(path).get(sha, '') or path
    sp = str(path).split(ZIP_SEP)[0]
    sp_n = os.path.normcase(os.path.realpath(sp))
    return any(sp_n == r or sp_n.startswith(r.rstrip('\\/') + os.sep) for r in SCOPE)


def role_is_specific(role):
    """'Medewerker' of 'Sales medewerker' zegt niets; 'Fractievoorzitter' of
    'Internship Junior Projectmanager' (3+ woorden als geheel) wel."""
    words = [w for w in normalized_name(role).split() if len(w) >= 2]
    if len(words) >= 3:
        return True
    return any(w not in GENERIC_EVIDENCE and len(w) >= 3 for w in words)


MIN_JAREN_EIS = 5       # regel opdrachtgever: minimaal 5 jaar in NL/BE; 4 tot 5 jaar = marge (door)
EXCLUDED = {}           # pad -> reden 'niet meegenomen' (laatste export)


# ---- Stap 3-7: verblijfsduur in NL/BE ----------------------------------------------------------------------
MAANDEN = {'jan': 1, 'januari': 1, 'january': 1, 'feb': 2, 'februari': 2, 'february': 2, 'mrt': 3, 'mar': 3,
           'maart': 3, 'march': 3, 'apr': 4, 'april': 4, 'mei': 5, 'may': 5, 'jun': 6, 'juni': 6, 'june': 6,
           'jul': 7, 'juli': 7, 'july': 7, 'aug': 8, 'augustus': 8, 'august': 8, 'sep': 9, 'sept': 9,
           'september': 9, 'okt': 10, 'oct': 10, 'oktober': 10, 'october': 10, 'nov': 11, 'november': 11,
           'dec': 12, 'december': 12}
LOPEND = {'heden', 'present', 'nu', 'now', 'current', 'currently', 'today', 'huidig', 'actualidad', 'dd'}
NL_BE_SCHOOL_WOORD = re.compile(
    r' (?:basisschool|lagere school|middelbare school|secundair onderwijs|lyceum|gymnasium|scholengemeenschap|'
    r'atheneum|koninklijk atheneum|havo|vwo|vmbo|mavo|mbo|roc|hogeschool|universiteit|hbo|wo bachelor|'
    r'tu delft|tu eindhoven|hva|uva|vu|avans|fontys|saxion|windesheim|hanzehogeschool|inholland|nhl stenden|'
    r'zuyd|han|erasmus universiteit|ku leuven|ugent|vub|hogent|thomas more|odisee|vives|howest|pxl|'
    r'karel de grote|artesis|ap hogeschool|ucll|uhasselt|uantwerpen) ')
ONLINE_RE = re.compile(r' (?:online|remote|op afstand|afstandsonderwijs|distance learning|e learning|thuiswerk|'
                       r'work from home|wfh|vanuit nederland|vanuit belgie|from the netherlands|from belgium) ')
AANKOMST_RE = re.compile(
    r' (?:(?:moved|relocated|came|arrived|migrated|verhuisd|verhuist|gekomen|aangekomen|geemigreerd)'
    r'(?: (?:to|in|naar))? (?:the )?(?P<land1>netherlands|nederland|belgium|belgie|holland)'
    r'(?: (?:in|since|sinds))? (?:(?:[a-z]+|\d{1,2}) )?(?P<j1>(?:19|20)\d\d)|'
    r'(?:living|wonend|woonachtig|resident|based|woon|woont|wonen)(?: [a-z]+){0,2} (?:in )?(?:the )?'
    r'(?P<land2>netherlands|nederland|belgium|belgie|holland) (?:since|sinds|vanaf) (?:(?:[a-z]+|\d{1,2}) )?'
    r'(?P<j2>(?:19|20)\d\d)|'
    r'(?:sinds|since) (?:(?:[a-z]+|\d{1,2}) )?(?P<j3>(?:19|20)\d\d) (?:woonachtig |wonend |living |based |resident )?'
    r'in (?:the )?(?P<land3>netherlands|nederland|belgium|belgie|holland)|'
    r'(?:in|naar|to) (?:the )?(?P<land4>netherlands|nederland|belgium|belgie|holland) (?:sinds|since|vanaf) '
    r'(?:(?:[a-z]+|\d{1,2}) )?(?P<j4>(?:19|20)\d\d)) ')


def _jaar(tok):
    """Jaartal uit een woord; duidelijke OCR-fout (2O18 -> 2018) wordt hersteld. Onmogelijke jaren: 0."""
    t = tok.translate(str.maketrans({'o': '0', 'l': '1', 'i': '1'})) if sum(ch.isdigit() for ch in tok) >= 3 else tok
    if not re.fullmatch(r'(?:19|20)\d\d', t):
        return 0
    y = int(t)
    return y if 1950 <= y <= dt.date.today().year else 0


def _maand(tok):
    if tok in MAANDEN:
        return MAANDEN[tok]
    return int(tok) if re.fullmatch(r'0?[1-9]|1[0-2]', tok) else 0


def periodes_uit_cv(snippet, voor_n=4):
    """Alle periodes met jaartallen: [(begin_maand, eind_maand, venster_tekst, exact)].
    Maandnummer = jaar*12 + maand-1. Notaties: 2015 2020, 03 2018 06 2022, mar 2018 jun 2022, 2021 heden,
    maart 2019 present. Alleen jaartallen: geen maanden verzonnen; de periode loopt dan van midden tot midden
    (2015-2020 = 5 jaar). Onmogelijke of omgekeerde periodes worden overgeslagen (niet gokken)."""
    words = _woorden_en_cijfers(snippet or '').split()
    nu = dt.date.today()
    nu_m = nu.year * 12 + nu.month - 1
    uit, i = [], 0
    while i < len(words):
        y1 = _jaar(words[i])
        if not y1:
            i += 1
            continue
        m1 = _maand(words[i - 1]) if i > 0 and (words[i - 1] in MAANDEN or (re.fullmatch(r'\d{1,2}', words[i - 1])
                                                and not _jaar(words[i - 1]))) else 0
        j, eind, m2, lopend = i + 1, 0, 0, False
        # einde binnen 3 woorden: (maand) jaar, of heden/present
        for k in range(i + 1, min(i + 4, len(words))):
            w = words[k]
            if w in LOPEND:
                lopend, j = True, k + 1
                break
            if _jaar(w) or re.fullmatch(r'(?:19|20)\d\d', w):
                eind = _jaar(w)
                if k > i + 1 and (words[k - 1] in MAANDEN or re.fullmatch(r'0?[1-9]|1[0-2]', words[k - 1])):
                    m2 = _maand(words[k - 1])
                j = k + 1
                if not eind:
                    eind = -1                      # onmogelijk jaartal (toekomst/OCR-onzin)
                break
            if w not in MAANDEN and w not in ('tot', 'to', 'until', 'till', 'en', 't', 'm', 'tm') \
                    and not re.fullmatch(r'0?[1-9]|1[0-2]', w):
                break
        if eind == -1:
            i = j
            continue
        if not eind and not lopend:
            i += 1                                 # los jaartal: geen periode
            continue
        exact = bool(m1) and (bool(m2) or lopend)
        b = y1 * 12 + (m1 - 1 if m1 else 6)
        e = nu_m if lopend else (eind * 12 + (m2 - 1 if m2 else 6))
        if e < b or e > nu_m:
            i = j
            continue                               # tegenstrijdig: einde voor begin
        venster = ' ' + ' '.join(words[j:j + 25]) + ' '
        voor = ' ' + ' '.join(words[max(0, i - voor_n):i]) + ' '
        uit.append((b, max(e, b), venster, exact, voor))
        i = j
    return uit


def _is_nl_be(venster):
    nl = ' ' + venster
    plaats = any(f' {p} ' in nl for p in NL_PLACES | BE_PLACES) or any(f' {p} ' in nl for p in NL_LAND | BE_LAND)
    return plaats or bool(NL_BE_SCHOOL_WOORD.search(nl))


def _is_buitenland(venster):
    buiten = (FOREIGN_PLACES | INDIA_PLACES) - BUITENLAND_IGNORE - BE_PLACES
    return any(f' {p} ' in venster for p in buiten)


def verblijf_beoordelen(snippet):
    """Verblijfsduur in NL/BE volgens het cv. Geeft (uitkomst, jaren, uitleg):
    - 'bevestigd'  : 5 jaar of meer aantoonbaar (werk/opleiding/adres met NL/BE-plaats of NL/BE-school)
    - 'marge'      : 4 tot 5 jaar aantoonbaar (door, maar 5 jaar is NIET bevestigd)
    - 'te_kort'    : het cv zegt zelf wanneer iemand naar NL/BE kwam (bv. 'moved to the Netherlands in 2024')
                     en er staat niets van eerder verblijf in NL/BE -> volledige verblijfsduur bekend en < 4 jaar
    - 'onbekend'   : minder zichtbaar, maar geen bewijs van de volledige duur -> gewoon door
    Overlap telt één keer; gaten zijn geen buitenland; werk-/schoolplaats is aanwijzing, geen woonplaats.
    Online/remote vanuit NL/BE telt als NL/BE; online vanuit buitenland niet als NL/BE."""
    nu = dt.date.today()
    nu_m = nu.year * 12 + nu.month - 1
    maanden = set()
    vanuit_nlbe = re.compile(r' (?:vanuit|from) (?:the )?(?:nederland|netherlands|belgie|belgium) ')
    for b, e, venster, _exact, voor in periodes_uit_cv(snippet):
        stuk = venster[:160]
        ctx = voor + stuk                                   # 'online cursus 2019 2022 ...' staat soms ervoor
        if vanuit_nlbe.search(ctx):
            maanden |= set(range(b, e + (1 if e == b else 0)))   # remote/online vanuit NL/BE telt als NL/BE
        elif _is_nl_be(stuk) and not (ONLINE_RE.search(ctx) and _is_buitenland(ctx)):
            maanden |= set(range(b, e + (1 if e == b else 0)))
    jaren = round(len(maanden) / 12, 1)
    text = ' ' + _woorden_en_cijfers(snippet or '') + ' '
    aankomst = 0
    for m in AANKOMST_RE.finditer(text):
        y = _jaar(m.group('j1') or m.group('j2') or m.group('j3') or m.group('j4'))
        if y:
            aankomst = min(aankomst, y) if aankomst else y
    if aankomst:
        sinds = nu_m - (aankomst * 12 + 6)
        eerder = [x for x in maanden if x < aankomst * 12]
        if eerder:
            return ('onbekend' if jaren < MIN_JAREN_NL else 'marge' if jaren < MIN_JAREN_EIS else 'bevestigd',
                    jaren, f'kwam {aankomst}, maar ook eerder in NL/BE')     # tegenstrijdig: niet gokken
        jaren = max(jaren, round(sinds / 12, 1))
        if jaren < MIN_JAREN_NL:
            return 'te_kort', jaren, f'in NL/BE sinds {aankomst}'
    if jaren >= MIN_JAREN_EIS:
        return 'bevestigd', jaren, ''
    if jaren >= MIN_JAREN_NL:
        return 'marge', jaren, ''
    return 'onbekend', jaren, ''


def _periodes_met_plaats(snippet):
    text = ' ' + _woorden_en_cijfers(snippet or '') + ' '
    plekken = (NL_PLACES | BE_PLACES | FOREIGN_PLACES | INDIA_PLACES) - BUITENLAND_IGNORE
    n = 0
    for m in re.finditer(r'(?<!\d)(?:19[5-9]\d|20[0-3]\d)(?!\d)', text):
        window = text[m.end():m.end() + 120] + ' '
        if any(f' {p} ' in window for p in plekken):
            n += 1
    return n


def filter_kandidaat(doc, phones, emails, ai_vraag):
    """Mag deze kandidaat mee? ('ok', '') / ('weg', reden) / ('controleren', reden).
    1. Taal van het cv (Spaans of een Indiase taal = niet meenemen).
    2. Woont nu in Nederland of België.
    3. Minimaal 5 jaar in NL/BE (4-5 jaar = twijfel).
    Bij twijfel kijkt de AI mee; pas als het daarna nog onduidelijk is: 'controleren'. Niemand gaat eruit op
    een aanname: alleen als het cv het duidelijk laat zien (of de AI het bevestigt)."""
    snippet = doc['snippet'] or ''
    taal = (doc['taal'] if 'taal' in doc.keys() else '') or ''
    taal = '' if taal == '-' else taal
    if not taal:
        taal = cv_hoofdtaal(snippet)
    # 1. Taal
    if taal in ('es', 'indiaas'):
        return 'weg', 'taal van het cv'
    if taal == 'onduidelijk':
        a = ai_vraag('taal')
        if a in ('spaans', 'indiase_taal'):
            return 'weg', 'taal van het cv'
        if a not in ('nederlands', 'engels', 'andere_taal'):
            return 'controleren', 'taal van het cv onduidelijk'
    # 2. Woont nu in NL/BE. Alleen uitsluiten bij betrouwbaar bewijs; onzeker of onbekend = gewoon door.
    land, _waar = woonplaats_bepalen(snippet, phones, emails, doc)
    if land == 'buitenland':
        if ai_vraag('woont') != 'ja':
            if 'mobiel nummer' in _waar:
                return 'weg', ('Spaans' if 'Spaans' in _waar else 'Indiaas') + ' eigen mobiel nummer'
            return 'weg', 'woont niet in Nederland of België'
    elif land in ('tegenstrijdig', 'aanwijzing_buitenland'):
        # Twee verschillende AI-vragen moeten het eens zijn voordat iemand wegvalt (geen onterechte uitsluiting).
        if ai_vraag('woont') == 'nee' and ai_vraag('woont_buiten') == 'ja':
            return 'weg', 'woont niet in Nederland of België'
    # 3. Minimaal 5 jaar in NL/BE. Alleen uitsluiten als het cv zelf de VOLLEDIGE verblijfsduur noemt
    #    ('sinds 2024 in Nederland') en die korter is dan 4 jaar. Opgetelde werk-/studiejaren zijn nooit genoeg
    #    om uit te sluiten: ontbrekende jaren zijn geen bewijs. 4-5 jaar = door (marge), niet als 5 jaar bevestigd.
    uitkomst, _jaren, _uitleg = verblijf_beoordelen(snippet)
    if uitkomst == 'te_kort':
        if ai_vraag('verblijf') != 'ja':            # AI kan het alleen nog redden, nooit zelf uitsluiten
            return 'weg', f'korter dan {MIN_JAREN_EIS} jaar in Nederland of België'
    return 'ok', ''


WERK_STOP = {'de', 'het', 'een', 'the', 'a', 'an', 'een', 'diverse', 'verschillende', 'eigen', 'own', 'various'}


def werk_periodes(d):
    """(werkgever, beginjaar, eindjaar) uit het HELE uitgelezen cv: elke periode met jaartallen en daarna
    'bij X' / 'at X' / '@ X'. Werkgever = eerste woord na bij/at (robuust, ook als de zin doorloopt)."""
    uit = []
    for b, e, venster, _exact, _voor in periodes_uit_cv(d['snippet'] or ''):
        stuk = ' ' + ' '.join(venster.split()[:12]) + ' '
        m = re.search(r' (?:bij|at|voor|for) ([a-z0-9]+(?: [a-z0-9]+)?)', stuk)
        if not m:
            continue
        if BIG_EMPLOYERS.match(m.group(1)) or BIG_EMPLOYERS.match(m.group(1).split()[0]):
            w = '*' + m.group(1).split()[0]          # groot bedrijf: telt mee voor botsingen, niet als bewijs
        else:
            w = m.group(1).split()[0]
        if len(w) < 3 or w in WERK_STOP or w in NL_PLACES:
            continue
        uit.append((w, b // 12, e // 12))
    return uit


def loopbaan_botst(a, b):
    """Twee cv's met elk minstens 2 banen, die in DEZELFDE jaren bij ANDERE werkgevers werkten (die werkgever
    komt in het andere cv niet voor) en geen enkele werkgever gemeen hebben = twee verschillende personen."""
    pa, pb = werk_periodes(a), werk_periodes(b)
    if not pa or not pb or len(pa) + len(pb) < 3:
        return False
    # 'heden' in een OUD cv betekent 'tot het cv geschreven werd', niet 'tot vandaag'. Oud cv: '2010 - heden bij
    # Rivierdata'; nieuw cv: 'Rivierdata 2012-2016, Polderlogic 2016-heden' is geen botsing maar een nieuwere versie.
    def tot_cv_jaar(lijst, ander):
        nu = dt.date.today().year
        laatste = max(y1 for _w, y1, _e in lijst)
        laatste_ander = max(y1 for _w, y1, _e in ander)
        if laatste >= laatste_ander:
            return lijst                          # dit is het nieuwste cv: 'heden' blijft heden
        # Alleen als die 'heden'-werkgever in het nieuwere cv ook staat (met een einde): baan is daar afgesloten.
        # En alleen als ALLE banen uit het oude cv ook in het nieuwe staan (zelfde werkgever, beginjaar ±1 jaar):
        # dan is het oude cv echt een eerdere versie van het nieuwe.
        def staat_erin(w, y1):
            return any(w == w2 and abs(y1 - z1) <= 1 for w2, z1, _e in ander)
        afgesloten = [(w, y1) for w, y1, e in ander if e < nu]
        if not all(staat_erin(w, y1) for w, y1, _e in lijst):
            return lijst
        return [(w, y1, min(e, laatste + 1) if e >= nu and any(w == w2 and abs(y1 - z1) <= 1 for w2, z1 in afgesloten)
                 else e) for w, y1, e in lijst]
    pa, pb = tot_cv_jaar(pa, pb), tot_cv_jaar(pb, pa)
    def ook_daar(lijst, w, b, e):
        # Staat werkgever w in het andere cv in (ongeveer) dezelfde jaren? (1 jaar speling)
        return any(x == w and min(e, y2) - max(b, y1) >= 0 for x, y1, y2 in lijst)
    for w1, b1, e1 in pa:
        for w2, b2, e2 in pb:
            # Minstens 2 jaar tegelijk bij twee verschillende werkgevers, en geen van beide cv's noemt de
            # werkgever van het andere cv in die jaren.
            lo, hi = max(b1, b2), min(e1, e2)
            if (w1 != w2 and hi - lo >= 2
                    and not ook_daar(pb, w1, lo + 1, hi - 1) and not ook_daar(pa, w2, lo + 1, hi - 1)):
                return True
    return False


OPL_WOORD = re.compile(r' (?:hbo|mbo|wo|bachelor|master|bsc|msc|universiteit|university|hogeschool|college|'
                       r'opleiding|studie|propedeuse|informatica) ')


def _opl_niveau(venster):
    v = ' ' + ' '.join(venster.split()[:6]) + ' '
    if re.search(r' (?:master|msc|ma|mba|wo master|postdoc|phd) ', v):
        return 'master'
    if re.search(r' (?:hbo|bachelor|bsc|ba|b tech|btech|propedeuse) ', v):
        return 'bachelor'
    # 'WO Informatica' is een universitaire studie, maar het woord 'WO'
    # zegt op zichzelf niet of er een bachelor of master is behaald.
    if re.search(r' wo ', v):
        return 'wo'
    if re.search(r' mbo ', v):
        return 'mbo'
    return ''


# Een opleiding kan zich meerdere keren in een loopbaan voordoen (twee bachelors, overstap, master na HBO).
# Lees daarom de afzonderlijke opleidingen, niet alleen het niveau en niet de huidige woonplaats.
OPLEIDING_RICHTINGEN = ('informatica', 'bedrijfskunde', 'data science', 'computer science',
                        'software engineering', 'commerciele economie', 'ict', 'rechten',
                        'verpleegkunde', 'werktuigbouwkunde', 'psychologie')


def opleiding_periodes(d):
    """(niveau, richting, school, plaats_bij_school, beginjaar, eindjaar).
    Een adres/woonplaats verderop in het cv is nooit de plaats van een opleiding."""
    out = []
    for begin, eind, venster, _exact, _voor in periodes_uit_cv(d['snippet'] or ''):
        words = venster.split()
        eerste = ' ' + ' '.join(words[:8]) + ' '
        if not OPL_WOORD.search(eerste) or re.search(r' (?:bij|at) ', ' ' + ' '.join(words[:5]) + ' '):
            continue
        niveau = _opl_niveau(venster)
        if not niveau:
            continue
        # De eerstvolgende cv-sectie/contactregel hoort niet bij de school.
        kort = re.split(r'\b(?:woonplaats|adres|werkervaring|experience|vaardigheden|skills|'
                        r'telefoon|mobiel|linkedin|huidige functie)\b',
                        ' '.join(words[:16]), maxsplit=1)[0]
        # Een periodevenster bevat soms ook de VOLGENDE studieperiode. Stop vóór
        # het volgende jaartal, zodat 'Hogeschool Utrecht 2010' niet als schoolnaam
        # wordt opgeslagen wanneer het tweede diploma in 2010 begint.
        kort = re.split(r'\b(?:19|20)\d{2}\b', kort, maxsplit=1)[0].strip()
        richting = next((v for v in OPLEIDING_RICHTINGEN if re.search(r'\b' + re.escape(v) + r'\b', kort)), '')
        m = re.search(r'\b(?:hogeschool|hogescholen|universiteit|university|college|roc)\b', kort)
        school = ''
        plaats = ''
        if m:
            # 'Hogeschool van Amsterdam' -> Amsterdam; 'Hogeschool Utrecht' -> Utrecht.
            # 'Woonplaats Amsterdam' na 'Hogeschool Utrecht' mag deze plaats niet vervangen.
            na = kort[m.end():].split()
            na = [w for w in na if w not in ('van', 'de', 'het', 'der', 'technical', 'skills', 'skill',
                                             'profiel', 'profile', 'vaardigheden') and not w.isdigit()][:3]
            plaats = _plaats_in(' ' + ' '.join(na) + ' ', NL_PLACES | BE_PLACES)
            school = kort[:m.end()].split()[-1] + ' ' + ' '.join(na[:1])
            school = school.strip()
        else:
            # Ook Fontys, Avans of Saxion tellen als opleiding; locatie is dan onbekend.
            for naam in ('fontys', 'avans', 'saxion', 'hanzehogeschool', 'inholland', 'stenden'):
                if re.search(r'\b' + naam + r'\b', kort):
                    school = naam
                    break
        out.append((niveau, richting, school, plaats, begin // 12, eind // 12))
    return out


def opleiding_overeenkomsten(a, b):
    """Dezelfde volledige opleiding: niveau + richting + school + begin- en eindjaar.
    Alleen 'HBO' of alleen 'Hogeschool Utrecht' is onvoldoende bewijs."""
    uit = set()
    for niveau, richting, school, plaats, jaar, einde in opleiding_periodes(a):
        if not richting or not school:
            continue
        for nv2, ri2, sc2, pl2, jr2, eind2 in opleiding_periodes(b):
            if (niveau, richting, school, jaar, einde) == (nv2, ri2, sc2, jr2, eind2):
                uit.add(f'{niveau} {richting} - {school} ({jaar}-{einde})')
    return sorted(uit)


def _zelfde_opleiding_record(x, y):
    """Eén specifieke studie, met jaar, richting en instelling, in twee cv's."""
    nv, richting, school, _plaats, jaar, einde = x
    nv2, richting2, school2, _plaats2, jaar2, einde2 = y
    return (nv == nv2 and richting and richting == richting2
            and school and school == school2
            and abs(jaar - jaar2) <= 1 and abs(einde - einde2) <= 1)


def opleiding_botst(a, b):
    """Twee cv's vermelden per opleidingsniveau maar één duidelijk verschillende opleiding:
    dan is dat GEEN bewijs voor twee studies van dezelfde kandidaat. Behandel dit als
    twee personen (zonder dubbel-twijfelgeval), tenzij een ander cv beide studies bewijst.
    Verschillende niveaus (bachelor/master), ontbrekende informatie en twee opleidingen
    in één cv zijn op zichzelf géén conflict.
    """
    pa, pb = opleiding_periodes(a), opleiding_periodes(b)
    for niveau in {o[0] for o in pa} & {o[0] for o in pb}:
        la = [x for x in pa if x[0] == niveau]
        lb = [x for x in pb if x[0] == niveau]
        if len(la) != 1 or len(lb) != 1:
            continue     # CV noemt zelf meerdere studies op hetzelfde niveau
        x, y = la[0], lb[0]
        if _zelfde_opleiding_record(x, y):
            continue
        _nv, richting, school, plaats, jaar, eind = x
        _nv2, richting2, school2, plaats2, jaar2, eind2 = y
        # Alleen blokkeren op echte, voldoende specifieke tegenspraak. Een andere
        # woonplaats kan hier niet meetellen: plaats is direct aan de school gekoppeld.
        voldoende = (richting or school) and (richting2 or school2)
        andere_richting = bool(richting and richting2 and richting != richting2)
        andere_school = bool(school and school2 and school != school2)
        andere_plaats = bool(plaats and plaats2 and plaats != plaats2)
        andere_jaren = abs(jaar - jaar2) > 1 or abs(eind - eind2) > 1
        if voldoende and (andere_richting or andere_school or andere_plaats or andere_jaren):
            return True
    return False


def opleidingen_verzoend(a, b, groep):
    """Een cv dat BEIDE afwijkende studies expliciet noemt kan een groepsbotsing verzoenen.
    De twee enkele cv's op zichzelf blijven terecht verschillend."""
    if not opleiding_botst(a, b):
        return True
    pa, pb = opleiding_periodes(a), opleiding_periodes(b)
    for doc in groep:
        studies = opleiding_periodes(doc)
        if len(studies) < 2:
            continue
        if (any(_zelfde_opleiding_record(x, s) for x in pa for s in studies)
                and any(_zelfde_opleiding_record(y, s) for y in pb for s in studies)):
            return True
    return False


MAAND_WOORDEN = {'jan', 'januari', 'january', 'feb', 'februari', 'february', 'mrt', 'maart', 'mar', 'march', 'apr', 'april',
                 'mei', 'may', 'jun', 'juni', 'june', 'jul', 'juli', 'july', 'aug', 'augustus', 'august', 'sep', 'sept',
                 'september', 'okt', 'oktober', 'oct', 'october', 'nov', 'november', 'dec', 'december'}


def zelfde_werk(a, b):
    """Dezelfde (niet-grote) werkgever met (bijna) dezelfde beginjaar in beide cv's: 'Datafabriek 2017-2021'."""
    pa, pb = werk_periodes(a), werk_periodes(b)
    uit = {f'{w} {y1}' for w, y1, _e in pa for w2, z1, _f in pb if w == w2 and not w.startswith('*') and y1 == z1}
    # Andere opmaak in het andere cv ('aug 2015 – heden / Tulpnet – Azure consultant', zonder 'bij'): staat de
    # werkgever uit het ene cv in het andere cv bij een periode met precies hetzelfde beginjaar?
    def vensters(d):
        # 'Tulpnet – Azure consultant (aug 2015 – heden)': werkgever kan ook vlak vóór de periode staan.
        # Alleen als de periode zelf geen 'bij/at X' heeft; anders is wat ervoor staat de vorige baan.
        uit = []
        for bm, _em, v, _x, vo in periodes_uit_cv(d['snippet'] or '', voor_n=6):
            na = v.split()[:14]
            voor = [w for w in vo.split()]
            while voor and voor[-1] in MAAND_WOORDEN:
                voor.pop()                         # 'Tulpnet, consultant (mar 2019': maand eraf
            if re.search(r' (?:bij|at|@) ', ' ' + ' '.join(na[:6]) + ' '):
                voor = []
            uit.append((bm // 12, ' ' + ' '.join(voor[-4:] + na) + ' '))
        return uit
    for lijst, ander in ((pa, b), (pb, a)):
        vs = vensters(ander)
        for w, y1, _e in lijst:
            if not w.startswith('*') and any(z == y1 and f' {w} ' in v for z, v in vs):
                uit.add(f'{w} {y1}')
    return sorted(uit)


def cv_samenvatting(d, max_chars=1800):
    """Korte, gestructureerde samenvatting van het hele cv voor de AI (in plaats van alleen het begin)."""
    regels = [f"naam: {d['name'] or '-'}"]
    try:
        regels.append('e-mail: ' + ', '.join(json.loads(d['emails'] or '[]')[:3]))
        regels.append('telefoon: ' + ', '.join(json.loads(d['phones'] or '[]')[:3]))
    except ValueError:
        pass
    per = periodes_uit_cv(d['snippet'] or '')
    for b, e, venster, _x, _v in per[:12]:
        regels.append(f"{b // 12}-{e // 12}: " + ' '.join(venster.split()[:10]))
    if 'opleidingen' in d.keys() and d['opleidingen']:
        regels.append('opleiding: ' + str(d['opleidingen'])[:160])
    tekst = '\n'.join(regels)
    if len(tekst) < 600:
        tekst += '\n' + (d['snippet'] or '')[:max_chars - len(tekst)]
    return tekst[:max_chars]


def _bewijs_stukken(a, b):
    """Onafhankelijke Python-bewijzen dat twee cv's van dezelfde persoon zijn (bureaugegevens tellen niet)."""
    stukken = []
    def sets(d, k):
        try:
            return set(json.loads(d[k] or '[]')) - BUREAU_CONTACT
        except ValueError:
            return set()
    if {e for e in sets(a, 'emails') & sets(b, 'emails') if not GENERIC_EMAIL.match(e)}:
        stukken.append('zelfde persoonlijk e-mailadres')
    if sets(a, 'phones') & sets(b, 'phones'):
        stukken.append('zelfde telefoonnummer')
    if sets(a, 'links') & sets(b, 'links'):
        stukken.append('zelfde LinkedIn')
    an, bn = normalized_name(a['name']), normalized_name(b['name'])
    if an and an == bn and len(an.split()) >= 2:
        stukken.append('zelfde volledige naam')
    def lijst(d, k):
        v = d[k] if k in d.keys() else ''
        return {normalized_name(x) for x in re.split(r'\s*[;|]\s*', v or '') if len(normalized_name(x)) >= 4}
    zw = zelfde_werk(a, b)
    if zw:
        stukken.append('zelfde werkgever met zelfde jaren (' + zw[0] + ')')
    else:
        wg = {w for w in lijst(a, 'werkgevers') & lijst(b, 'werkgevers') if not BIG_EMPLOYERS.match(w)}
        if wg:
            stukken.append('zelfde werkgever (' + sorted(wg)[0] + ')')
    if lijst(a, 'opleidingen') & lijst(b, 'opleidingen'):
        stukken.append('zelfde opleiding')
    return stukken


def ai_dubbel_controle(docs, reviews, samenvoegen, out_dir):
    """'Mogelijk dubbel'-paren zoveel mogelijk automatisch oplossen:
    1. Python: harde blokkades (andere voornaam, andere persoon in bestandsnaam) -> nooit samen.
    2. Python: minstens 2 onafhankelijke bewijzen (persoonlijk mailadres, telefoon, LinkedIn, volledige naam,
       werkgever, opleiding); bureaugegevens tellen niet.
    3. AI vergelijkt beide cv's (houdt rekening met oude/nieuwe versie en andere taal).
    4. Tweede, gerichte AI-vraag: een concreet feit dat in beide staat (werkgever+jaren, opleiding+jaar).
    5. Python: past het bij ALLE cv's van de kandidaatgroep? Pas dan samen. Anders blijft het een twijfelgeval."""
    if not reviews:
        return reviews
    # Stap A (Python, zonder AI): persoonlijk contactgegeven gedeeld (geen bureau) + dezelfde specifieke werkgever
    # met dezelfde jaren + geen harde blokkade + past in de hele groep -> samen.
    rest = []
    for a, b, reason in reviews:
        da, db_ = docs[a], docs[b]
        st = _bewijs_stukken(da, db_)
        contact = any(x.startswith(('zelfde persoonlijk', 'zelfde telefoon', 'zelfde LinkedIn')) for x in st)
        if (not harde_blokkade(da, db_) and contact and zelfde_werk(da, db_) and not opleiding_botst(da, db_)
                and samenvoegen(a, b)):
            continue
        rest.append((a, b, reason))
    reviews = rest
    if not reviews or os.environ.get('CV_AI_DUBBEL', '0') != '1':   # AI pas aan na geslaagde AI-test
        return reviews
    try:
        import ai_rechter
        if not ai_rechter.available():
            return reviews
    except Exception:
        return reviews
    cache_path = (out_dir / 'ai_dubbel.json') if out_dir else None
    try:
        cache = json.loads(cache_path.read_text(encoding='utf-8')) if cache_path else {}
    except (OSError, ValueError):
        cache = {}
    over, samen = [], 0
    for a, b, reason in reviews:
        da, db_ = docs[a], docs[b]
        if harde_blokkade(da, db_) or len(_bewijs_stukken(da, db_)) < 2:
            over.append((a, b, reason))
            continue
        key = f"{ai_rechter.MODEL_ID}|" + '|'.join(sorted((da['sha'] or da['path'], db_['sha'] or db_['path'])))
        res = cache.get(key)
        if res is None:
            res = ai_rechter.zelfde_persoon(cv_samenvatting(da), cv_samenvatting(db_))
            if res is None:
                over.append((a, b, reason))
                continue
            if not res.get('fout'):
                cache[key] = res
        if res.get('oordeel') == 'zelfde' and samenvoegen(a, b):
            samen += 1
            continue
        over.append((a, b, reason))
    if cache_path:
        try:
            cache_path.write_text(json.dumps(cache), encoding='utf-8')
        except OSError:
            pass
    if samen:
        print(f'AI-DUBBEL: {samen} mogelijk dubbele kandidaten automatisch samengevoegd (Python + 2x AI akkoord).', flush=True)
    return over


def deduplicate(db, merge_file=None):
    if merge_file:
        apply_decisions(db, Path(merge_file).parent)
    EXCLUDED.clear()
    out_dir = Path(merge_file).parent if merge_file else None
    # Twijfelgevallen in het Spaans of een Indiase taal: niet meenemen (geen handwerk nodig).
    try:
        _gekozen = set(json.loads((out_dir / DECISIONS_FILE).read_text(encoding='utf-8'))) if out_dir else set()
    except (OSError, ValueError):
        _gekozen = set()
    for r in db.execute("SELECT path, sha, snippet, taal, status, reason FROM docs WHERE status='review' "
                        "OR (status='other' AND reason LIKE '%naam: ja%' AND reason LIKE '%contact: ja%')").fetchall():
        if r['sha'] in _gekozen:
            continue          # jouw eigen keuze in het twijfel-venster gaat voor
        taal = (r['taal'] if r['taal'] not in (None, '', '-') else '') or cv_hoofdtaal(r['snippet'] or '')
        if taal in ('es', 'indiaas'):
            db.execute("UPDATE docs SET status='other', reason='niet meegenomen: taal van het cv' WHERE path=?", (r['path'],))
    # Bureau-cv's: bovenaan staat de recruiter ('Elmer van den Heuvel'), de kandidaat staat in de bestandsnaam
    # ('denBulck Tom HQ… .txt') én in het cv. Dan geldt de naam uit de bestandsnaam (ook voor al gelezen cv's).
    for r in db.execute("SELECT path, name, snippet, emails FROM docs WHERE status IN ('cv','review') AND name!=''").fetchall():
        try:
            _em = json.loads(r['emails'] or '[]')
        except ValueError:
            _em = []
        aangevuld = naam_aanvullen(r['name'], _em, r['snippet'])
        if aangevuld != r['name']:
            db.execute("UPDATE docs SET name=? WHERE path=?", (aangevuld, r['path']))
            continue
        beter = naam_botst_met_bestand(r['name'], r['path'], r['snippet'])
        if beter:
            try:
                eigen = [e for e in json.loads(r['emails'] or '[]') if not GENERIC_EMAIL.match(e)]
            except ValueError:
                eigen = []
            if not any(email_matches_name(r['name'], e) for e in eigen):
                db.execute("UPDATE docs SET name=? WHERE path=?", (beter, r['path']))
    db.commit()
    try:
        decided = json.loads((out_dir / DECISIONS_FILE).read_text(encoding='utf-8')) if out_dir else {}
    except (OSError, ValueError):
        decided = {}
    ai_cache_path = (out_dir / 'ai_filters.json') if out_dir else None
    try:
        ai_cache = json.loads(ai_cache_path.read_text(encoding='utf-8')) if ai_cache_path else {}
    except (OSError, ValueError):
        ai_cache = {}
    try:
        import ai_rechter
        ai_model = ai_rechter.MODEL_ID
    except Exception:
        ai_model = 'geen'
    docs = db.execute("SELECT * FROM docs WHERE status='cv' AND classifier_version=? AND name_verified=1 AND name!='' ORDER BY path", (CLASSIFIER_VERSION,)).fetchall()
    docs = [d for d in docs if in_scope(d['path'], d['sha'])]
    dsu = DSU(len(docs))
    by_sha, by_email, by_phone, by_link, by_name = (collections.defaultdict(list) for _ in range(5))
    for i, d in enumerate(docs):
        by_sha[d['sha']].append(i)
        for email in json.loads(d['emails']): by_email[email].append(i)
        for phone in json.loads(d['phones']): by_phone[phone].append(i)
        for link in json.loads(d['links']): by_link[link].append(i)
        name = normalized_name(d['name'])
        if name:
            by_name[name].append(i)
            kern = [w for w in name.split() if w not in NAME_FILLERS]
            if len(kern) >= 2:
                # ook 'Erwin Boven' ~ 'Erwin van Boven' en 'Michel Bisschof' ~ 'Michael Bisschoff' vergelijken
                by_name['~' + kern[0][0] + ' ' + kern[-1][:5]].append(i)
    # Bureau-/recruitergegevens: een e-mail, nummer of LinkedIn dat in cv's van 3+ verschillende namen staat.
    BUREAU_CONTACT.clear()
    for groups in (by_email, by_phone, by_link):
        for value, idxs in groups.items():
            namen = {normalized_name(naam_uit_bestandsnaam(docs[i]['path'], docs[i]['snippet']) or docs[i]['name'])
                     for i in idxs} - {''}
            if len(namen) >= 3 or (groups is by_email and GENERIC_EMAIL.match(value)):
                BUREAU_CONTACT.add(value)
    candidates = set()
    for groups in (by_sha, by_email, by_phone, by_link, by_name):
        for group in groups.values():
            if len(group) > 1:
                for idx, a in enumerate(group):
                    for b in group[idx+1:]: candidates.add((min(a,b), max(a,b)))
    GESPLITST.clear()
    reviews = []
    component_names = {i: ({normalized_name(docs[i]['name'])} if docs[i]['name'] else set())
                       for i in range(len(docs))}
    leden = {i: [i] for i in range(len(docs))}

    def groep_botst(ra, rb):
        """Toets de hele groep; twee verschillende studies mogen alleen samen wanneer
        een derde cv BEIDE opleidingen letterlijk bevat."""
        alle = [docs[i] for i in leden[ra] + leden[rb]]
        for x in leden[ra]:
            for y in leden[rb]:
                blokkade = harde_blokkade(docs[x], docs[y])
                if not blokkade:
                    continue
                if zelfde_naam_eigen_email(docs[x], docs[y]) and not mail_verliest_van_conflict(docs[x], docs[y]):
                    continue
                if (blokkade.startswith('tegenstrijdige opleiding')
                        and opleidingen_verzoend(docs[x], docs[y], alle)):
                    continue
                # Zelfde studie, maar de lastige CV noemt een andere werkgever: één persoon.
                if blokkade.startswith('andere loopbaan') and opleiding_overeenkomsten(docs[x], docs[y]):
                    continue
                return True
        return False

    def samenvoegen(a, b):
        ra, rb = dsu.find(a), dsu.find(b)
        if ra == rb:
            return True
        if groep_botst(ra, rb):
            return False
        combined = component_names[ra] | component_names[rb]
        alle = leden[ra] + leden[rb]
        dsu.union(a, b)
        component_names[dsu.find(a)] = combined
        leden[dsu.find(a)] = alle
        return True
    for a, b in sorted(candidates):
        auto, review, reason = safe_pair(docs[a], docs[b])
        if auto:
            # Controleer ook alle namen die eerder via een naamloos CV zijn verbonden.
            ra, rb = dsu.find(a), dsu.find(b)
            left, right = component_names[ra], component_names[rb]
            if ra!=rb and any(difflib.SequenceMatcher(None,n,m).ratio()<.70 for n in left for m in right):
                reviews.append((a,b,'mogelijk contactgegeven van meerdere personen; groepsnamen verschillen'))
            elif not samenvoegen(a, b):
                pass                       # botst met een ander cv in de groep: apart houden
        elif review:
            reviews.append((a,b,reason))
    reviews = ai_dubbel_controle(docs, reviews, samenvoegen, out_dir)
    # Bewaar per automatische koppeling de concrete reden in de exportmap.
    # Geen namen of persoonsgegevens in de log: bestands-hashes en besluiten volstaan.
    if out_dir:
        try:
            redenen = []
            for i, j in sorted(candidates):
                if dsu.find(i) != dsu.find(j):
                    continue
                auto, _rev, reden = safe_pair(docs[i], docs[j])
                if auto:
                    redenen.append(dict(cv_sha_1=docs[i]['sha'], cv_sha_2=docs[j]['sha'], reden=reden))
            save_csv(out_dir / 'samenvoeg_redenen.csv', redenen,
                     ['cv_sha_1', 'cv_sha_2', 'reden'])
        except OSError:
            pass
    # Extra samenvoegingen alleen als de gebruiker dit expliciet bevestigt in CSV.
    if merge_file and merge_file.exists():
        pre_groups = collections.defaultdict(list)
        for i in range(len(docs)): pre_groups[dsu.find(i)].append(i)
        lookup = {str(uuid.uuid5(uuid.NAMESPACE_URL, min(docs[i]['sha'] for i in g)))[:12]:g[0]
                  for g in pre_groups.values()}
        try:
            with merge_file.open(encoding='utf-8-sig', newline='') as f:
                for merge in csv.DictReader(f,delimiter=';'):
                    a, b = lookup.get(merge.get('persoon_id_1')), lookup.get(merge.get('persoon_id_2'))
                    if a is not None and b is not None:
                        dsu.union(a,b)
        except (OSError, UnicodeError, csv.Error) as exc:
            print('WAARSCHUWING: handmatige samenvoegingen niet ingelezen:',exc)
    # Geen verdere automatische merges via slechts naamgelijkheid; twijfel blijft apart.
    groups = collections.defaultdict(list)
    for i in range(len(docs)):
        groups[dsu.find(i)].append(docs[i])
    identities = []
    doc_to_person = {}
    # Vaste ID's: eenmaal toegekend blijft een persoon-ID gelijk bij elke volgende scan.
    id_file = (Path(merge_file).parent / 'vaste_ids.json') if merge_file else None
    try:
        id_map = json.loads(id_file.read_text(encoding='utf-8')) if id_file and id_file.exists() else {}
    except (OSError, ValueError):
        id_map = {}
    used_ids = set()
    # 'Op de pc gekomen' telt per INHOUD het vroegste moment: een kopie van hetzelfde cv
    # (ook de kopieën die de scanner zelf in Alle_cvs zet) maakt die versie niet nieuwer.
    first_seen = {}
    for d in docs:
        rec = d['received_ns'] if 'received_ns' in d.keys() else 0
        if d['sha'] and rec and f'/{CV_FOLDER}/' not in str(d['path']).replace('\\', '/'):
            first_seen[d['sha']] = min(first_seen.get(d['sha'], rec), rec)
    def inhoud_jaar(d):
        # Nieuwste cv = het cv met de recentste loopbaan (laatste beginjaar in de tekst). Een oud cv dat vandaag
        # is gedownload of gekopieerd wordt daardoor niet 'nieuwer'. Geen jaartallen: dan telt de bestandsdatum.
        jaren = [bm // 12 for bm, _e, _v, _x, _vo in periodes_uit_cv(d['snippet'] or '')]
        return max(jaren) if jaren else 0
    def newest(d):
        rec = first_seen.get(d['sha']) or 0
        if not rec and not (f'/{CV_FOLDER}/' in str(d['path']).replace('\\', '/')):
            rec = (d['received_ns'] if 'received_ns' in d.keys() else 0) or 0
        return max(d['mtime_ns'] or 0, rec)
    def cv_versie_rang(d):
        """Hoofdversie: meest recente werkPERIODE (laatste functie eerst),
        daarna opleidingsPERIODE, daarna expliciete ICT-inhoud.
        Alleen bij gelijkspel een betrouwbare bestandsdatum en SHA.
        Een later gekopieerde oude CV mag geen nieuwere baan verdringen.
        """
        banen = werk_periodes(d)
        studies = opleiding_periodes(d)
        werk = max(((begin, eind) for _w, begin, eind in banen), default=(0, 0))
        opleiding = max(((begin, eind) for _nv, _richting, _school,
                          _plaats, begin, eind in studies), default=(0, 0))
        inhoud = normalized_name(d['snippet'] or '')
        it_termen = re.findall(
            r'\b(?:python|java|javascript|typescript|sql|azure|aws|docker|'
            r'kubernetes|linux|git|react|nodejs|csharp|dotnet|'
            r'data engineering|cloud|software development|cybersecurity|'
            r'netwerkbeheer|informatica|devops)\b', inhoud)
        it = len(set(it_termen))
        return (werk[0], werk[1], opleiding[0], opleiding[1],
                it, newest(d), d['year'] or '', d['sha'] or '')
    for group in sorted(groups.values(), key=lambda g: min(d['sha'] for d in g)):
        recent = sorted(group, key=cv_versie_rang, reverse=True)
        all_emails = sorted(set(_fix_email(v) for d in group for v in json.loads(d['emails'])))
        all_phones = sorted(set(filter(None, (norm_phone(v) for d in group for v in json.loads(d['phones'])))))
        all_links = sorted(set(v for d in group for v in json.loads(d['links'])))
        # Naam die in de meeste versies staat (opgeschoond); bij gelijkspel de vollere naam.
        names = [tidy_name(clean_person_name(d['name']) or d['name']) for d in group if d['name']]
        uniek = sorted({n for n in names}, key=len)
        if len(uniek) >= 2:
            volledig = naam_verzoenen(uniek[0], uniek[-1], [d['snippet'] for d in group])
            if volledig and normalized_name(volledig) not in {normalized_name(n) for n in names}:
                names.append(volledig); names.append(volledig)     # volledige naam (meerdere voornamen) wint
        counts = collections.Counter(normalized_name(n) for n in names)
        names.sort(key=lambda n: (bool(clean_person_name(n)), counts[normalized_name(n)], len(n.split()), len(n)),
                   reverse=True)
        name = names[0] if names else ''
        name = name_order_by_email(name_with_email(tidy_name(name), sorted({v for d in group for v in json.loads(d['emails'])})),
                                   sorted({v for d in group for v in json.loads(d['emails'])}))
        # Stabiele ID op de vroegst aanwezige bestandsfingerprint (per volledige exportsnapshot).
        known = collections.Counter(id_map[d['sha']] for d in group if d['sha'] in id_map)
        known_ids = [i for i, _n in sorted(known.items(), key=lambda kv: (-kv[1], kv[0])) if i not in used_ids]
        if known and not known_ids:
            # Deze cv's hoorden eerder bij een kandidaat die nu gesplitst is (bv. bureau-cv's die ten onrechte
            # waren samengevoegd): krijgt een nieuw nummer; het oude nummer blijft bij de andere groep.
            GESPLITST.append((sorted(known)[0], [d['path'] for d in group]))
        ident = known_ids[0] if known_ids else str(uuid.uuid5(uuid.NAMESPACE_URL, min(d['sha'] for d in group)))[:12]
        if ident in used_ids:
            ident = str(uuid.uuid5(uuid.NAMESPACE_URL, 'x' + min(d['sha'] for d in group)))[:12]
        used_ids.add(ident)
        for d in group:
            id_map[d['sha']] = ident
        first = recent[0]     # dezelfde hoofdversie als alle andere gekozen velden
        roles = [d['role'] for d in recent if d['role']]
        employers = [d['employer'] for d in recent if d['employer']]
        distinct_shas = len({d['sha'] for d in group})
        def latest(field):
            # Nieuwste CV wint per veld. Ontbreekt mail of telefoon daar, dan van een ouder CV.
            # Heeft de nieuwste CV e-mail én telefoon, dan blijven oude contactgegevens ongebruikt.
            for item in recent:
                values = json.loads(item[field])
                if field == 'phones':
                    values = [x for x in (norm_phone(v) for v in values) if x]   # oude opgeslagen nummers ook controleren
                elif field == 'emails':
                    values = [_fix_email(v) for v in values]
                if values:
                    return values[0]
            return ''
        levs = collections.Counter(d['leverancier'] for d in group if 'leverancier' in d.keys() and d['leverancier'])
        candidate = dict(persoon_id=ident, naam=name, bedrijf_raw=levs.most_common(1)[0][0] if levs else '',
           email=latest('emails'),
           telefoon=latest('phones'), linkedin=latest('links'),
           alle_emails=' | '.join(all_emails), alle_telefoons=' | '.join(all_phones),
           alle_linkedin=' | '.join(all_links), huidige_functie=roles[0] if roles else '',
           laatst_bekende_werkgever=employers[0] if employers else '',
           cv_jaar=first['year'], aantal_cv_versies=distinct_shas,
           aantal_bestandslocaties=len(group), eerste_bron='CV', cv_id=cv_id_of(first['sha']), cv_sha=first['sha'],
           cv_pad=first['path'], cv_archief=first['archive'])
        # E-mail: liefst het adres dat bij de naam past (niet dat van een referentie of leidinggevende).
        if candidate['email'] and email_owner_mismatch(name, candidate['email']):
            fitting = [e for e in all_emails if email_matches_name(name, e) and not GENERIC_EMAIL.match(e)]
            if fitting:
                candidate['email'] = fitting[0]
        # Taal, woonplaats NL/BE en verblijfsduur (wens opdrachtgever). Jouw eigen keuze 'wel cv' gaat voor.
        if not any(decided.get(d['sha'], {}).get('status') == 'cv' for d in group):
            def ai_vraag(soort, _d=first):
                key = f"{ai_model}|{soort}|{_d['sha']}"
                if key not in ai_cache:
                    try:
                        import ai_rechter
                        ans = ai_rechter.filter_vraag(soort, _d['snippet'] or '')
                    except Exception:
                        ans = None
                    if ans is None:
                        return None
                    ai_cache[key] = ans
                return ai_cache[key]
            uitkomst, reden = filter_kandidaat(first, all_phones, all_emails, ai_vraag)
            if uitkomst == 'weg':
                for d in group:
                    EXCLUDED[d['path']] = reden
                continue
            if uitkomst == 'controleren':
                for d in group:
                    db.execute("UPDATE docs SET status='review', name_verified=0, reason=? WHERE path=?",
                               (f'controleren: {reden}', d['path']))
                continue
        identities.append(candidate)
        for d in group: doc_to_person[d['path']] = ident
    # Een e-mail of nummer dat in de cv's van 3+ verschillende personen staat, is van een bureau of bedrijf
    # (bemiddelaar, detacheerder). Wel invullen (via het bureau bereik je de kandidaat), maar een eigen
    # nummer/mail gaat voor, en de bron-kolom zegt dat het een bureau-/bedrijfsgegeven is.
    shared_count = collections.Counter()
    for person in identities:
        for v in set(filter(None, person['alle_emails'].split(' | '))) | set(filter(None, person['alle_telefoons'].split(' | '))):
            shared_count[v] += 1
    for person in identities:
        for field, allf in (('email', 'alle_emails'), ('telefoon', 'alle_telefoons')):
            values = [v for v in person[allf].split(' | ') if v]
            own = [v for v in values if shared_count[v] < 3 and not (field == 'email' and GENERIC_EMAIL.match(v))]
            if person[field] and person[field] not in own:
                if own:
                    person[field] = own[0]
                else:
                    person[field + '_bureau'] = shared_count[person[field]]
    # Zelfde LinkedIn bij 3+ verschillende personen (bv. linkedin.com/in/jrecruit): van het bureau, niet van de
    # kandidaat. Niet als eigen LinkedIn gebruiken; dan zoekt de online stap het echte profiel.
    li_count = collections.Counter()
    for person in identities:
        for v in set(filter(None, (person.get('alle_linkedin') or '').split(' | '))):
            li_count[v] += 1
    for person in identities:
        eigen_li = [v for v in (person.get('alle_linkedin') or '').split(' | ') if v and li_count[v] < 3]
        if person.get('linkedin') and li_count[person['linkedin']] >= 3:
            person['linkedin'] = eigen_li[0] if eigen_li else ''
    company_count = collections.Counter(normalized_name(p['bedrijf_raw'].replace('via:', '')) for p in identities
                                        if p.get('bedrijf_raw'))
    for person in identities:
        raw = person.pop('bedrijf_raw', '')
        person['naam_is_bedrijf'] = bool(raw) and normalized_name(person['naam']) == normalized_name(raw.replace('via:', ''))
        person['bedrijf'], person['bedrijf_soort'] = bedrijf_soort(
            raw, person['naam'], [v for v in person['alle_emails'].split(' | ') if v],
            company_count.get(normalized_name(raw.replace('via:', '')), 1) if raw else 1)
    db.commit()
    if ai_cache_path:
        try:
            ai_cache_path.write_text(json.dumps(ai_cache), encoding='utf-8')
        except OSError:
            pass
    identities.sort(key=lambda r:(normalized_name(r['naam']), r['persoon_id']))
    if id_file:
        try:
            id_file.write_text(json.dumps(id_map, indent=0), encoding='utf-8')
        except OSError as exc:
            print('WAARSCHUWING: vaste ID\'s niet opgeslagen:', exc, flush=True)
    # Kandidaatnummer voor Loxo: SNK-00001, eenmaal toegekend nooit meer veranderd of hergebruikt.
    num_file = (id_file.parent / 'kandidaat_nummers.json') if id_file else None
    try:
        nums = json.loads(num_file.read_text(encoding='utf-8')) if num_file and num_file.exists() else {}
    except (OSError, ValueError):
        nums = {}
    nxt = max([int(v.split('-')[1]) for v in nums.values() if re.fullmatch(r'SNK-\d+', v)] + [0]) + 1
    for person in identities:
        if person['persoon_id'] not in nums:
            nums[person['persoon_id']] = f'SNK-{nxt:05d}'
            nxt += 1
        person['kandidaat_id'] = nums[person['persoon_id']]
    if num_file:
        try:
            num_file.write_text(json.dumps(nums, indent=0), encoding='utf-8')
        except OSError as exc:
            print('WAARSCHUWING: kandidaatnummers niet opgeslagen:', exc, flush=True)
    if GESPLITST:
        # Melden welke eerder samengevoegde kandidaten nu gesplitst zijn (in Loxo even nalopen).
        per_pad = {}
        for person in identities:
            per_pad[person['persoon_id']] = person
        regels = []
        for oud, paden in GESPLITST:
            nieuw = next((per_pad.get(doc_to_person.get(pth, '')) for pth in paden if doc_to_person.get(pth)), None)
            oud_nr = nums.get(oud, oud)
            if nieuw:
                regels.append(f"{oud_nr} -> {nieuw['kandidaat_id']} {nieuw['naam']}")
        if regels:
            print(f'GESPLITST ({len(regels)}): eerder één kandidaat, nu apart (in Loxo nalopen): ' + '; '.join(regels[:20]),
                  flush=True)
            if id_file:
                try:
                    with (id_file.parent / 'gesplitste_kandidaten.txt').open('a', encoding='utf-8') as fh:
                        fh.write(dt.datetime.now().strftime('%Y-%m-%d %H:%M') + '\n' + '\n'.join(regels) + '\n\n')
                except OSError:
                    pass
    distinct_review = []
    for a,b,reason in reviews:
        if docs[a]['path'] not in doc_to_person or docs[b]['path'] not in doc_to_person:
            continue          # een van beide is niet meegenomen of moet nog gecontroleerd worden
        if doc_to_person[docs[a]['path']] != doc_to_person[docs[b]['path']]:
            distinct_review.append(dict(soort='Mogelijk dezelfde persoon',
                persoon_id_1=doc_to_person[docs[a]['path']], persoon_id_2=doc_to_person[docs[b]['path']],
                bestand_1=docs[a]['path'], bestand_2=docs[b]['path'],
                naam_1=docs[a]['name'],naam_2=docs[b]['name'], toelichting=reason))
    return docs, identities, doc_to_person, distinct_review


def formula_safe(val):
    s = str(val if val is not None else '')
    # Excel en Numbers interpreteren velden met =, +, -, @, tabs, CR soms als formules.
    if s and (s[0] in '=+-@' or s[0] in '\t\r\n'):
        return "'" + s
    return s


def save_csv(path, rows, columns):
    with path.open('w', encoding='utf-8-sig', newline='') as f:
        w = csv.DictWriter(f, fieldnames=columns, delimiter=';', extrasaction='ignore')
        w.writeheader()
        for row in rows:
            w.writerow({k:formula_safe(row.get(k,'')) for k in columns})


class XLink(str):
    """Cel-tekst met een klikbare link erachter (bv. 'Zoek in Google')."""
    def __new__(cls, text, url):
        obj = str.__new__(cls, text)
        obj.url = url
        return obj


def xlsx_export(path, sheets):
    """Compacte XLSX-writer met standaardbibliotheek, zonder macro's of formules."""
    main = 'http://schemas.openxmlformats.org/spreadsheetml/2006/main'
    rel = 'http://schemas.openxmlformats.org/officeDocument/2006/relationships'
    pkg = 'http://schemas.openxmlformats.org/package/2006/relationships'
    def colref(i):
        result = ''
        while i:
            i, rem = divmod(i - 1, 26)
            result = chr(65 + rem) + result
        return result
    with zipfile.ZipFile(path, 'w', compression=zipfile.ZIP_DEFLATED) as z:
        types = ['<?xml version="1.0" encoding="UTF-8"?>',
         '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">',
         '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>',
         '<Default Extension="xml" ContentType="application/xml"/>',
         '<Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>',
         '<Override PartName="/xl/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"/>']
        book_sheets = []
        book_rels = []
        for idx, (title, columns, rows) in enumerate(sheets, 1):
            types.append(f'<Override PartName="/xl/worksheets/sheet{idx}.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>')
            book_sheets.append(f'<sheet name="{html.escape(title,quote=True)}" sheetId="{idx}" r:id="rId{idx}"/>')
            book_rels.append(f'<Relationship Id="rId{idx}" Type="{rel}/worksheet" Target="worksheets/sheet{idx}.xml"/>')
            cells = []
            links = []
            for r_index, record in enumerate([dict(zip(columns,columns))] + rows, 1):
                row = []
                for c_index, col in enumerate(columns, 1):
                    raw = record.get(col,'')
                    pos = f'{colref(c_index)}{r_index}'
                    style = ' s="1"' if r_index == 1 else ''
                    if r_index > 1 and isinstance(raw, int) and not isinstance(raw, bool):
                        row.append(f'<c r="{pos}" s="4"><v>{raw}</v></c>')
                    else:
                        val = safe_xml_cell(raw)
                        target = raw.url if isinstance(raw, XLink) else (
                            val if r_index > 1 and re.fullmatch(r'https?://\S{4,1500}', val or '') else '')
                        if target and not target.startswith(('http', 'file:')):
                            target = urllib.parse.quote(target, safe='/')
                        if target:
                            links.append((pos, target))
                            style = ' s="2"'
                        elif r_index > 1:
                            style = ' s="4"'       # bovenaan uitlijnen; meerdere regels netjes onder elkaar
                        row.append(f'<c r="{pos}" t="inlineStr"{style}><is><t xml:space="preserve">{html.escape(val)}</t></is></c>')
                cells.append(f'<row r="{r_index}">{"".join(row)}</row>')
            def col_width(c):
                longest = max([len(c)] + [len(str(r.get(c, '') or '')) for r in rows[:300]])
                if c == 'gezocht_waarom':
                    return 75
                longest = max([len(c)] + [len(line) for r in rows[:300] for line in str(r.get(c, '') or '').split('\n')])
                return min(70 if c in ('advies', 'verschillend', 'toelichting') else 48, max(12, longest + 2))
            widths = ''.join(f'<col min="{j}" max="{j}" width="{col_width(c)}" customWidth="1"/>'
                             for j,c in enumerate(columns,1))
            hyper = ('<hyperlinks>' + ''.join(f'<hyperlink ref="{pos}" r:id="h{n}"/>'
                                              for n, (pos, _u) in enumerate(links, 1)) + '</hyperlinks>') if links else ''
            sheet = f'''<?xml version="1.0" encoding="UTF-8"?>
            <worksheet xmlns="{main}"><sheetViews><sheetView workbookViewId="0"><pane ySplit="1" topLeftCell="A2" activePane="bottomLeft" state="frozen"/></sheetView></sheetViews>
            <cols>{widths}</cols><sheetData>{''.join(cells)}</sheetData><autoFilter ref="A1:{colref(len(columns))}{max(1,len(rows)+1)}"/>{hyper}</worksheet>'''
            sheet = sheet.replace(f'<worksheet xmlns="{main}">', f'<worksheet xmlns="{main}" xmlns:r="{rel}">', 1)
            z.writestr(f'xl/worksheets/sheet{idx}.xml', sheet)
            if links:
                z.writestr(f'xl/worksheets/_rels/sheet{idx}.xml.rels',
                           f'<?xml version="1.0" encoding="UTF-8"?><Relationships xmlns="{pkg}">' + ''.join(
                               f'<Relationship Id="h{n}" Type="{rel}/hyperlink" Target="{html.escape(u, quote=True)}" TargetMode="External"/>'
                               for n, (_pos, u) in enumerate(links, 1)) + '</Relationships>')
        types.append('</Types>')
        z.writestr('[Content_Types].xml', ''.join(types))
        z.writestr('_rels/.rels', f'<Relationships xmlns="{pkg}"><Relationship Id="rId1" Type="{rel}/officeDocument" Target="xl/workbook.xml"/></Relationships>')
        z.writestr('xl/_rels/workbook.xml.rels', f'<Relationships xmlns="{pkg}">{"".join(book_rels)}<Relationship Id="styles" Type="{rel}/styles" Target="styles.xml"/></Relationships>')
        z.writestr('xl/workbook.xml', f'<?xml version="1.0" encoding="UTF-8"?><workbook xmlns="{main}" xmlns:r="{rel}"><sheets>{"".join(book_sheets)}</sheets></workbook>')
        z.writestr('xl/styles.xml', f'''<?xml version="1.0" encoding="UTF-8"?><styleSheet xmlns="{main}"><fonts count="3"><font><sz val="11"/><name val="Aptos"/></font><font><b/><sz val="11"/><color rgb="FFFFFFFF"/><name val="Aptos"/></font><font><u/><sz val="11"/><color rgb="FF0563C1"/><name val="Aptos"/></font></fonts><fills count="4"><fill><patternFill patternType="none"/></fill><fill><patternFill patternType="gray125"/></fill><fill><patternFill patternType="solid"><fgColor rgb="FF023A51"/><bgColor indexed="64"/></patternFill></fill><fill><patternFill patternType="solid"><fgColor rgb="FFFFF2B3"/><bgColor indexed="64"/></patternFill></fill></fills><borders count="1"><border><left/><right/><top/><bottom/><diagonal/></border></borders><cellStyleXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0"/></cellStyleXfs><cellXfs count="5"><xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0" applyAlignment="1"><alignment vertical="top"/></xf><xf numFmtId="0" fontId="1" fillId="2" borderId="0" xfId="0" applyFont="1" applyFill="1"/><xf numFmtId="0" fontId="2" fillId="0" borderId="0" xfId="0" applyFont="1" applyAlignment="1"><alignment vertical="top"/></xf><xf numFmtId="0" fontId="0" fillId="3" borderId="0" xfId="0" applyFill="1"/><xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0" applyAlignment="1"><alignment vertical="top" wrapText="1"/></xf></cellXfs></styleSheet>''')


def apply_corrections(people, out):
    corrections = out / 'contact_correcties.csv'
    if not corrections.exists():
        save_csv(corrections, [], ['persoon_id','naam','email','telefoon','linkedin','toelichting'])
    by_id = {r['persoon_id']:r for r in people}
    try:
        with corrections.open(encoding='utf-8-sig', newline='') as f:
            for row in csv.DictReader(f, delimiter=';'):
                ident = row.get('persoon_id','')
                if ident not in by_id:
                    continue
                r = by_id[ident]
                for field in ('email','telefoon','linkedin'):
                    v = (row.get(field) or '').strip()
                    if v:
                        r[field] = v
                        r['eerste_bron'] = 'CV + handmatige correctie'
    except Exception as exc:
        print('WAARSCHUWING: correcties konden niet gelezen worden:', exc)


def refresh_index(args, db, out):
    """Houd de database actueel met wat er NU op de pc staat, overal.

    1. Bestanden die niet meer bestaan (verplaatst/verwijderd) gaan eruit,
       behalve als hun hele schijf ontbreekt (bv. losgekoppelde USB-schijf).
    2. Bestanden met een oude leesfout die nog bestaan, worden opnieuw gelezen.
    """
    gone, retry = [], []
    for row in db.execute('SELECT path, status FROM docs').fetchall():
        p = container_path(row['path'])
        try:
            exists = p.exists()
        except OSError:
            continue
        if p.name.startswith(('~$', '._')):
            gone.append(row['path'])   # tijdelijk Word-/Mac-bestand, geen document
        elif not exists:
            if Path(p.anchor or '/').exists():
                gone.append(row['path'])
        elif row['status'] == 'error' and row['path'] not in LAST_SEEN and p not in retry:
            retry.append(p)
    for start in range(0, len(gone), 500):
        chunk = gone[start:start+500]
        db.execute(f"DELETE FROM docs WHERE path IN ({','.join('?' for _ in chunk)})", chunk)
    db.commit()
    if gone:
        print(f'Bijgewerkt: {len(gone)} bestanden staan niet meer op die plek en zijn uit het overzicht gehaald.', flush=True)
    if retry:
        ingest(args, db, out, roots=retry,
               label=f'Bijwerken: {len(retry)} bestanden met een eerdere leesfout worden opnieuw gelezen…')


def remove_vanished(db):
    """Haal bestanden uit de database die niet meer op hun oude plek staan
    (verplaatst, hernoemd of verwijderd). Zo blijven er geen oude fouten of
    dubbele locaties in de Excel hangen. Een schijf die niet aangesloten is
    (USB/netwerk) wordt overgeslagen, zodat die gegevens bewaard blijven."""
    gone = []
    for row in db.execute('SELECT path FROM docs').fetchall():
        p = container_path(row['path'])
        try:
            if p.exists():
                continue
            if not Path(p.anchor or '/').exists():
                continue  # schijf niet aangesloten: niets weggooien
        except OSError:
            continue
        gone.append(row['path'])
    for path in gone:
        db.execute('DELETE FROM docs WHERE path=?', (path,))
    if gone:
        db.commit()
        print(f'OPGERUIMD: {len(gone)} bestanden staan niet meer op hun oude plek '
              '(verplaatst/verwijderd) en zijn uit het overzicht gehaald.', flush=True)
    return len(gone)


def google_xray_link(name, need_mail, need_phone):
    """Gratis: klikbare Google X-ray-zoekopdracht. Google toont in de zoektekst vaak
    het stuk van de LinkedIn-Info met nummer/mail. Jij klikt zelf; kost niets."""
    if not name or not (need_mail or need_phone):
        return ''
    terms = []
    if need_phone:
        terms += ['"+31 6"', '"06"', '"+316"']
    if need_mail:
        terms += ['"mail"']
    q = f'site:linkedin.com/in "{name}" (' + ' OR '.join(terms) + ')'
    label = 'Zoek ' + ' en '.join(x for x, ok in (('telefoon', need_phone), ('mail', need_mail)) if ok) + ' in Google'
    return XLink(label, 'https://www.google.com/search?q=' + urllib.parse.quote(q))


def pretty_waarom(text):
    """Leesbaar in Excel: elk bewijs op een eigen regel."""
    text = re.sub(r'staat in de Info-tekst van het LinkedIn-profiel dat zeker klopt \(https?://\S+?\)',
                  'staat in de Info-tekst van het zekere LinkedIn-profiel', text or '')
    lines = []
    for part in text.split(' | '):
        part = part.strip()
        if not part:
            continue
        note = ''
        if ' – ' in part:
            part, note = part.split(' – ', 1)
        if ': ✓' in part:
            head, tick = part.split(': ✓', 1)
            lines.append(head + ':')
            lines.append('   ✓' + tick)
        elif part.startswith('✓'):
            lines.append('   ' + part)
        else:
            lines.append(part)
        if note:
            lines.append('   (' + note.strip() + ')')
    return '\n'.join(lines)


def only_ticks(text):
    """Toon alleen wat overeenkomt (✓); kruisjes (✗) weglaten, opmerkingen na '–' bewaren."""
    out, carry = [], ''
    for line in (text or '').split(' | '):
        note = ''
        if ' – ' in line:
            line, note = line.split(' – ', 1)
            note = ' – ' + note
        head, sep, rest = line.partition(': ')
        labelled = bool(sep) and not head.strip().startswith(('✓', '✗'))
        body = rest if labelled else line
        prefix = head + ': ' if labelled else ''
        if body.strip().startswith('✗'):
            carry = carry or prefix
            if note:
                if out:
                    out[-1] += note
                else:
                    carry += note.strip(' –') + ' '
            continue
        out.append(carry + prefix + body + note if not (carry and prefix) else prefix + body + note)
        carry = ''
    if carry:
        out.append(carry.strip() + ' geen overeenkomsten')
    return ' | '.join(x for x in out if x.strip())


def personen_overzicht(people):
    """Eerste tabblad: per gegeven 'uit cv' naast 'gezocht' (online), plus zekerheid en waarom."""
    cols = ['naam', 'kandidaat_id', 'cv_id', 'open_cv', 'email', 'email_bron', 'telefoon', 'telefoon_bron',
            'linkedin', 'linkedin_bron', 'gezocht_zekerheid', 'gezocht_waarom', 'bedrijf', 'bedrijf_soort',
            ]
    rows = []
    for p in people:
        zeker = {}
        for part in (p.get('online_zekerheid') or '').split('|'):
            if ':' in part:
                k, v = part.split(':', 1)
                zeker[k.strip().lower()] = v.strip()
        def found(field, label):
            if p.get('online_' + field) or p.get('te_controleren_' + field):
                return p.get('online_' + field) or p.get('te_controleren_' + field)
            # Alleen als er ECHT gezocht is en het niet in het cv staat:
            if p.get('niet_gevonden_' + field) and not p.get(field):
                return 'gezocht, niet gevonden'
            return ''
        labels = []
        for field, label in (('email', 'e-mail'), ('telefoon', 'telefoon'), ('linkedin', 'linkedin')):
            if found(field, label) and found(field, label) != 'gezocht, niet gevonden':
                state = '✓ zeker' if p.get('online_' + field) else 'controleren'
                labels.append(f"{label.capitalize() if label != 'linkedin' else 'LinkedIn'}: {state}")
        if 'LinkedIn: niet gevonden' in (p.get('online_zekerheid') or ''):
            labels.append('LinkedIn: niet gevonden')
        elif 'LinkedIn: alleen naamgenoten' in (p.get('online_zekerheid') or ''):
            labels.append('LinkedIn: alleen naamgenoten (niets klopt)')
        role = p.get('huidige_functie', '') or ''
        employer = p.get('laatst_bekende_werkgever', '') or ''
        if '·' in role:
            parts = [x.strip() for x in role.split('·') if x.strip()]
            role = parts[0]
            employer = employer if employer and '·' not in employer else ' · '.join(parts[1:2])
        elif '·' in employer:
            employer = employer.split('·')[0].strip()
        cv_file = p.get('cv_bestand', '')
        def contact(field):
            """Eén waarde + waar die vandaan komt. Leeg = nergens gevonden."""
            if p.get(field) and p.get(field + '_bureau'):
                n = p[field + '_bureau']
                return p[field], (f'cv – bureau/bedrijf (staat bij {n} kandidaten)' if n >= 3
                                  else 'cv – algemeen adres (info@ e.d.)')
            if p.get(field) and field == 'email' and email_owner_mismatch(p.get('naam', ''), p[field]):
                return p[field], 'cv – let op: e-mail lijkt van iemand anders'
            if p.get(field):
                return p[field], 'cv'
            if p.get('online_' + field):
                return p['online_' + field], 'online (zeker)'
            if p.get('te_controleren_' + field):
                return p['te_controleren_' + field], 'online (nog controleren)'
            if field == 'linkedin' and p.get('naamgenoten_linkedin'):
                return '', 'niet ingevuld: wel profielen met deze naam, maar niets uit het cv klopt'
            if p.get('niet_gevonden_' + field):
                return '', 'niet gevonden (cv en online)'
            return '', 'niet in cv (online niet gezocht)'
        email, email_bron = contact('email')
        tel, tel_bron = contact('telefoon')
        li, li_bron = contact('linkedin')
        rows.append(dict(naam=p['naam'], kandidaat_id=p.get('kandidaat_id', ''), cv_id=p.get('cv_id', ''),
                         open_cv=(XLink('Open cv', cv_rel_link(cv_file)) if cv_file else
                                  XLink('Open cv', mac_archive_link(p.get('cv_archief', '')))
                                  if sys.platform == 'darwin' and mac_archive_link(p.get('cv_archief', '')) else
                                  XLink('Open cv', file_link(p.get('cv_pad', ''), p.get('cv_archief', '')))
                                  if p.get('cv_pad') else ''),
                         email=email, email_bron=email_bron, telefoon=tel, telefoon_bron=tel_bron,
                         linkedin=li, linkedin_bron=li_bron,
                         gezocht_zekerheid='\n'.join(labels), gezocht_waarom=pretty_waarom(only_ticks(p.get('online_waarom', ''))),
                         huidige_functie=role, laatst_bekende_werkgever=employer,
                         bedrijf=p.get('bedrijf', ''), bedrijf_soort=p.get('bedrijf_soort', ''),
                         zelf_zoeken_google=google_xray_link(p['naam'], not p['email'] and not found('email', ''),
                                                             not p['telefoon'] and not found('telefoon', '')),
                         persoon_id=p['persoon_id']))
    return rows, cols


def build_checks(reviews, people, doubt, legacy_count, unread_count, scanned_count=0):
    """Tabblad 'Controle nodig': per personen-paar één regel met wat hetzelfde en wat anders is,
    per twijfelbestand één regel (kopieën samengevoegd), en korte samenvattingen."""
    by_id = {p['persoon_id']: p for p in people}

    def vals(person, key):
        return {v.strip().lower() for v in (person.get(key) or '').split('|') if v.strip()}

    pairs = {}
    for r in reviews:
        key = tuple(sorted((r.get('persoon_id_1', ''), r.get('persoon_id_2', ''))))
        info = pairs.setdefault(key, dict(files=set(), reason=r.get('toelichting', ''), b1=r['bestand_1'], b2=r['bestand_2']))
        info['files'] |= {r['bestand_1'], r['bestand_2']}
    rows = []
    for (id1, id2), info in pairs.items():
        p1, p2 = by_id.get(id1, {}), by_id.get(id2, {})
        same, diff = [], []
        if normalized_name(p1.get('naam', '')) == normalized_name(p2.get('naam', '')):
            same.append('naam')
        else:
            diff.append(f"naam: {p1.get('naam','')} ≠ {p2.get('naam','')}")
        strong_same = strong_diff = 0
        for key, label in (('alle_emails', 'e-mail'), ('alle_telefoons', 'telefoon'), ('alle_linkedin', 'LinkedIn')):
            a, b = vals(p1, key), vals(p2, key)
            if a and b:
                if a & b:
                    same.append(label); strong_same += 1
                else:
                    diff.append(f"{label}: {' / '.join(sorted(a))[:40]} ≠ {' / '.join(sorted(b))[:40]}"); strong_diff += 1
        for key, label in (('huidige_functie', 'functie'), ('laatst_bekende_werkgever', 'werkgever')):
            a, b = (p1.get(key) or '').strip(), (p2.get(key) or '').strip()
            if a and b:
                (same.append(label) if normalized_name(a) == normalized_name(b) else diff.append(f'{label}: {a[:35]} ≠ {b[:35]}'))
        if strong_same:
            advice, order = 'Waarschijnlijk DEZELFDE persoon → samenvoegen (zet beide persoon_id\'s in samenvoegen_correcties.csv)', 0
        elif strong_diff >= 2:
            advice, order = 'Waarschijnlijk 2 VERSCHILLENDE personen (andere contactgegevens) → niets doen', 3
        else:
            advice, order = 'Onduidelijk → open beide cv\'s en vergelijk', 1
        rows.append((order, dict(soort='Mogelijk dezelfde persoon', advies=advice,
            naam_1=p1.get('naam', ''), naam_2=p2.get('naam', ''), zelfde=', '.join(same) or '-',
            verschillend=' | '.join(diff) or '-', persoon_id_1=id1, persoon_id_2=id2,
            bestand_1=info['b1'], bestand_2=info['b2'], aantal_bestanden=len(info['files']),
            toelichting=info['reason'])))
    for copies in doubt.values():
        d = copies[0]
        rows.append((2, dict(soort='Twijfel: is dit een cv?',
            advies='Open het bestand. Wel een cv? Hernoem het met "CV" in de naam en scan opnieuw.',
            naam_1=d['name'] or '', naam_2='', zelfde='', verschillend='', persoon_id_1='', persoon_id_2='',
            bestand_1=d['path'], bestand_2='', aantal_bestanden=len(copies),
            toelichting=(d['note'] or d['reason'] or ''))))
    if legacy_count:
        rows.append((4, dict(soort='Oude beoordeling', advies='Scan opnieuw (hele pc) om deze bestanden opnieuw te beoordelen.',
            naam_1='', naam_2='', zelfde='', verschillend='', persoon_id_1='', persoon_id_2='', bestand_1='',
            bestand_2='', aantal_bestanden=legacy_count, toelichting='Beoordeeld met een oudere versie van de herkenning.')))
    if scanned_count:
        rows.append((2, dict(soort='Gescande pdf\'s (niet gelezen)',
            advies='Kunnen cv\'s zijn. Vink OCR aan (ingebouwd, niets installeren) en scan opnieuw, of open ze zelf via tabblad Leesfouten.',
            naam_1='', naam_2='', zelfde='', verschillend='', persoon_id_1='', persoon_id_2='', bestand_1='',
            bestand_2='', aantal_bestanden=scanned_count, toelichting='Pdf bevat alleen een afbeelding, geen tekst.')))
    if unread_count:
        rows.append((5, dict(soort='Niet gelezen', advies='Zie tabblad Leesfouten.', naam_1='', naam_2='',
            zelfde='', verschillend='', persoon_id_1='', persoon_id_2='', bestand_1='', bestand_2='',
            aantal_bestanden=unread_count, toelichting='Leesfouten of bestanden die alleen online staan.')))
    rows.sort(key=lambda r: (r[0], r[1]['naam_1'].lower()))
    return [r for _, r in rows]


def twijfel_overzicht(db):
    """Anoniem overzicht van de twijfelgevallen: per soort hoeveel, zonder namen of paden.
    Zo kun je zien waar de twijfel vandaan komt zonder persoonsgegevens te delen."""
    rows = db.execute("SELECT ext, reason, note, sha FROM docs WHERE status='review' AND classifier_version=?",
                      (CLASSIFIER_VERSION,)).fetchall()
    if not rows:
        return
    uniq, seen_sha = [], set()
    for r in rows:
        key = r['sha'] or r['ext']
        if key in seen_sha:
            continue
        seen_sha.add(key)
        uniq.append(r)
    rows = uniq
    groups = collections.Counter()
    for r in rows:
        reason = (r['reason'] or '').lower()
        if 'geen leesbare tekst' in reason:
            key = 'geen leesbare tekst (scan/afbeelding)' + (' - OCR mislukt' if 'ocr mislukt' in (r['note'] or '').lower() else '')
        else:
            heads = re.search(r'kopjes: ([^;]*)', reason)
            heads = heads.group(1).strip() if heads else '?'
            nheads = 0 if heads in ('geen', '?') else len(heads.split(','))
            def flag(name):
                m = re.search(name + r': (ja|nee)', reason)
                return m.group(1) if m else '?'
            periods = re.search(r'werkperiodes: (\d+)', reason)
            periods = int(periods.group(1)) if periods else 0
            key = (f"kopjes {min(nheads, 3)}{'+' if nheads >= 3 else ''} | naam {flag('naam')} | contact {flag('contact')} | "
                   f"cv-kop {flag('cv-kop')} | werkperiodes {'2+' if periods >= 2 else periods}"
                   + (' | andere documentsoort?' if 'andere documentsoort' in reason else ''))
        groups[(key, (r['ext'] or '').lower())] += 1
    print(f'TWIJFEL-OVERZICHT ({len(rows)} documenten, zonder namen of paden):', flush=True)
    for (key, ext), n in groups.most_common(20):
        print(f'  {n:>5}  {ext:<6} {key}', flush=True)


def naamloze_cv_rijen(docs):
    """Alleen sterke naamloze CV-kandidaten, na de normale scan en AI.

    Een 'review' uit een willekeurige CV-map is niet genoeg: een rapport,
    functie-eis of handleiding met CV-kopjes hoort hier niet automatisch thuis.
    Bevestigde kandidaten komen uitsluitend op het tabblad 'Unieke personen'.
    """
    result = []
    for source in docs:
        d = dict(source)
        status = d.get('status', '')
        reason = d.get('reason') or ''
        if status not in ('cv', 'review') or (d.get('classifier_version') or 0) != CLASSIFIER_VERSION:
            continue
        if d.get('path') in EXCLUDED or reason.startswith('niet meegenomen:'):
            continue
        name = (d.get('name') or '').strip()
        verified = bool(d.get('name_verified'))
        if status == 'cv':
            if verified and name:
                continue       # Hoort al bij een echte kandidaat
            state = 'CV, naam niet bevestigd' if name else 'CV, naam ontbreekt'
        else:
            if name:
                continue       # Onzekere documentsoort, niet per se een naamloos CV
            match = re.search(r'kopjes:\s*([^;]+)', reason, re.I)
            heads = {h.strip().lower() for h in match.group(1).split(',')} if match else set()
            periods = re.search(r'werkperiodes:\s*(\d+)', reason, re.I)
            nperiods = int(periods.group(1)) if periods else 0
            # Minimaal drie sterke kopjes inclusief Opleiding en Werkervaring,
            # én 2 concrete perioden. Zo vallen de test-handleidingen,
            # offertes en lege CV-sjablonen niet in deze verzameling.
            if not (len(heads) >= 3 and {'opleiding', 'werkervaring'} <= heads
                    and nperiods >= 2 and 'andere documentsoort' not in reason.lower()
                    and 'sjabloon' not in reason.lower() and 'vacature' not in reason.lower()
                    and 'AI: geen cv' not in reason):
                continue
            state = 'Mogelijk CV, naam ontbreekt'
        result.append(dict(
            cv_id=cv_id_of(d.get('sha') or ''),
            naam_onbevestigd=name if not verified else '',
            beoordeling=state,
            cv_score=d.get('score') or 0,
            oorspronkelijk_bestand=d.get('path') or '',
            archiefkopie=d.get('archive') or '',
            sha256=d.get('sha') or '',
            jaar=d.get('year') or '',
            toelichting=(d.get('note') or reason or 'Naam na controle nog niet betrouwbaar vastgesteld'),
        ))
    result.sort(key=lambda r: (r['beoordeling'], (r['oorspronkelijk_bestand'] or '').lower()))
    return result


def dubbele_kandidaten_controle(people, docs, docs_to_id, out):
    """Onderzoek gelijke namen met verschillende kandidaatnummers.

    Bewaart per paar WAAROM het apart gebleven is. Verandert geen kandidaat-IDs,
    ontdubbelingsbesluiten, bronbestanden of persoonlijke informatie online.
    """
    cols = ['naam', 'kandidaat_id_1', 'kandidaat_id_2',
            'hoofd_cv_1', 'hoofd_cv_2', 'gedeeld_persoonlijk_email',
            'oorzaak', 'advies']
    by_name = collections.defaultdict(list)
    by_person = collections.defaultdict(list)
    for d in docs:
        pid = docs_to_id.get(d['path'])
        if pid:
            by_person[pid].append(d)
    for p in people:
        normalized = normalized_name(p.get('naam') or '')
        if normalized:
            by_name[normalized].append(p)
    rows = []
    for members in by_name.values():
        if len(members) < 2:
            continue
        members = sorted(members, key=lambda p: p.get('kandidaat_id') or '')
        for i, one in enumerate(members[:20]):
            for two in members[i + 1:20]:
                left = by_person.get(one['persoon_id'], [])
                right = by_person.get(two['persoon_id'], [])
                own_mail, auto_match, conflicts, doubts = False, False, set(), set()
                for d1 in left[:12]:
                    for d2 in right[:12]:
                        if zelfde_naam_eigen_email(d1, d2):
                            own_mail = True
                        auto, review, reason = safe_pair(d1, d2)
                        auto_match |= bool(auto)
                        if review and reason:
                            doubts.add(reason)
                        conflict = harde_blokkade(d1, d2)
                        if conflict:
                            conflicts.add(conflict)
                if own_mail:
                    cause = 'Zelfde volledige naam én persoonlijke e-mail, maar niet gekoppeld'
                    advice = 'FOUT: controleer groepsblokkade en vaste IDs'
                elif auto_match:
                    cause = 'Cv-paar had automatisch moeten koppelen; groep bleef gescheiden'
                    advice = 'Groepsconflict: ' + ', '.join(sorted(conflicts)[:2]) if conflicts else 'Controleer groepsblokkade'
                elif conflicts:
                    cause = 'Tegenstrijdige informatie: ' + ', '.join(sorted(conflicts)[:2])
                    advice = 'Niet automatisch samenvoegen; handmatige controle'
                elif doubts:
                    cause = 'Onzeker: ' + '; '.join(sorted(doubts)[:2])
                    advice = 'Handmatige identiteitscontrole'
                else:
                    cause = 'Alleen dezelfde naam, zonder voldoende gedeeld identiteitsbewijs'
                    advice = 'Controleer e-mail, telefoon en echte loopbaan'
                rows.append(dict(
                    naam=one['naam'],
                    kandidaat_id_1=one.get('kandidaat_id') or '',
                    kandidaat_id_2=two.get('kandidaat_id') or '',
                    hoofd_cv_1=one.get('cv_pad') or '',
                    hoofd_cv_2=two.get('cv_pad') or '',
                    gedeeld_persoonlijk_email='ja' if own_mail else 'nee',
                    oorzaak=cause, advies=advice))
    save_csv(out / 'dubbele_kandidaten_controle.csv', rows, cols)
    if rows:
        n = sum(len(members) > 1 for members in by_name.values())
        print(f'DUBBELE KANDIDATEN: {n} gelijke namen met meerdere nummers; '
              f'{len(rows)} paren in dubbele_kandidaten_controle.csv', flush=True)
    else:
        print('DUBBELE KANDIDATEN: geen gelijke volledige namen met aparte nummers.', flush=True)
    return rows


def build_exports(db, out):
    # Vorige export nooit als nieuw rapporteren wanneer opbouwen mislukt.
    # De XLSX zelf mag voor herstel blijven staan; alleen de geldigheidsstempel vervalt.
    (out / 'excel_export_geldig.json').unlink(missing_ok=True)
    remove_vanished(db)
    merges = out/'samenvoegen_correcties.csv'
    if not merges.exists():
        save_csv(merges, [], ['persoon_id_1','persoon_id_2','toelichting'])
    docs, people, docs_to_id, reviews = deduplicate(db, merges)
    dubbele_kandidaten_controle(people, docs, docs_to_id, out)
    apply_corrections(people,out)
    cv_files = collect_cvs(out, people, docs, docs_to_id, db=db)
    for p in people:
        p['cv_bestand'] = cv_files.get(p.get('cv_sha', ''), '')
    all_docs = [d for d in db.execute('SELECT * FROM docs ORDER BY path').fetchall() if in_scope(d['path'], d['sha'])]
    if SCOPE:
        print('ALLEEN DEZE MAP: Excel toont alleen wat in ' + ', '.join(SCOPE) + ' staat. '
              '‘Excel bijwerken’ of ‘Scan hele pc’ toont weer alles.', flush=True)
    files = []
    checks = []
    error_details = []
    legacy_count = unread_count = niet_leesbaar = 0
    doubt = {}
    scanned = set()
    for d in all_docs:
        pid = docs_to_id.get(d['path'],'')
        legacy = (d['classifier_version'] or 0) < CLASSIFIER_VERSION
        label = 'oude_beoordeling' if legacy and d['status'] in ('cv','review') else d['status']
        if d['path'] in EXCLUDED or (d['reason'] or '').startswith('niet meegenomen:'):
            label = 'niet meegenomen'
        if d['status'] == 'error':
            label = 'niet leesbaar'          # kapot/wachtwoord/niet te openen: weg, geen controlepunt
        files.append(dict(persoon_id=pid, cv_id=cv_id_of(d['sha']), naam=d['name'] if not legacy else '',status=label,cv_score=d['score'] if not legacy else 0,
             oorspronkelijk_bestand=d['path'],archiefkopie=d['archive'],sha256=d['sha'],
             jaar=d['year'],toelichting=(EXCLUDED[d['path']] if d['path'] in EXCLUDED else
                 (d['reason'] or '').replace('niet meegenomen: ', '') if (d['reason'] or '').startswith('niet meegenomen:')
                 else (d['note'] or d['reason'])),fout=d['error']))
        if (legacy and d['status'] in ('cv','review')):
            legacy_count += 1
        if d['status'] in ('review','error','deferred') and not (legacy and d['status']=='review'):
            if d['status'] == 'error':
                niet_leesbaar += 1
                continue                 # niet als twijfel- of controlepunt; wordt bij een volgende scan wel opnieuw geprobeerd
            if d['status'] in ('error','deferred'):
                error_details.append(dict(categorie=error_category(d['error']) if d['status']=='error'
                    else 'Niet lokaal beschikbaar / overschreden limiet',
                    bestand=d['path'],extensie=d['ext'],status=d['status'],
                    omschrijving=d['error'] or d['note']))
            if d['status'] == 'review':
                doubt.setdefault(d['sha'] or d['path'], []).append(d)
            else:
                unread_count += 1
        if d['status'] == 'other' and d['ext'] == '.pdf' and 'geen leesbare tekst' in (d['reason'] or ''):
            # Gescande pdf: zonder OCR geen tekst, dus niet te beoordelen. Niet stil wegzetten.
            scanned.add(d['sha'] or d['path'])
            error_details.append(dict(categorie='Gescande pdf (geen tekst – OCR nodig)', bestand=d['path'],
                extensie=d['ext'], status='gescand',
                omschrijving=('OCR is geprobeerd maar mislukte; bij de volgende scan probeert de scanner het opnieuw.'
                              if 'OCR mislukt' in (d['note'] or '') else
                              'OCR heeft het gelezen maar vond geen tekst; open het bestand zelf als het een cv kan zijn.'
                              if 'geen tekst' in (d['note'] or '') else
                              'Nog niet met OCR gelezen (OCR stond uit); scan opnieuw met OCR aan.')))
    # Lege namen alleen onderaan 'Bestanden en status'; nooit tussen kandidaten.
    # Ze blijven zichtbaar en klikbaar, ook wanneer AI niets kon bevestigen.
    files.sort(key=lambda row: (not bool((row.get('naam') or '').strip()),
                                normalized_name(row.get('naam') or ''),
                                (row.get('oorspronkelijk_bestand') or '').lower()))
    pcols = ['persoon_id','cv_id','naam','email','telefoon','linkedin',
             'online_linkedin','online_email','online_telefoon','online_zekerheid','online_waarom',
             'te_controleren_linkedin','te_controleren_email','te_controleren_telefoon',
             'huidige_functie','laatst_bekende_werkgever','alle_emails','alle_telefoons','alle_linkedin',
             'cv_jaar','aantal_cv_versies','aantal_bestandslocaties','eerste_bron','online_bron']
    merge_online_suggestions(people, out)
    fcols = ['persoon_id','cv_id','naam','status','cv_score','oorspronkelijk_bestand',
             'archiefkopie','sha256','jaar','toelichting','fout']
    checks = build_checks(reviews, people, doubt, legacy_count, unread_count, len(scanned))
    ccols = ['soort','advies','naam_1','naam_2','zelfde','verschillend','persoon_id_1','persoon_id_2',
             'bestand_1','bestand_2','aantal_bestanden','toelichting']
    view, vcols = personen_overzicht(people)
    save_csv(out/'unieke_personen.csv',view,vcols)
    weg_personen = len({d['sha'] or d['path'] for d in all_docs if d['path'] in EXCLUDED
                        or (d['reason'] or '').startswith('niet meegenomen:')})
    if weg_personen:
        print(f'NIET MEEGENOMEN: {weg_personen} cv(\'s) (taal, woonplaats of verblijfsduur); '
              'zie ‘Bestanden en status’.', flush=True)
    save_csv(out/'cv_bestanden.csv',files,fcols)
    # Naamloze CV's apart tonen, nooit als kandidaten of als dubbelpaar.
    # De AI-stap is al afgerond voordat build_exports wordt aangeroepen.
    naamloze = naamloze_cv_rijen(all_docs)
    kopieer_naamloze_cvs(out, naamloze)
    naamloos_cols = ['cv_id','naam_onbevestigd','beoordeling','cv_score',
                     'oorspronkelijk_bestand','kopie_in_naamloze_map','archiefkopie','sha256','jaar','toelichting']
    save_csv(out/'naamloze_cvs.csv',naamloze,naamloos_cols)
    save_csv(out/'handmatige_controle.csv',checks,ccols)
    error_cols = ['categorie','bestand','extensie','status','omschrijving']
    categories = collections.Counter(item['categorie'] for item in error_details)
    summary_rows = [dict(categorie=c, aantal=n) for c,n in categories.most_common()]
    # Geen apart foutenbestand meer: fouten staan in het logboek (Activiteit) en in scan_log.txt.
    for old_file in ('fouten_overzicht.csv', 'fouten_per_categorie.csv'):
        try:
            (out / old_file).unlink()
        except OSError:
            pass
    if niet_leesbaar:
        print(f'NIET LEESBAAR: {niet_leesbaar} bestand(en) (beschadigd, wachtwoord of niet te openen) – overgeslagen.', flush=True)
    if error_details:
        print(f'LEESFOUTEN ({len(error_details)}):', flush=True)
        for cat, n in categories.most_common():
            print(f'  {cat}: {n}', flush=True)
            for item in [e for e in error_details if e['categorie'] == cat]:
                print(f"    - {item['bestand']}  ({item['omschrijving'][:90]})", flush=True)
    online_rows = []
    try:
        with (out/'online_suggesties.csv').open(encoding='utf-8-sig', newline='') as f:
            online_rows = [{k: (v or '').lstrip("'") for k, v in r.items()} for r in csv.DictReader(f, delimiter=';')]
    except (OSError, csv.Error, UnicodeError):
        pass
    # Never leave half-written XLSX files if the program is killed during export.
    final = out/'CV_Crawler_Resultaten.xlsx'
    temporary = out/'CV_Crawler_Resultaten.bezig.xlsx'
    try:
        kid = {p['persoon_id']: p.get('kandidaat_id', '') for p in people}
        def as_kid(cols, rows):
            ren = {'persoon_id': 'kandidaat_id', 'persoon_id_1': 'kandidaat_id_1', 'persoon_id_2': 'kandidaat_id_2'}
            new_rows = []
            for r in rows:
                r2 = {ren.get(k, k): (kid.get(v, v) if k in ren else v) for k, v in r.items()}
                if 'sha256' in r2:
                    src = r2.get('oorspronkelijk_bestand', '')
                    r2['open_cv'] = (XLink('Open', cv_rel_link(cv_files[r2['sha256']]))
                                     if in_cv_folder(src, out) and r2.get('sha256') in cv_files
                                     else XLink('Open', file_link(src, r2.get('archiefkopie', ''))) if src else '')
                new_rows.append(r2)
            new_cols = [ren.get(c, c) for c in cols]
            if 'sha256' in cols:
                new_cols.insert(new_cols.index('cv_id') + 1, 'open_cv')
            return new_cols, new_rows
        li_rijen = linkedin_gegevens_rijen(people, out)
        li_cols = ['kandidaat_id', 'naam', 'linkedin', 'linkedin_bron', 'huidige_functie', 'huidige_werkgever',
                   'werkervaring', 'opleidingen', 'certificeringen', 'vaardigheden', 'over', 'locatie',
                   'email_openbaar', 'telefoon_openbaar']
        xlsx_export(temporary, [
            ('Unieke personen',vcols,view),
            ("Naamloze cv's",*as_kid(naamloos_cols,naamloze)),
            ('Bestanden en status',*as_kid(fcols,files))]
            + ([('LinkedIn-gegevens', li_cols, li_rijen)] if li_rijen else []))
        # Controle nodig, leesfouten en online-details staan alleen nog in de CSV-bestanden in de exportmap.
        with zipfile.ZipFile(temporary) as z:
            if z.testzip() is not None or 'xl/workbook.xml' not in z.namelist():
                raise RuntimeError('Het gemaakte Excel-bestand is ongeldig')
        try:
            os.replace(temporary, final)
        except PermissionError:
            # Oude Excel staat nog open in Excel: Windows blokkeert overschrijven.
            # Bewaar de nieuwe export onder een eigen naam i.p.v. te falen.
            final = out/f"CV_Crawler_Resultaten_{dt.datetime.now().strftime('%Y-%m-%d_%H%M%S')}.xlsx"
            os.replace(temporary, final)
            print('LET OP: de vorige Excel staat nog open, daarom is de nieuwe export opgeslagen als:',
                  final.name, flush=True)
            print('Tip: sluit Excel voor je scant, dan wordt CV_Crawler_Resultaten.xlsx gewoon bijgewerkt.',
                  flush=True)
        save_kandidaten_json(out, vcols, view)
        # Stempel pas NA de volledige, gevalideerde XLSX én bijbehorende export.
        # Bij falen: geen stempel -> Open Excel toont nooit een vorige versie.
        _stat = final.stat()
        _ready = dict(schema=1, filename=final.name, bytes=_stat.st_size,
                      mtime_ns=_stat.st_mtime_ns, classifier_version=CLASSIFIER_VERSION)
        _ready_tmp = out / 'excel_export_geldig.bezig.json'
        try:
            _ready_tmp.write_text(json.dumps(_ready), encoding='utf-8')
            os.replace(_ready_tmp, out / 'excel_export_geldig.json')
        finally:
            _ready_tmp.unlink(missing_ok=True)
        (out/'laatste_excel.txt').write_text(str(final), encoding='utf-8')
    finally:
        if temporary.exists():
            temporary.unlink()
    print(f'EXPORT: {len(people)} unieke persoonsrecords, {len(docs)} CV-bestandslocaties, {len(checks)} controlepunten')
    print(f"NAAMLOZE CV'S: {len(naamloze)} apart op het Excel-tabblad; niet in de kandidatenlijst.")
    print(f'LET OP: alleen documenten die met herkenning V{CLASSIFIER_VERSION} zijn beoordeeld, tellen mee als bevestigd CV.')
    print('EXPORT-CONTROLE: ' + ', '.join(f'{v} {k}' for k,v in categories.most_common())
          if categories else 'EXPORT-CONTROLE: geen leesfouten of online-only bestanden')
    twijfel_overzicht(db)
    print('Excel:', final)
    return people


TAVILY_URL = 'https://api.tavily.com/search'
ONLINE_COLS = ['naam','type','gevonden_waarde','keuze','zekerheid','overeenkomst','bron_url','brontitel',
               'gevonden_op','status','zoekopdracht','persoon_id']


def tavily_search(query, key, include_domains=None, max_results=6, exclude_domains=None, raw=False, depth='basic'):
    body = {'query': query[:390], 'search_depth': depth, 'max_results': max_results,
            'country': 'netherlands', 'topic': 'general'}
    if raw:
        body['include_raw_content'] = 'markdown'   # volledige profieltekst (Info/bio); zelfde 1 credit
    if include_domains:
        body['include_domains'] = include_domains
    if exclude_domains:
        body['exclude_domains'] = exclude_domains
    req = urllib.request.Request(TAVILY_URL, data=json.dumps(body).encode('utf-8'), method='POST',
                                 headers={'Authorization': 'Bearer ' + key,
                                          'Content-Type': 'application/json',
                                          'User-Agent': 'CV-Scanner/10'})
    with urllib.request.urlopen(req, timeout=25) as r:
        data = json.load(r)
    return data.get('results', [])


def tavily_extract(url, key, depth='basic'):
    """Lees één openbare pagina via Tavily Extract. Basic: 5 gelukte = 1 credit; advanced: 5 gelukte = 2 credits.
    Mislukt = gratis. Geeft (tekst, foutmelding)."""
    body = {'urls': [url], 'extract_depth': depth, 'format': 'markdown'}
    req = urllib.request.Request('https://api.tavily.com/extract', data=json.dumps(body).encode('utf-8'),
                                 method='POST', headers={'Authorization': 'Bearer ' + key,
                                                         'Content-Type': 'application/json',
                                                         'User-Agent': 'CV-Scanner/10'})
    with urllib.request.urlopen(req, timeout=40) as r:
        data = json.load(r)
    res = data.get('results') or []
    failed = data.get('failed_results') or []
    if depth != 'basic':
        return ((res[0].get('raw_content') or '') if res else '',
                '; '.join(str(f.get('error', f)) for f in failed)[:200])
    return (res[0].get('raw_content') or '') if res else ''


def serper_search(query, key):
    """Google-zoekopdracht via Serper.dev met JOUW sleutel (1 zoekopdracht van je tegoed)."""
    body = {'q': query[:400], 'gl': 'nl', 'hl': 'nl', 'num': 10}
    req = urllib.request.Request('https://google.serper.dev/search', data=json.dumps(body).encode('utf-8'),
                                 method='POST', headers={'X-API-KEY': key, 'Content-Type': 'application/json',
                                                         'User-Agent': 'CV-Scanner/12'})
    with urllib.request.urlopen(req, timeout=25) as r:
        data = json.load(r)
    return [{'url': o.get('link', ''), 'title': o.get('title', ''), 'content': o.get('snippet', '')}
            for o in data.get('organic', [])]


def serpapi_search(query, key):
    """Google-zoekopdracht via SerpApi (gratis: elke maand een nieuw tegoed)."""
    params = urllib.parse.urlencode({'engine': 'google', 'q': query[:400], 'gl': 'nl', 'hl': 'nl',
                                     'num': 10, 'api_key': key})
    req = urllib.request.Request('https://serpapi.com/search.json?' + params, headers={'User-Agent': 'CV-Scanner/12'})
    with urllib.request.urlopen(req, timeout=40) as r:
        data = json.load(r)
    if data.get('error') and 'hasn' not in str(data.get('error')).lower():
        # bv. 'Your account has run out of searches.' -> behandelen als 'tegoed op'
        raise urllib.error.HTTPError(req.full_url, 429, str(data['error']), None, None)
    return [{'url': o.get('link', ''), 'title': o.get('title', ''), 'content': o.get('snippet', '')}
            for o in data.get('organic_results', [])]


def google_search(query):
    """Eerst Serper (eenmalig tegoed) tot dat op is, daarna SerpApi (maandelijks tegoed).
    Geeft (resultaten, dienst) of (None, reden) als geen van beide kan."""
    for name, env, fn in (('Serper', 'SERPER_API_KEY', serper_search), ('SerpApi', 'SERPAPI_API_KEY', serpapi_search)):
        key = os.environ.get(env, '').strip()
        if not key:
            continue
        try:
            return fn(query, key), name
        except urllib.error.HTTPError as exc:
            reason = 'tegoed op of sleutel ongeldig' if exc.code in (400, 401, 402, 403, 429) else f'fout {exc.code}'
            print(f'ONLINE: {name} gestopt ({reason}); ' +
                  ('verder met SerpApi.' if name == 'Serper' and os.environ.get('SERPAPI_API_KEY') else 'geen Google meer.'),
                  flush=True)
            os.environ[env] = ''          # deze run niet meer proberen
        except (urllib.error.URLError, TimeoutError, ValueError) as exc:
            print(f'ONLINE: {name} lukte niet:', str(exc)[:100], flush=True)
            return None, 'netwerk'
    return None, 'geen sleutel'


_HAAK_AT = r'\s*(?:\[\s*(?:at|@|apenstaartje)\s*\]|\(\s*(?:at|@|apenstaartje)\s*\)|\{\s*(?:at|@)\s*\}|<\s*at\s*>|\uff20|@)\s*'
_HAAK_DOT = r'\s*(?:\[\s*(?:dot|punt)\s*\]|\(\s*(?:dot|punt)\s*\)|\{\s*(?:dot|punt)\s*\}|<\s*dot\s*>|\.)\s*'
_TLD = r'(?:com|nl|be|org|net|eu|de|io|info|nu|me|biz|uk|fr|es|it)'
_VERSTOPT_HAAK = re.compile(rf'([\w+-]+(?:{_HAAK_DOT}[\w+-]+)*){_HAAK_AT}([a-z0-9-]+(?:{_HAAK_DOT}[a-z0-9-]+)*){_HAAK_DOT}({_TLD})\b', re.I)
_WOORD_DOT = r'\s+(?:dot|punt)\s+|\.'
_VERSTOPT_WOORD = re.compile(rf'([\w+-]+(?:(?:{_WOORD_DOT})[\w+-]+)*)\s+(?:at|apenstaartje)\s+([a-z0-9-]+)(?:{_WOORD_DOT})({_TLD})\b', re.I)


# Alleen maildiensten (geen werkgevers zoals Ziggo/KPN): 'Team lead at Ziggo.nl' is geen e-mail.
_ALLEEN_MAIL = {'gmail.com', 'googlemail.com', 'hotmail.com', 'hotmail.nl', 'live.nl', 'live.com', 'outlook.com',
                'outlook.nl', 'msn.com', 'yahoo.com', 'yahoo.nl', 'icloud.com', 'me.com', 'proton.me', 'protonmail.com'}


def ontsluier_email(text):
    """Verstopte e-mail in een bio/Info-tekst leesbaar maken: 'jan.bakker [at] gmail [dot] com',
    'jan.bakker(at)gmail.com', 'jan.bakker @ gmail . com', 'jan punt bakker apenstaartje gmail punt com'.
    Alleen tussen haakjes/@ altijd; met los 'at' alleen bij een maildienst (gmail, hotmail, …) (anders is 'Sales at Bol.com' geen mail)."""
    def haak(m):
        if '@' in m.group(0) and not re.search(r'\s|\[|\(|\{|<|\uff20', m.group(0)):
            return m.group(0)                       # al een gewone e-mail
        loc = re.sub(_HAAK_DOT, '.', m.group(1), flags=re.I)
        dom = re.sub(_HAAK_DOT, '.', m.group(2), flags=re.I)
        return f' {loc}@{dom}.{m.group(3)} '
    def woord(m):
        dom = f'{m.group(2)}.{m.group(3)}'.lower()
        m0 = m.group(0).lower()
        if dom not in _ALLEEN_MAIL and 'apenstaartje' not in m0:
            return m.group(0)
        if not ('apenstaartje' in m0 or re.search(r'\s(?:dot|punt)\s', m0) or re.search(r'[._\d]', m.group(1))):
            return m.group(0)                       # 'Consultant at Outlook.com': functie, geen e-mail
        loc = re.sub(_WOORD_DOT, '.', m.group(1), flags=re.I)
        return f' {loc}@{dom} '
    text = _VERSTOPT_HAAK.sub(haak, text or '')
    return _VERSTOPT_WOORD.sub(woord, text)


def fix_nl_mobile(text):
    """Google laat het '+' soms weg: '31 6 30…' / '316…' -> '+31 6 …' (alleen voor zoektekst)."""
    # Ook '31 06 12345678' (0 te veel). Alleen als er precies 8 cijfers volgen (dus geen datum als '31 06 2020').
    return re.sub(r'(?<![\d+])31[\s.\-]*(?:\(\s*0\s*\)|0)?[\s.\-]*6(?=[\s.\-]*\d(?:[\s.\-]*\d){7}(?![\s.\-]*\d))',
                  '+31 6', text)


def linkedin_slug(link):
    return link.rstrip('/').rsplit('/', 1)[-1].lower()


def _name_words(name):
    return [w for w in normalized_name(name).split() if len(w) >= 3 and w not in NAME_FILLERS]


RAW_CUT_RE = re.compile(r'\n#+\s*(?:people also viewed|others named|more profiles|similar profiles|'
                        r'anderen met de naam|mensen bekeken ook|explore more|recommendations|aanbevelingen)', re.I)


def own_profile_text(raw):
    """Alleen het eigen profieldeel; stukken over andere mensen (aanbevelingen, 'ook bekeken') eraf."""
    raw = raw or ''
    m = RAW_CUT_RE.search(raw)
    return (raw[:m.start()] if m else raw)[:8000]


def own_linkedin(link, title, words, full):
    slug = normalized_name(link.rsplit('/', 1)[-1].replace('-', ' ')).replace(' ', '')
    return ((len(words) >= 2 and words[0] in slug and words[-1] in slug and slug.startswith((words[0], words[-1])))
            or normalized_name(title).startswith(normalized_name(full)))


PRIVATE_SOCIAL = ['facebook.com', 'instagram.com', 'tiktok.com', 'x.com', 'twitter.com', 'linkedin.com',
                  'snapchat.com', 'pinterest.com']


def fetch_page(url, limit=600_000, timeout=8):
    """Haal één openbare pagina op (alleen lezen). Geeft (html, eind-url) of ('', url)."""
    try:
        req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0 (CV-Scanner; contactpagina lezen)'})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            if 'html' not in (r.headers.get('Content-Type') or 'text/html'):
                return '', url
            return r.read(limit).decode('utf-8', errors='replace'), r.geturl()
    except Exception:
        return '', url


def contacts_from_html(raw):
    """E-mail en telefoon uit een webpagina (ook mailto:/tel:-links)."""
    emails = [m.lower() for m in re.findall(r'mailto:([^"\'?\s>]+)', raw, re.I)]
    phones = re.findall(r'tel:([+\d\s().-]{8,20})', raw, re.I)
    text = html.unescape(re.sub(r'<[^>]+>', ' ', re.sub(r'(?is)<(script|style).*?</\1>', ' ', raw)))
    emails += emails_in(text)
    found_phones = []
    for ph in phones:
        n = norm_phone(ph)
        if n:
            found_phones.append(n)
    found_phones += phones_in(text)
    return list(dict.fromkeys(e.strip('.') for e in emails if '@' in e)), list(dict.fromkeys(found_phones)), text


def github_profile(url):
    """Openbaar GitHub-profiel via de gratis GitHub-API: e-mail (als openbaar) en eigen website."""
    m = re.search(r'github\.com/([A-Za-z0-9-]{1,39})(?:/|$)', url)
    if not m or m.group(1).lower() in ('orgs', 'about', 'features', 'topics', 'sponsors'):
        return {}
    try:
        req = urllib.request.Request('https://api.github.com/users/' + m.group(1),
                                     headers={'User-Agent': 'CV-Scanner', 'Accept': 'application/vnd.github+json'})
        with urllib.request.urlopen(req, timeout=8) as r:
            return json.load(r)
    except Exception:
        return {}


def own_site_contacts(url, words):
    """Lees de eigen site uit het cv (homepage + max. 2 contactpagina's op hetzelfde domein)."""
    raw, final = fetch_page(url)
    if not raw:
        return [], [], ''
    host = urllib.parse.urlparse(final).netloc
    pages = [raw]
    for href in re.findall(r'href=["\']([^"\']+)["\']', raw, re.I):
        if re.search(r'(?i)contact|over-?mij|about', href):
            target = urllib.parse.urljoin(final, href)
            if urllib.parse.urlparse(target).netloc == host and len(pages) < 3:
                more, _ = fetch_page(target)
                if more:
                    pages.append(more)
    emails, phones, text = [], [], ''
    for page in pages:
        e, p, t = contacts_from_html(page)
        emails += e; phones += p; text += ' ' + t
    return list(dict.fromkeys(emails)), list(dict.fromkeys(phones)), text


def gemeen(row):
    n = (row.get('overeenkomst') or '').count('✓')
    return f' ({n} {"ding" if n == 1 else "dingen"} gemeen)' if n else ''


ONLINE_DECISIONS = 'online_beslissingen.json'


def online_key(naam, typ, value, pid=''):
    clean = (value or '').lstrip(chr(39)).strip().lower()
    return f"{pid or normalized_name(naam)}|{typ}|{clean}"


def load_online_decisions(out):
    try:
        return json.loads((out / ONLINE_DECISIONS).read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return {}


def merge_online_suggestions(people, out):
    """Zet online gevonden gegevens in APARTE kolommen (nooit over CV-gegevens heen)."""
    path = out / 'online_suggesties.csv'
    for person in people:
        for col in ('online_linkedin','online_email','online_telefoon','online_bron','online_zekerheid','online_waarom',
                    'te_controleren_linkedin','te_controleren_email','te_controleren_telefoon'):
            person.setdefault(col, '')
    if not path.exists():
        return
    try:
        with path.open(encoding='utf-8-sig', newline='') as f:
            rows = list(csv.DictReader(f, delimiter=';'))
    except (OSError, csv.Error, UnicodeError):
        return
    by_id = {p['persoon_id']: p for p in people}
    by_name = {normalized_name(p['naam']): p for p in people if p['naam']}
    # Jouw eigen keuzes ('klopt' / 'klopt niet') gaan voor alles.
    decisions = load_online_decisions(out)
    for row in rows:
        dec = decisions.get(online_key(row.get('naam', ''), row.get('type', ''), row.get('gevonden_waarde', ''),
                                       row.get('persoon_id', '')))
        if dec == 'ja':
            row['zekerheid'] = 'waarschijnlijk (door jou bevestigd)'
            row['keuze'] = 'beste'
            row['overeenkomst'] = 'door jou gecontroleerd en bevestigd' + (
                ' | ' + row['overeenkomst'] if row.get('overeenkomst') and '✓' in row['overeenkomst'] else '')
        elif dec == 'nee':
            row['keuze'] = 'afgewezen'
    confirmed = {(r.get('persoon_id') or normalized_name(r.get('naam', '')), r.get('type')) for r in rows
                 if r.get('zekerheid') == 'waarschijnlijk (door jou bevestigd)'}
    for row in rows:
        if (row.get('persoon_id') or normalized_name(row.get('naam', '')), row.get('type')) in confirmed \
                and row.get('zekerheid') != 'waarschijnlijk (door jou bevestigd)':
            row['keuze'] = 'afgewezen'      # jij koos een ander profiel/nummer
    order = {'waarschijnlijk (door jou bevestigd)': -1, 'waarschijnlijk': 0, 'controleren': 1, 'naamgenoot - controleren': 2}
    rows.sort(key=lambda r: order.get(r.get('zekerheid', ''), 2))
    for row in rows:
        person = by_id.get(row.get('persoon_id', '')) or by_name.get(normalized_name(row.get('naam', '')))
        if not person:
            continue
        if row.get('keuze') == 'niets':
            typ = row.get('type')
            if typ == 'linkedin':
                namesakes = 'met dezelfde naam gevonden' in (row.get('overeenkomst') or '')
                label = 'LinkedIn: alleen naamgenoten' if namesakes else 'LinkedIn: niet gevonden'
                if not person.get('linkedin') and not person['online_linkedin'] and label not in person['online_zekerheid']:
                    person['online_zekerheid'] = (person['online_zekerheid'] + ' | ' + label).strip(' |')
                    person['naamgenoten_linkedin'] = namesakes
                    why = row.get('overeenkomst') or 'geen profiel met precies deze naam gevonden'
                    person['online_waarom'] = (person['online_waarom'] + ' | LinkedIn: ' + why).strip(' |')
            if typ in ('linkedin', 'email', 'telefoon'):
                person['niet_gevonden_' + typ] = True
            continue
        if row.get('keuze', 'beste') != 'beste':
            continue
        if row.get('zekerheid', '').startswith('naamgenoot'):
            label = {'linkedin': 'LinkedIn', 'email': 'E-mail', 'telefoon': 'Telefoon'}.get(row.get('type'), row.get('type'))
            note = f'{label}: niet ingevuld – meerdere personen met deze naam in je lijst; kies zelf via Online controleren in de scanner'
            if note not in person['online_waarom']:
                person['online_waarom'] = (person['online_waarom'] + ' | ' + note).strip(' |')
            continue
        if not row.get('zekerheid', '').startswith('waarschijnlijk'):
            # Streng: twijfelgeval NIET als zeker invullen, wel als 'te controleren' (klikbaar).
            col = 'te_controleren_' + row.get('type', '')
            if col in person and not person[col]:
                person[col] = row.get('gevonden_waarde', '').lstrip("'")
                total = sum(1 for r in rows if r.get('persoon_id') == row.get('persoon_id')
                            and r.get('type') == row.get('type'))
                label = {'linkedin': 'LinkedIn', 'email': 'E-mail', 'telefoon': 'Telefoon'}.get(row.get('type'), row.get('type'))
                note = f"{label}: niet zeker ({total} mogelijk)"
                person['online_zekerheid'] = (person['online_zekerheid'] + ' | ' + note).strip(' |')
                person['online_waarom'] = (person['online_waarom'] + ' | ' +
                                           f"{label} niet zeker{gemeen(row)}: {row.get('overeenkomst', '')}").strip(' |')
            continue
        col = {'linkedin': 'online_linkedin', 'email': 'online_email', 'telefoon': 'online_telefoon'}.get(row.get('type'))
        if not col or person.get(col) or person.get(row.get('type')):
            continue   # al gevuld (uit cv of eerdere suggestie)
        person[col] = row.get('gevonden_waarde', '').lstrip("'")
        bron = row.get('bron_url', '')
        if bron and bron not in person['online_bron']:
            person['online_bron'] = (person['online_bron'] + ' | ' + bron).strip(' |')
        label = {'linkedin': 'LinkedIn', 'email': 'E-mail', 'telefoon': 'Telefoon'}.get(row.get('type'), row.get('type'))
        door_jou = ' (door jou bevestigd)' if 'bevestigd' in row.get('zekerheid', '') else ''
        person['online_zekerheid'] = (person['online_zekerheid'] + ' | ' + f'{label}: zeker{door_jou}').strip(' |')
        person['online_waarom'] = (person['online_waarom'] + ' | ' +
                                   f"{label} zeker{gemeen(row)}: {row.get('overeenkomst', '')}").strip(' |')


ONLINE_NOISE = {'linkedin','profiel','profile','bekijk','view','netherlands','nederland','connecties',
                'connections','experience','ervaring','education','opleiding','locatie','location','followers',
                'volgers','werkervaring','about','meer','more','https','www','member','lid','professional',
                'professionele','gemeenschap','community','million','miljoen','bekijken','activiteit'}


# Algemene woorden die in duizenden profielen staan: tellen NIET als bewijs.
GENERIC_EVIDENCE = {
 'gemeente','hogeschool','universiteit','university','college','school','student','studente','studies','studie',
 'medewerker','medewerkster','bachelor','master','mbo','hbo','diploma','opleiding','cursus','stage','stagiair',
 'stagiaire','intern','internship','junior','senior','medior','manager','management','adviseur','advisor',
 'assistent','assistant','developer','engineer','consultant','specialist','analist','analyst','officer',
 'coordinator','coördinator','teamleider','leidinggevende','directeur','director','eigenaar','owner','founder',
 'freelance','freelancer','zelfstandig','communicatie','communication','marketing','sales','research','onderzoek',
 'business','software','technology','technologie','services','service','support','klantenservice','customer',
 'administratie','administratief','financieel','finance','juridisch','logistiek','logistics','zorg','healthcare',
 'onderwijs','education','overheid','government','stichting','foundation','group','groep','holding','company',
 'bedrijf','organisatie','organization','nederland','netherlands','holland','amsterdam','rotterdam','utrecht',
 'haarlem','eindhoven','groningen','tilburg','almere','breda','nijmegen','arnhem','leiden','zwolle','delft',
 'europe','europa','international','internationaal','global','digital','digitaal','online','media','design',
 'werkzaam','working','works','currently','momenteel','huidige','current','years','jaren','maanden','months',
 'skills','vaardigheden','talen','languages','engels','english','nederlands','dutch','duits','german',
 # algemene functiewoorden die bij duizenden mensen staan
 'medewerkers','werknemer','employee','staff','vrijwilliger','volunteer','uitzendkracht','oproepkracht','invalkracht',
 'parttime','fulltime','bijbaan','vakantiewerk','trainee','werkstudent','afgestudeerd','graduate','starter',
 'allround','algemeen','general','operator','productie','production','magazijn','warehouse','kassa','kassamedewerker',
 'verkoop','verkoper','verkoopster','winkel','winkelmedewerker','retail','horeca','bediening','bezorger','chauffeur',
 'schoonmaak','schoonmaker','receptie','receptionist','secretaresse','office','kantoor','team','lid','member',
 'projectmedewerker','beleidsmedewerker','baliemedewerker','servicedesk','helpdesk','callcenter','klant','klanten',
 'docent','leraar','teacher','trainer','coach','begeleider','verpleegkundige','verzorgende','zelfstandige','zzp',
 'zzper','ondernemer','entrepreneur','cofounder','partner','lead','head','chief','hoofd','projectleider','projectmanager'}

# Werkgevers waar heel veel mensen werken of die niets zeggen: tellen hooguit als 'middel', nooit als 'sterk'.
BIG_EMPLOYERS = re.compile(r'(?i)^(?:gemeente\b.*|provincie\b.*|rijksoverheid|ministerie\b.*|belastingdienst|defensie|'
    r'politie|uwv|svb|duo|rijkswaterstaat|ns|prorail|postnl|dhl|albert heijn|ah|jumbo|lidl|aldi|plus|dirk|coop|'
    r'hema|action|kruidvat|etos|ikea|mediamarkt|coolblue|bol(?:\.com)?|zalando|mcdonald.?s|burger king|kfc|'
    r'starbucks|randstad|tempo.?team|youngcapital|adecco|manpower|start ?people|olympia|unique|timing|'
    r'ing|rabobank|abn amro|asn|sns|kpn|vodafone|ziggo|t-mobile|odido|philips|shell|unilever|heineken|asml|'
    r'schiphol|klm|tata steel|umc\b.*|.*ziekenhuis.*|.*universiteit.*|.*hogeschool.*|.*university.*)$')
VAGUE_EMPLOYERS = re.compile(r'(?i)^(?:eigen bedrijf|zzp|freelance|freelancer|zelfstandig(?:e)?|diverse(?: werkgevers)?|'
    r'verschillende(?: werkgevers)?|uitzendbureau|via uitzendbureau|n\.?v\.?t\.?|onbekend|-|geen)$')


def _words(text):
    return {w for w in re.findall(r'[a-z]{5,}', asciifold((text or '').lower()))
            if w not in STOP_WORDS and w not in ONLINE_NOISE}


def _cv_words(db, person):
    """Woorden uit de cv('s) van deze persoon: werkgevers, scholen, functies, plaatsen, vaardigheden."""
    words = set()
    if db is None:
        return words
    target = normalized_name(person['naam'])
    for row in db.execute("SELECT name, snippet, employer, role FROM docs WHERE status='cv'"):
        if normalized_name(row['name']) == target:
            words |= _words(row['snippet']) | _words(row['employer']) | _words(row['role'])
    return words - set(_name_words(person['naam'])) - GENERIC_EVIDENCE


NL_PLACES = {'hendrik ido ambacht','capelle aan den ijssel','alphen aan den rijn','bergen op zoom','krimpen aan den ijssel','nieuwerkerk aan den ijssel','berkel en rodenrijs','wijk bij duurstede','ouder amstel','amsterdam','rotterdam','den haag','utrecht','eindhoven','groningen','tilburg','almere','breda',
    'nijmegen','apeldoorn','haarlem','arnhem','enschede','amersfoort','zaanstad','zaandam','hoofddorp','heemstede',
    'haarlemmermeer','zoetermeer','zwolle','leiden','maastricht','dordrecht','ede','delft','alkmaar','emmen','venlo',
    'deventer','helmond','hilversum','heerlen','oss','leeuwarden','purmerend','schiedam','vlaardingen','almelo',
    'gouda','hoorn','amstelveen','diemen','beverwijk','velsen','ijmuiden','castricum','heerhugowaard','katwijk',
    'noordwijk','lisse','weesp','s hertogenbosch','den bosch','capelle aan den ijssel','nieuwegein','houten',
    'zeist','barneveld','veenendaal','wageningen','roermond','sittard','kampen','assen','lelystad','harderwijk',
    'badhoevedorp','bloemendaal','zandvoort','ijsselstein','rijswijk','delfzijl','den helder','schagen'}
SCHOOL_RE = re.compile(r'\b((?:hogeschool|universiteit|university|academie|roc|mbo college|grafisch lyceum)'
                       r'(?: van| voor)? [a-z]+)')


def _cv_snippets(db, person):
    target = normalized_name(person['naam'])
    out = []
    if db is None:
        return out
    for row in db.execute("SELECT name, snippet FROM docs WHERE status='cv'"):
        if normalized_name(row['name']) == target and row['snippet']:
            out.append(row['snippet'])
    return out


def _ai_cv_text(db, person):
    """Korte, leesbare samenvatting van het cv voor de AI-controle (blijft op de computer)."""
    lines = [person.get('naam', '')]
    for label, key in (('Functie', 'huidige_functie'), ('Werkgever', 'laatst_bekende_werkgever'), ('Woonplaats', 'woonplaats')):
        if person.get(key):
            lines.append(f'{label}: {person[key]}')
    for label, col in (('Werkgevers', 'werkgevers'), ('Opleidingen', 'opleidingen'), ('Stages', 'stage'),
                       ('Werkplaatsen', 'werkplaatsen')):
        val = _cv_field(db, person, col) if db is not None else ''
        if val:
            lines.append(f'{label}: {val}')
    snip = (_cv_snippets(db, person) or [''])[0]
    if snip:
        lines.append('Tekst uit het cv: ' + snip[:1500])
    return '\n'.join(l for l in lines if l)


def _edits(a, b, limit):
    """Damerau-Levenshtein-afstand (omgedraaide letters = 1 fout), stopt boven limit."""
    if abs(len(a) - len(b)) > limit:
        return limit + 1
    prev2, prev = None, list(range(len(b) + 1))
    for i in range(1, len(a) + 1):
        cur = [i] + [0] * len(b)
        for j in range(1, len(b) + 1):
            cost = a[i - 1] != b[j - 1]
            cur[j] = min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + cost)
            if prev2 and i > 1 and j > 1 and a[i - 1] == b[j - 2] and a[i - 2] == b[j - 1]:
                cur[j] = min(cur[j], prev2[j - 2] + 1)
        if min(cur) > limit:
            return limit + 1
        prev2, prev = prev, cur
    return prev[-1]


WORD_ENDINGS = ('ing', 'ings', 'er', 'ers', 'en', 'e', 's', 'r', 'ie', 'ies')


def word_like(a, b):
    """0 = exact gelijk, 1 = alleen andere uitgang (engineer/engineering), 2 = één typfout, None = ander woord.
    Bewust streng: woorden < 5 letters en getallen moeten exact zijn, eerste letter moet gelijk zijn,
    hooguit één letter fout of twee letters omgedraaid."""
    if a == b:
        return 0
    if min(len(a), len(b)) < 5 or a[0] != b[0] or a.isdigit() or b.isdigit():
        return None
    short, long_ = sorted((a, b), key=len)
    if long_.startswith(short) and long_[len(short):] in WORD_ENDINGS:
        return 1
    if len(a) == len(b):
        diff = [i for i in range(len(a)) if a[i] != b[i]]
        if len(diff) == 1 and diff[0] >= len(a) - 2:
            return None                   # andere letter in de uitgang = ander woord (planner ≠ plannen)
    return 2 if _edits(a, b, 1) <= 1 else None


def _words_like(pw, bw):
    kinds = [word_like(p, w) for p, w in zip(pw, bw)]
    # Max één echte typfout per bedrijfs-/schoolnaam/functie, anders te gokkerig.
    return None not in kinds and kinds.count(2) <= 1


def fuzzy_find(phrase, blob):
    """Zoek phrase woord-voor-woord in blob (beide genormaliseerd), typfout-tolerant.
    Geeft het gevonden stuk tekst terug, of ''."""
    pw, bw = phrase.split(), blob.split()
    if not pw:
        return ''
    for i in range(len(bw) - len(pw) + 1):
        if _words_like(pw, bw[i:i + len(pw)]):
            return ' '.join(bw[i:i + len(pw)])
    return ''


def phrase_like(a, b):
    """Twee korte waarden (bedrijf/school) zijn gelijk, op typfouten na."""
    aw, bw = a.split(), b.split()
    return len(aw) == len(bw) and _words_like(aw, bw)


FUNCTION_WORDS = {'van', 'voor', 'het', 'een', 'de', 'der', 'den', 'en', 'and', 'of', 'the', 'in', 'for', 'with',
                  'met', 'bij', 'at', 'to', 'te', 'op', 'aan', 'als', 'door', 'from', 'on', 'a', 'an', 'om', 'over'}
# Landen en buitenlandse plaatsen die in cv's als 'bedrijf' worden aangezien ('Developer | India').
FOREIGN_PLACES = {
    'india', 'south africa', 'africa', 'poland', 'polen', 'katowice', 'krakow', 'warsaw', 'warschau', 'wroclaw',
    'bangalore', 'bengaluru', 'mumbai', 'delhi', 'new delhi', 'chennai', 'pune', 'hyderabad', 'kolkata', 'noida',
    'gurgaon', 'woking', 'london', 'londen', 'manchester', 'uk', 'united kingdom', 'england', 'engeland', 'usa',
    'united states', 'us', 'new york', 'germany', 'duitsland', 'berlin', 'berlijn', 'munich', 'munchen',
    'frankfurt', 'hamburg', 'dusseldorf', 'keulen', 'koln', 'belgie', 'belgium', 'brussel', 'brussels', 'antwerpen',
    'gent', 'leuven', 'france', 'frankrijk', 'paris', 'parijs', 'spain', 'spanje', 'madrid', 'barcelona', 'italy',
    'italie', 'rome', 'milan', 'milaan', 'portugal', 'lisbon', 'lissabon', 'porto', 'turkije', 'turkey', 'istanbul',
    'ankara', 'morocco', 'marokko', 'suriname', 'paramaribo', 'curacao', 'aruba', 'egypt', 'egypte', 'cairo',
    'china', 'shanghai', 'beijing', 'japan', 'tokyo', 'brazil', 'brazilie', 'romania', 'roemenie', 'bucharest',
    'boekarest', 'bulgaria', 'bulgarije', 'sofia', 'ukraine', 'oekraine', 'kiev', 'kyiv', 'russia', 'rusland',
    'moscow', 'moskou', 'greece', 'griekenland', 'athens', 'athene', 'ireland', 'ierland', 'dublin', 'scotland',
    'denmark', 'denemarken', 'copenhagen', 'sweden', 'zweden', 'stockholm', 'norway', 'noorwegen', 'finland',
    'austria', 'oostenrijk', 'vienna', 'wenen', 'switzerland', 'zwitserland', 'zurich', 'geneva', 'czech republic',
    'prague', 'praag', 'hungary', 'hongarije', 'budapest', 'serbia', 'servie', 'belgrade', 'croatia', 'kroatie',
    'pakistan', 'karachi', 'lahore', 'islamabad', 'bangladesh', 'dhaka', 'sri lanka', 'colombo', 'nepal',
    'indonesia', 'indonesie', 'jakarta', 'philippines', 'filipijnen', 'manila', 'vietnam', 'singapore',
    'malaysia', 'maleisie', 'australia', 'australie', 'sydney', 'canada', 'toronto', 'mexico', 'argentina',
    'nigeria', 'lagos', 'kenya', 'nairobi', 'ghana', 'iran', 'tehran', 'iraq', 'irak', 'syria', 'syrie',
    'afghanistan', 'kabul', 'dubai', 'uae', 'saudi arabia', 'israel', 'europe', 'europa', 'asia', 'azie',
    'netherlands', 'nederland', 'holland', 'the netherlands', 'remote', 'worldwide', 'emea', 'benelux'}
# Woorden die niets over een bedrijf/school zeggen (stukken zin, vakgebieden, rollen).
EVIDENCE_JUNK = {
    'director', 'directeur', 'it', 'ict', 'technical', 'accounting', 'environment', 'environments',
    'werkzaamheden', 'functie', 'functies', 'ontwikkelen', 'ontwikkeling', 'optimization', 'implementation',
    'implementatie', 'verantwoordelijk', 'inrichten', 'project', 'projects', 'projecten', 'diverse', 'various',
    'klanten', 'clients', 'client', 'opdrachtgever', 'opdrachtgevers', 'eigen', 'own', 'library', 'department',
    'afdeling', 'divisie', 'division', 'unit', 'branch', 'vestiging', 'hoofdkantoor', 'headquarters', 'data',
    'systems', 'system', 'systemen', 'network', 'netwerk', 'web', 'cloud', 'security', 'beveiliging', 'ict',
    'informatie', 'information', 'operations', 'maintenance', 'onderhoud', 'beheer', 'development', 'testing',
    'test', 'quality', 'kwaliteit', 'project', 'program', 'programma', 'change', 'innovation', 'innovatie',
    'strategy', 'strategie', 'hr', 'finance', 'financien', 'administration', 'consulting', 'consultancy',
    'interim', 'detachering', 'secondment', 'contract', 'contractor', 'opdracht', 'tijdelijk', 'temporary',
    'werk', 'work', 'job', 'baan', 'role', 'rol', 'position', 'positie', 'stage', 'afstuderen', 'graduation',
    'thesis', 'scriptie', 'diverse', 'overig', 'other', 'others', 'personal', 'persoonlijk', 'heden', 'present',
    'today', 'now', 'sinds', 'since', 'tot', 'until', 'jaar', 'year', 'maand', 'month'}
SCHOOL_TYPE_WORDS = {'hogeschool', 'universiteit', 'university', 'academie', 'academy', 'college', 'lyceum',
                     'gymnasium', 'atheneum', 'school', 'roc', 'instituut', 'institute', 'institut', 'campus',
                     'faculteit', 'faculty', 'scholengemeenschap', 'mbo', 'hbo', 'wo', 'technische', 'technical',
                     'business', 'library', 'centre', 'center', 'centrum', 'state',
                     'national', 'nationale', 'international', 'internationale', 'applied', 'sciences', 'science'}
ROLE_WORDS = ('engineer', 'developer', 'ontwikkelaar', 'manager', 'consultant', 'analist', 'analyst', 'beheerder',
              'architect', 'programmeur', 'programmer', 'designer', 'ontwerper', 'tester', 'specialist', 'adviseur',
              'advisor', 'administrator', 'leider', 'lead', 'coordinator', 'owner', 'master', 'officer', 'directeur',
              'director', 'monteur', 'technicus', 'technician', 'assistent', 'assistant', 'scientist', 'trainer',
              'docent', 'controller', 'accountant', 'auditor', 'recruiter', 'planner', 'operator', 'expert',
              'medewerker', 'coach', 'head', 'hoofd', 'secretaresse', 'officemanager', 'kok', 'chef', 'verkoper',
              'researcher', 'onderzoeker', 'student', 'stagiair', 'intern', 'trainee')
ROLE_GENERIC_EXTRA = {'project', 'it', 'ict', 'data', 'test', 'web', 'applicatie', 'application', 'functioneel',
                      'functional', 'technisch', 'technical', 'informatie', 'information', 'systeem', 'system',
                      'systems', 'network', 'netwerk', 'office', 'full', 'stack', 'fullstack', 'front', 'end',
                      'frontend', 'backend', 'back', 'mobile', 'cloud', 'devops', 'java', 'net', 'python', 'php',
                      'sql', 'quality', 'qa', 'product', 'scrum', 'agile', 'delivery', 'solution', 'solutions',
                      'interim', 'freelance', 'zzp', 'lead', 'principal', 'staff', 'chief', 'it', 'and', 'en'}


def evidence_term_ok(label, phrase):
    """Mag dit (uit het cv gehaalde) kenmerk meetellen als bewijs dat een LinkedIn-profiel van deze persoon is?
    Nee voor rommel als 'india', 'university of', 'director', 'ontwikkelen van een nieuw ...', 'Bangalore' als functie."""
    words = (phrase or '').split()
    if not words or label.startswith(('woonplaats', 'werkplaats', 'plaats')) or phrase == '__stagiair__':
        return bool(words)
    if len(words) > 5 or words[-1] in FUNCTION_WORDS or len(words[-1]) < 2 or len(words[0]) < 2:
        return False
    if sum(w in FUNCTION_WORDS and not (label == 'school' and w == 'of') for w in words) >= 2:
        return False                                   # stuk zin ('university of X of Y' mag wel)
    if phrase in FOREIGN_PLACES or phrase in NL_PLACES:
        return False
    content = [w for w in words if w not in FUNCTION_WORDS]
    if label == 'school':
        return any(w not in SCHOOL_TYPE_WORDS and w not in EVIDENCE_JUNK and len(w) >= 2 for w in content)
    if label == 'functie':
        if not any(w.endswith(ROLE_WORDS) for w in content):
            return False                               # 'Bangalore', 'Philips Crystalize', 'Summa College'
        return any(w not in GENERIC_EVIDENCE and w not in ROLE_GENERIC_EXTRA and not w in ROLE_WORDS
                   and w not in EVIDENCE_JUNK for w in content)   # 'Project Manager' alleen is te algemeen
    return any(w not in GENERIC_EVIDENCE and w not in STOP_WORDS and w not in ONLINE_NOISE
               and w not in EVIDENCE_JUNK and w not in FOREIGN_PLACES and w not in ROLE_GENERIC_EXTRA
               and not w.endswith(ROLE_WORDS) and len(w) >= 2 for w in content)


def _clean_org(value):
    """'Solutionnow · Hoofddorp' -> 'Solutionnow';  'Codewerk B.V. (fictief), Alkmaar' -> 'Codewerk'."""
    value = re.split(r'\s*(?:·|,|\||\(| - | – )\s*', value or '')[0]
    words = [w for w in normalized_name(value).split() if w not in ('bv', 'b', 'v', 'nv', 'vof')]
    return ' '.join(words)[:40].strip()


def _cv_stage(db, person):
    if db is None:
        return ''
    target = normalized_name(person['naam'])
    cols = {r[1] for r in db.execute('PRAGMA table_info(docs)')}
    if 'stage' not in cols:
        return ''
    for row in db.execute("SELECT name, stage FROM docs WHERE status='cv' AND stage != '' ORDER BY mtime_ns DESC"):
        if normalized_name(row['name']) == target:
            return row['stage']
    return ''


def _cv_field(db, person, col):
    if db is None or col not in {r[1] for r in db.execute('PRAGMA table_info(docs)')}:
        return ''
    target = normalized_name(person['naam'])
    for row in db.execute(f"SELECT name, {col} FROM docs WHERE status='cv' AND {col} != '' ORDER BY mtime_ns DESC"):
        if normalized_name(row['name']) == target:
            return row[col]
    return ''


def _cv_bijbanen(db, person):
    if db is None:
        return []
    if 'bijbanen' not in {r[1] for r in db.execute('PRAGMA table_info(docs)')}:
        return []
    target = normalized_name(person['naam'])
    out = []
    for row in db.execute("SELECT name, bijbanen FROM docs WHERE status='cv' AND bijbanen != '' ORDER BY mtime_ns DESC"):
        if normalized_name(row['name']) == target:
            out += [x.strip() for x in row['bijbanen'].split(' ; ') if x.strip()]
    return list(dict.fromkeys(out))[:4]


def _cv_sites(db, person):
    sites = []
    if db is None:
        return sites
    target = normalized_name(person['naam'])
    for row in db.execute("SELECT name, websites FROM docs WHERE status='cv'"):
        if normalized_name(row['name']) == target:
            try:
                sites += json.loads(row['websites'] or '[]')
            except ValueError:
                pass
    return sites


def profielen_voorbereiden(people, todo, cache, cache_path, profielen, max_people):
    """Stap 1 (Apify eerst): alle LinkedIn-URL's verzamelen en in batches ophalen.
    - URL's uit het cv (alle kandidaten, ook wie al alles heeft: voor het tabblad LinkedIn-gegevens).
    - Wie geen URL heeft: één Google-zoekopdracht (Serper, anders SerpApi) naar 'site:linkedin.com/in "naam"
      bedrijf', opgeslagen onder dezelfde sleutel als de gewone online stap (die zoekt dan niet opnieuw).
      Alleen profielen met precies die naam gaan naar Apify (max 3 per persoon)."""
    urls = []
    for p in people:
        for l in linkedins_in(p.get('linkedin') or ''):
            urls.append(l)
    gezocht = 0
    for p in todo[:max_people]:
        if p.get('linkedin'):
            continue
        full = p['naam'].replace('"', '')
        ck = 'linkedin|' + normalized_name(full)
        if ck not in cache:
            emp = _clean_org(p.get('laatst_bekende_werkgever') or '')
            rol = (p.get('huidige_functie') or '')[:45].replace('"', '')
            if len(emp) >= 3 and emp not in NL_PLACES and not VAGUE_EMPLOYERS.match(emp) and not BIG_EMPLOYERS.match(emp):
                term = '"' + re.sub(r'\(.*?\)', ' ', (p.get('laatst_bekende_werkgever') or '').split(',')[0]).replace('"', '').strip()[:45] + '"'
            else:
                term = rol if rol and role_is_specific(rol) else ''
            if not term:
                ck += '|alleen_naam'          # zelfde sleutel als de gewone online stap
            if ck in cache:
                res = cache[ck]
            else:
                res, via = google_search(f'site:linkedin.com/in "{full}" {term}'.strip())
                tkey = os.environ.get('TAVILY_API_KEY', '').strip()
                if res is None and tkey:
                    try:
                        res = tavily_search(f'"{full}" {term}'.strip(), tkey, include_domains=['linkedin.com'], max_results=20)
                    except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError, ValueError):
                        res = None
            if res is None:
                print('PROFIELEN: geen zoektegoed meer (Serper, SerpApi, Tavily) om LinkedIn-adressen te vinden; rest volgende keer.', flush=True)
                break
            if ck not in cache:
                gezocht += 1
                cache[ck] = [{k: r.get(k, '') for k in ('url', 'title', 'content')} for r in res]
                cache['q|' + ck] = f'"{full}" {term}'.strip()
        words = _name_words(full)
        eigen = []
        for r in cache.get(ck) or []:
            for l in linkedins_in(r.get('url', '')):
                if own_linkedin(l, r.get('title', ''), words, full) and l not in eigen:
                    eigen.append(l)
        urls += eigen[:3]
    if gezocht:
        print(f'PROFIELEN: {gezocht} LinkedIn-adressen gezocht (Serper/SerpApi/Tavily).', flush=True)
    try:
        profielen.ophalen(urls, cache, log=lambda m: print(m, flush=True))
    finally:
        try:
            cache_path.write_text(json.dumps(cache, ensure_ascii=False), encoding='utf-8')
        except OSError:
            pass


def linkedin_gegevens_rijen(people, out):
    """Tabblad 'LinkedIn-gegevens': alleen voor profielen die zeker van de kandidaat zijn (uit het cv of online
    'zeker'). Gegevens uit het openbare profiel via Apify."""
    try:
        import profielbron as profielen
        cache = json.loads((out / 'online_cache.json').read_text(encoding='utf-8'))
    except Exception:
        return []
    rijen = []
    for p in people:
        bron = 'cv' if p.get('linkedin') else ''
        link = p.get('linkedin') or ''
        if not link and p.get('online_linkedin'):          # alleen zekere online profielen staan hier
            link, bron = p.get('online_linkedin') or '', 'online (zeker)'
        s = profielen.slug(link)
        g = profielen.gegevens_van(cache, s) if s else {}
        if not g:
            continue
        rijen.append(dict(kandidaat_id=p.get('kandidaat_id', ''), naam=p.get('naam', ''), linkedin=link, linkedin_bron=bron,
                          huidige_functie=g['functie'], huidige_werkgever=g['werkgever'],
                          werkervaring=' | '.join(g['werkervaring'])[:3000] or '(niet openbaar)',
                          opleidingen=' | '.join(g['opleidingen'])[:2000], certificeringen=' | '.join(g['certificeringen'])[:1500],
                          vaardigheden=', '.join(g['vaardigheden'])[:1000], over=g['over'], locatie=g['locatie'],
                          email_openbaar=g['email'], telefoon_openbaar=g['telefoon']))
    return rijen


def online_suggestions(people, out, db=None, max_people=500, delay=0.4):
    """Zoek ontbrekende LinkedIn / e-mail / telefoon online op via Tavily.
    Alleen voor personen bij wie iets ontbreekt. Een profiel heet pas
    'waarschijnlijk' als het ook inhoudelijk overeenkomt met het cv
    (werkgever, school, functie, plaats). Alles blijft een SUGGESTIE."""
    key = os.environ.get('TAVILY_API_KEY', '').strip()
    try:
        import profielbron as profielen          # Apify
    except Exception:
        profielen = None
    bd_aan = bool(profielen and profielen.sleutel())
    google_aan = bool(os.environ.get('SERPER_API_KEY', '').strip() or os.environ.get('SERPAPI_API_KEY', '').strip())
    if not key and not bd_aan and not google_aan:
        print('ONLINE: geen zoeksleutel ingesteld (Apify, Serper, SerpApi of Tavily); online aanvullen overgeslagen.', flush=True)
        return False
    cache_path = out / 'online_cache.json'
    try:
        cache = json.loads(cache_path.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        cache = {}
    if os.environ.get('CV_ONLINE_VERS') == '1':
        # Gekozen: iedereen opnieuw opzoeken (actueel, kost credits). Oude resultaten niet gebruiken.
        print('ONLINE: alles opnieuw opzoeken (opgeslagen zoekresultaten worden niet gebruikt).', flush=True)
        cache = {}
    collected = []
    checked = used = 0
    google_used = 0
    google_via = {}
    tavily_out = False          # Tavily-tegoed op: verder via Google (Serper/SerpApi)
    todo = [p for p in people if p['naam'] and not p.get('naam_is_bedrijf') and not p.get('niet_voor_loxo') and not all(p[k] for k in ('email','telefoon','linkedin'))]
    name_count = collections.Counter(normalized_name(p['naam']) for p in people if p['naam'])
    if bd_aan or not key:
        # Volgorde: Apify (profielgegevens) -> Serper -> SerpApi -> Tavily (laatste reserve).
        tavily_out = True
    if bd_aan:
        profielen_voorbereiden(people, todo, cache, cache_path, profielen, max_people)
    print(f'ONLINE: {len(todo)} van {len(people)} personen missen LinkedIn, e-mail of telefoon.', flush=True)
    for person in todo:
        if checked >= max_people:
            print(f'ONLINE: limiet van {max_people} personen bereikt.', flush=True)
            break
        checked += 1
        full = person['naam'].replace('"', '')
        company = (person.get('laatst_bekende_werkgever') or '')[:45].replace('"', '')
        words = _name_words(full)
        cv_words = _cv_words(db, person)
        namesake = name_count[normalized_name(full)] > 1
        role = (person.get('huidige_functie') or '')[:45].replace('"', '')
        employer_raw = re.sub(r'\(.*?\)', ' ', (person.get('laatst_bekende_werkgever') or '').split(',')[0])
        employer_core = ' '.join(w for w in normalized_name(employer_raw).split()
                                 if w not in ('bv', 'b', 'v', 'nv', 'vof'))[:40].strip()
        stages = [x.strip() for x in _cv_stage(db, person).split(' ; ')
                  if x.strip() and normalized_name(x) not in NL_PLACES]
        stage = stages[0] if stages else ''
        # Algemene functie ('Medewerker', 'Stagiair' …) zegt niets: niet op zoeken en niet als bewijs tellen.
        role_generic = bool(role) and not role_is_specific(role)
        if role_generic:
            role = ''
        role_is_stage = not role and bool(stage)
        if role_is_stage:
            role = stage[:45].replace('"', '')        # geen huidige functie: zoek op stageplek
        elif not role and employer_core:
            role = employer_core                     # geen (bruikbare) functie: zoek op werkgever
        key_phrases = {}
        # Kerntermen met gewicht: sterk (uniek), middel, klein (bv. plaats).
        terms = []   # (label, getoonde waarde, genormaliseerde zoektekst, gewicht)
        emp = _clean_org(person.get('laatst_bekende_werkgever') or '')
        stage_org = _clean_org(stage)
        if len(emp) >= 3 and emp not in NL_PLACES and not VAGUE_EMPLOYERS.match(emp):   # 'Den Haag' = plaats
            if BIG_EMPLOYERS.match(emp):
                terms.append(('werkgever (groot)', emp, emp, 'middel'))   # duizenden collega's: geen sterk bewijs
            elif re.search(r'(?i)opleiding|training|academ|school|instituut|college', emp):
                # 'QTB Opleidingen en Trainingen': kan in het cv een baan zijn en op LinkedIn een opleiding
                # (of andersom). Telt overal op het profiel, niet alleen bij 'werkt bij'.
                terms.append(('organisatie (werk/opleiding)', emp, emp, 'sterk'))
            else:
                terms.append(('werkgever', emp, emp, 'sterk'))
        for st in stages:
            st_org = _clean_org(st)
            if len(st_org) >= 3 and st_org != emp and not any(t[2] == st_org for t in terms) \
                    and not VAGUE_EMPLOYERS.match(st_org):
                terms.append(('stagebedrijf (groot)' if BIG_EMPLOYERS.match(st_org) else 'stagebedrijf',
                              st_org, st_org, 'middel' if BIG_EMPLOYERS.match(st_org) else 'sterk'))
        for bj in _cv_bijbanen(db, person):
            bj_org = _clean_org(bj)
            # Bijbaan: telt mee, maar als middel (veel mensen werken bij dezelfde supermarkt e.d.).
            if len(bj_org) >= 3 and not any(t[2] == bj_org for t in terms):
                terms.append(('bijbaan', bj_org, bj_org, 'middel'))
        if len(normalized_name(role)) >= 5 and not role_is_stage and normalized_name(role) != employer_core:
            role_clean = re.split(r'\s*(?:·|,|\|)\s*', role)[0]
            # Alleen als de functie ook iets specifieks bevat (niet alleen 'medewerker', 'junior' e.d.).
            if role_is_specific(role_clean):
                terms.append(('functie', role_clean, normalized_name(role_clean), 'middel'))
        snippets = ' '.join(_cv_snippets(db, person))
        heading_words = {'vaardigheden','skills','talen','werkervaring','opleiding','opleidingen','profiel','certificaten',
                         'cursussen','hobby','hobbys','interesses','referenties','contact','projecten','stage','en'}
        schools = [m.strip() for m in SCHOOL_RE.findall(snippets) if m.split()[-1] not in heading_words]
        schools += re.findall(r'\b((?:avans|haagse|zuyd|hz|aeres|has|marnix|driestar|iselinge|nhl stenden|christelijke) hogeschool)\b', snippets)
        schools += [x for x in _cv_field(db, person, 'opleidingen').split(' ; ') if x]
        for school in list(dict.fromkeys(schools))[:6]:
            if not any(school in t[2] or t[2] in school for t in terms if t[0] == 'school'):
                terms.append(('school', school, school, 'middel'))
        # Het losse woord 'stage/intern' telt NIET als bewijs: dat staat op heel veel profielen.
        # Alleen het stagebedrijf zelf (hierboven) telt mee.
        places = [pl for pl in NL_PLACES if re.search(r'(?<![a-z])' + re.escape(pl) + r'(?![a-z])',
                                                        snippets + ' ' + normalized_name(stage or ''))]
        name_low = normalized_name(full)
        in_name = lambda pl: re.search(r'(?<![a-z])' + re.escape(pl) + r'(?![a-z])', name_low) is not None
        places = [pl for pl in places if not in_name(pl)]
        home = _cv_field(db, person, 'woonplaats')
        if home and in_name(home):
            home = ''
        if home:
            terms.append(('woonplaats', home, home, 'klein'))
        work_places = [x for x in _cv_field(db, person, 'werkplaatsen').split(' ; ') if x]
        work_places = [wp for wp in work_places if not in_name(wp)]
        for wp in work_places:
            # Telt apart van de woonplaats, maar alleen als het profiel die plaats bij een BAAN noemt.
            terms.append(('werkplaats', wp, wp, 'klein'))
        for pl in sorted(places)[:4]:
            if pl != home and pl not in work_places:
                terms.append(('plaats (werk/school)', pl, pl, 'klein'))
        # Rommel-kenmerken (landen, zinsdelen, 'university of', 'director') tellen nooit als bewijs.
        # Een algemene functie ('Monteur', 'Project Manager') telt alleen als 'zwak': genoeg om 'controleren'
        # te tonen, nooit genoeg voor 'zeker'.
        terms = [t if evidence_term_ok(t[0].replace(' (groot)', ''), t[2]) else (t[0], t[1] + ' (algemene functie)', t[2], 'zwak')
                 for t in terms
                 if evidence_term_ok(t[0].replace(' (groot)', ''), t[2])
                 or (t[0] == 'functie' and any(w.endswith(ROLE_WORDS) for w in t[2].split()))]
        candidates = {}   # (type, waarde) -> info
        have = {'email': bool(person['email']), 'telefoon': bool(person['telefoon']),
                'linkedin': bool(person['linkedin'])}
        confirmed_li = next((k.split('|', 2)[2] for k, v in load_online_decisions(out).items()
                             if v == 'ja' and k.startswith((person.get('persoon_id') or normalized_name(full)) + '|linkedin|')), '')
        if confirmed_li and not have['linkedin']:
            # Door jou bevestigd: niet opnieuw zoeken, wel mail/telefoon op dit profiel zoeken.
            person = dict(person, linkedin=confirmed_li)
            have['linkedin'] = True

        def add(typ, value, url, title, query, fixed=None):
            if have.get(typ) is True:
                return
            info = candidates.setdefault((typ, value), dict(match=set(), url=url, title=title[:150], query=query))
            if fixed:
                info['fixed'] = fixed

        # STAP 1 (gratis): eigen links uit het cv – GitHub-profiel en eigen website/portfolio.
        own_hosts = ('github.io', 'gitlab.io', 'netlify.app', 'vercel.app', 'wixsite.com', 'carrd.co',
                     'about.me', 'webflow.io', 'pages.dev', 'notion.site', 'behance.net', 'dribbble.com')
        sites = list(dict.fromkeys(_cv_sites(db, person)))
        for site in list(sites):
            if 'github.com/' in site or 'gitlab.com/' in site:
                prof = github_profile(site) if 'github.com/' in site else {}
                if prof.get('email'):
                    add('email', prof['email'].lower(), site, 'GitHub-profiel (link uit cv)', 'eigen cv-link',
                        'waarschijnlijk (eigen GitHub uit cv)')
                if prof.get('blog'):
                    blog = prof['blog'] if prof['blog'].startswith('http') else 'https://' + prof['blog']
                    if blog not in sites:
                        sites.append(blog)
                continue
        for site in sites:
            if 'github.com/' in site or 'gitlab.com/' in site:
                continue
            host = urllib.parse.urlparse(site).netloc.lower()
            personal_host = any(h in host for h in own_hosts) or any(w in host.replace('-', '') for w in words if len(w) >= 4)
            emails, phones, text = own_site_contacts(site, words)
            name_on_page = len(words) >= 2 and words[0] in normalized_name(text) and words[-1] in normalized_name(text)
            for e in emails:
                local = normalized_name(e.split('@')[0].replace('.', ' '))
                ok = personal_host or any(w in local for w in words if len(w) >= 3)
                add('email', e, site, 'eigen website (link uit cv)', 'eigen cv-link',
                    'waarschijnlijk (eigen site uit cv)' if ok else 'controleren')
            for ph in phones:
                add('telefoon', ph, site, 'eigen website (link uit cv)', 'eigen cv-link',
                    'waarschijnlijk (eigen site uit cv)' if (personal_host and name_on_page) else 'controleren')
        for (typ, _v), info in candidates.items():
            if info.get('fixed', '').startswith('waarschijnlijk'):
                have[typ] = 'gevonden'

        # STAP 2 (1 credit): LinkedIn op naam + functie. STAP 3 (1 credit): eigen/ZZP-site, GitHub, freelance.
        queries = []
        search_failed = False      # netwerk-/certificaatfout: dan NIET 'niet gevonden' noteren
        if not have['linkedin']:
            # Eerst op naam + werkgever (een specifiek bedrijf geeft echt bewijs op het profiel); bij een groot of
            # vaag bedrijf (Albert Heijn, gemeente, eigen bedrijf) op naam + functie. Lukt het niet, dan zoekt de
            # stap hieronder (orgcheck) nog via Google op werkgever/school.
            specifiek_bedrijf = (len(emp) >= 3 and emp not in NL_PLACES and not VAGUE_EMPLOYERS.match(emp)
                                 and not BIG_EMPLOYERS.match(emp) and employer_core)
            zoekterm = f'"{employer_raw.replace(chr(34), "").strip()[:45]}"' if specifiek_bedrijf and employer_raw.strip() else role
            queries.append(('linkedin', f'"{full}" {zoekterm}'.strip(), ['linkedin.com'], None))
        # Tavily alleen voor LinkedIn. Mail/telefoon gaan via Google (Serper/SerpApi) op het zekere profiel.
        # De oude 'contact website zzp'-zoekopdracht (kostte Tavily-credits, leverde weinig op) alleen als je die aanzet.
        if (not have['email'] or not have['telefoon']) and os.environ.get('CV_ONLINE_CONTACT') == '1':
            queries.append(('contact', f'"{full}" {role} contact website zzp freelance github'.strip(),
                            None, PRIVATE_SOCIAL))
        stop = False
        for kind, query, domains, excluded in queries:
            # Onthouden per persoon (naam), niet per zoektekst: wie al eens is opgezocht,
            # kost nooit meer een credit, ook niet als bv. de werkgever later verandert.
            cache_key = kind + '|' + normalized_name(full) + ('|alleen_naam' if query == f'"{full}"' and kind == 'linkedin' else '')
            old_key = kind + '|' + query
            if cache_key not in cache and old_key in cache:
                cache[cache_key] = cache[old_key]
            if (kind == 'linkedin' and cache_key in cache and role_generic and 'q|' + cache_key not in cache
                    and query != f'"{full}"'):
                # Eerder gezocht met een nietszeggende functie ('Medewerker'): één keer opnieuw op werkgever.
                del cache[cache_key]
            if (False and kind == 'linkedin' and cache_key in cache and (not have['email'] or not have['telefoon'])
                    and not any('raw' in r for r in cache[cache_key])
                    and any(own_linkedin(l, r.get('title', ''), words, full)
                            for r in cache[cache_key] for l in linkedins_in(r.get('url', '')))):
                # Oud resultaat zonder Info-tekst, maar wel een eigen profiel: 1x opnieuw (1 credit).
                del cache[cache_key]
            if cache_key in cache:
                results = cache[cache_key]
                summary_cached = True
            else:
                results = None
                if not tavily_out:
                    try:
                        results = tavily_search(query, key, include_domains=domains, exclude_domains=excluded,
                                                max_results=20 if kind == 'linkedin' else 8,
                                                raw=(kind == 'linkedin'))
                        used += 1
                    except urllib.error.HTTPError as exc:
                        if exc.code in (401, 403):
                            print('ONLINE FOUT: Tavily-sleutel ongeldig of geweigerd. Controleer je sleutel.', flush=True)
                            return False
                        if exc.code in (429, 432, 433):
                            print('ONLINE: Tavily-tegoed op; verder via Google (Serper/SerpApi).', flush=True)
                            tavily_out = True
                        else:
                            print('ONLINE zoekfout:', exc.code, flush=True)
                            search_failed = True
                            continue
                    except (urllib.error.URLError, TimeoutError, ValueError) as exc:
                        print('ONLINE zoekfout:', type(exc).__name__, str(exc)[:120], flush=True)
                        search_failed = True
                        continue
                if results is None:
                    # Zoeken via Google (Serper, daarna SerpApi). Op? Dan als laatste Tavily (als er een sleutel is).
                    res, via = google_search(f'site:linkedin.com/in {query}' if kind == 'linkedin' else query)
                    if res is None and key and bd_aan:
                        try:
                            res = tavily_search(query, key, include_domains=domains, exclude_domains=excluded,
                                                max_results=20 if kind == 'linkedin' else 8, raw=(kind == 'linkedin'))
                            used += 1
                            via = 'Tavily'
                        except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError, ValueError) as exc:
                            print('ONLINE: ook Tavily lukte niet (' + str(exc)[:80] + ').', flush=True)
                            res = None
                    if res is None:
                        print('ONLINE: geen zoektegoed meer (Serper, SerpApi en Tavily); rest volgende keer.', flush=True)
                        stop = True
                        break
                    if via != 'Tavily':
                        google_used += 1
                        google_via[via] = google_via.get(via, 0) + 1
                    results = res
                cache['q|' + cache_key] = query
                cache[cache_key] = [dict({k: r.get(k, '') for k in ('url', 'title', 'content')},
                                         **({'raw': own_profile_text(r.get('raw_content'))}
                                            if kind == 'linkedin' and r.get('raw_content') else {}))
                                    for r in results]
                time.sleep(delay)
            for result in results:
                url = result.get('url', '')
                title = html.unescape(re.sub(r'<[^>]+>', ' ', result.get('title', '') or ''))
                content = result.get('content', '') or ''
                if bd_aan and kind == 'linkedin':
                    # Volledig openbaar profiel van Apify (werkgevers, functies, jaren, opleidingen, about).
                    ptekst = profielen.tekst_van(cache, profielen.slug(url))
                    if ptekst:
                        content = content + '\n' + ptekst
                if result.get('raw'):
                    content = content + '\n' + own_profile_text(result['raw'])
                context = normalized_name(title + ' ' + content + ' ' + url.replace('-', ' '))
                if len(words) >= 2 and not (words[0] in context and words[-1] in context):
                    continue
                match = sorted(_words(title + ' ' + content) & cv_words)
                blob = normalized_name(title + ' ' + content)
                # Engelse/officiële plaatsnamen op LinkedIn gelijkstellen aan de Nederlandse.
                for en, nl in (('the hague', 'den haag'), ('s gravenhage', 'den haag'), ('hague', 'den haag'),
                               ('the netherlands', 'nederland')):
                    blob = re.sub(r'(?<![a-z])' + en + r'(?![a-z])', nl, blob)
                hits = set()
                raw_low = (title + ' ' + content).lower()
                # Plaatsnamen die alleen in een gevonden schoolnaam staan tellen niet dubbel
                # (bv. 'utrecht' in 'hogeschool utrecht').
                blob_places = blob
                for label, shown, phrase, weight in terms:
                    if label == 'school':
                        blob_places = re.sub(r'(?<![a-z])' + re.escape(phrase) + r'(?![a-z])', ' ', blob_places)
                job_lines = []
                for ln in (title + '\n' + content).splitlines():
                    if re.search(r'(?i)\bat\b|\bbij\b|\b(19|20)\d{2}\b|present|heden', ln) and not re.search(r'(?i)location|locatie', ln):
                        jl = normalized_name(ln)
                        for en, nl in (('the hague', 'den haag'), ('s gravenhage', 'den haag'), ('hague', 'den haag')):
                            jl = re.sub(r'(?<![a-z])' + en + r'(?![a-z])', nl, jl)
                        job_lines.append(jl)
                for label, shown, phrase, weight in terms:
                    pat = r'(?<![a-z])' + re.escape(phrase) + r'(?![a-z])'
                    if phrase == '__stagiair__':
                        sm = re.search(r'(?<![a-z])(stagiair|stagiaire|intern|internship|stage)(?![a-z])', blob)
                        ok = bool(sm)
                        if ok:
                            shown = f'(cv: stage; profiel: "{sm.group(1)}")'
                    elif label == 'werkplaats':
                        ok = any(re.search(pat, jl) for jl in job_lines)
                    elif label.startswith(('plaats', 'woonplaats')):
                        ok = re.search(pat, blob_places) is not None   # plaatsen: exact
                    else:
                        found_as = phrase if re.search(pat, blob) else fuzzy_find(phrase, blob)
                        ok = bool(found_as)
                        if ok and found_as != phrase:
                            shown = shown + f' (als "{found_as}")'
                            phrase = found_as
                    if ok and label in ('werkgever', 'stagebedrijf'):
                        # Alleen sterk als het echt als bedrijf staat ('at X', 'bij X', '- X'),
                        # niet als losse woorden in een functietitel.
                        as_org = re.search(r'(?:\bat|\bbij|@|\s[-–|·])\s+' + re.escape(phrase).replace(r'\ ', r'[\s.,&-]+') + r'(?![a-z])',
                                           raw_low)
                        if not as_org:
                            hits.add((label, shown + ' (in functietitel, niet als bedrijf)', 'middel'))
                            match = [m for m in match if m not in phrase.split()]
                            continue
                    if ok:
                        hits.add((label, shown, weight))
                        match = [m for m in match if m not in phrase.split()]
                if kind == 'contact' and not any(sk in url for sk in PRIVATE_SOCIAL):
                    host = urllib.parse.urlparse(url).netloc.lower()
                    if len(words) >= 2 and words[-1] in host.replace('-', ''):
                        # Lijkt een eigen (ZZP-)site: contactpagina gratis uitlezen.
                        emails, phones, text = own_site_contacts(url, words)
                        on_page = words[0] in normalized_name(text) and words[-1] in normalized_name(text)
                        for e in emails:
                            add('email', e, url, title, query, 'waarschijnlijk (eigen site)' if on_page else 'controleren')
                        for ph in phones:
                            add('telefoon', ph, url, title, query, 'waarschijnlijk (eigen site)' if on_page else 'controleren')
                found = []
                if kind == 'linkedin' and not have['linkedin']:
                    # Alleen profielen VAN deze persoon: volledige naam in het adres of vooraan in de titel.
                    full_norm = normalized_name(full)
                    title_norm = normalized_name(title)
                    for link in linkedins_in(url):
                        if own_linkedin(link, title, words, full):
                            found.append(('linkedin', link))
                    # Gratis meepakken: e-mail/telefoon uit de Info-tekst, alleen van een eigen profiel.
                    if found and not have['email']:
                        found += [('email', e) for e in emails_in(ontsluier_email(title + '\n' + content)) if not GENERIC_EMAIL.match(e)]
                    if found and not have['telefoon']:
                        found += [('telefoon', t) for t in phones_in(title + '\n' + content)]
                if kind == 'contact':
                    if not have['email']:
                        found += [('email', e) for e in emails_in(ontsluier_email(title + '\n' + content)) if not GENERIC_EMAIL.match(e)]
                    if not have['telefoon']:
                        found += [('telefoon', t) for t in phones_in(title + '\n' + content)]
                for typ, value in found:
                    info = candidates.setdefault((typ, value), dict(match=set(), hits=set(), url=url, title=title[:150], query=query))
                    if typ == 'linkedin' and len(info.get('tekst', '')) < 2500:
                        info['tekst'] = (info.get('tekst', '') + '\n' + title + '\n' + content[:1500]).strip()
                    info['match'] |= set(match)
                    info['hits'] |= hits
                    if kind == 'linkedin' and typ in ('email', 'telefoon') and found[0][0] == 'linkedin':
                        info.setdefault('from_linkedin', found[0][1])
                    if typ == 'linkedin':
                        slug = normalized_name(value.rsplit('/', 1)[-1].replace('-', ' '))
                        info['name_in_url'] = len(words) >= 2 and words[0] in slug and words[-1] in slug
                    if typ == 'email':
                        local = normalized_name(value.split('@')[0].replace('.', ' ').replace('_', ' '))
                        info['name_in_url'] = bool(words) and words[-1] in local
            if (kind == 'linkedin' and query != f'"{full}"' and not have['linkedin']
                    and not any(t == 'linkedin' for (t, _v) in candidates)):
                # Met functie erbij niets gevonden: nog één keer alleen op de naam zoeken (1 credit).
                # Zo komen ook profielen boven met een andere functie dan in het cv (als 'controleren').
                queries.append(('linkedin', f'"{full}"', ['linkedin.com'], None))
        # Tavily ziet vaak maar een afgekapt stuk van een profiel ("Stagiair bij ..."). Staat een
        # specifiek bedrijf uit het cv nog op GEEN enkel gevonden profiel, controleer dat dan via
        # Google (Serper/SerpApi): site:linkedin.com/in "naam" "bedrijf". Alleen een treffer op
        # precies een van de gevonden profielen telt (max 2 zoekopdrachten per persoon, daarna cache).
        li_opts = [(v, i) for (t, v), i in candidates.items() if t == 'linkedin']
        if not have['linkedin'] and not confirmed_li:
            slugs = {linkedin_slug(v): i for v, i in li_opts}
            raw_orgs = ' '.join(str(person.get(k) or '') for k in ('laatst_bekende_werkgever', 'huidige_functie')) + ' ' + str(stage or '')
            for lbl, shown, phrase, wgt in [t for t in terms if t[3] == 'sterk'][:2]:
                if any(x[0] == lbl and x[2] == 'sterk' for _v, i in li_opts for x in i['hits']):
                    continue
                # Korte vorm: eerste woord als dat een afkorting in hoofdletters is (QTB, KPN, ABN)
                # of een lang eigen woord (Datafabriek). Bedrijven noemen zich op LinkedIn vaak zo.
                first = phrase.split()[0] if phrase.split() else ''
                acro = re.search(r'(?<![A-Za-z])' + re.escape(first.upper()) + r'(?![A-Za-z])', raw_orgs) is not None
                # Een lang woord telt alleen als het in het cv met een hoofdletter staat (eigen naam als
                # 'Datafabriek'), niet een gewoon woord als 'ontwikkelen' of 'optimization'.
                proper = re.search(r'(?<![A-Za-z])' + re.escape(first[:1].upper() + first[1:]) + r'(?![a-z])', raw_orgs) is not None
                short = first if len(phrase.split()) > 1 and ((acro and len(first) >= 2) or (len(first) >= 6 and proper)) \
                    and evidence_term_ok(lbl, first) else ''
                variants = [phrase] + ([short] if short else [])
                gk = f'orgcheck|{normalized_name(full)}|{phrase}' + ('|kort' if short else '')
                if gk not in cache:
                    q = ' OR '.join(f'"{v}"' for v in variants)
                    res, via = google_search(f'site:linkedin.com/in "{full}" ({q})')
                    if res is None:
                        break
                    cache[gk] = res
                    google_used += 1
                    google_via[via] = google_via.get(via, 0) + 1
                for r in cache.get(gk) or []:
                    info = slugs.get(linkedin_slug(r.get('url', '')))
                    if info is None:
                        # Google vond een profiel dat Tavily miste: alleen meenemen als het echt
                        # van iemand met precies deze naam is (naam in titel/adres).
                        links = [l for l in linkedins_in(r.get('url', '')) if own_linkedin(l, r.get('title', ''), words, full)]
                        if not links:
                            continue
                        info = candidates.setdefault(('linkedin', links[0]), dict(match=set(), hits=set(), url=r.get('url', ''),
                                                      title=(r.get('title') or '')[:150], query='Google: naam + bedrijf'))
                        slugs[linkedin_slug(links[0])] = info
                        li_opts.append((links[0], info))
                    if len(info.get('tekst', '')) < 2500:
                        info['tekst'] = (info.get('tekst', '') + '\n' + (r.get('title') or '') + '\n' + (r.get('content') or '')[:800]).strip()
                    txt = normalized_name((r.get('title') or '') + ' ' + (r.get('content') or ''))
                    if re.search(r'(?<![a-z])' + re.escape(phrase) + r'(?![a-z])', txt) or fuzzy_find(phrase, txt):
                        info['hits'].add((lbl, shown + ' (via Google)', 'sterk'))
                    elif short and re.search(r'(?<![a-z])' + re.escape(short) + r'(?![a-z])', txt):
                        info['hits'].add((lbl, f'{shown} (als "{short}", via Google)', 'sterk'))
        # Nog geen enkel profiel met een sterk bewijs? Dan één extra Google-zoekopdracht (1 credit)
        # met school, grote werkgever, specifieke functie en woonplaats tegelijk:
        # site:linkedin.com/in "naam" ("erasmus universiteit" OR "coolblue" OR "rotterdam").
        # Alleen een treffer op een van de al gevonden profielen telt, met het gewone gewicht.
        li_opts = [(v, i) for (t, v), i in candidates.items() if t == 'linkedin']
        if li_opts and not have['linkedin'] and not any(x[2] == 'sterk' for _v, i in li_opts for x in i['hits']):
            slugs = {linkedin_slug(v): i for v, i in li_opts}
            extra = [t for t in terms if t[3] == 'middel' and t[2] != '__stagiair__'][:3]
            extra += [t for t in terms if t[0] == 'woonplaats'][:1]
            extra = [t for t in extra if not any(x[0] == t[0] and x[1].startswith(t[1])
                                                  for _v, i in li_opts for x in i['hits'])]
            if extra:
                gk = f'extracheck|{normalized_name(full)}|' + '|'.join(t[2] for t in extra)
                if gk not in cache:
                    ors = ' OR '.join(f'"{t[1]}"' for t in extra)
                    res, via = google_search(f'site:linkedin.com/in "{full}" ({ors})')
                    if res is not None:
                        cache[gk] = res
                        google_used += 1
                        google_via[via] = google_via.get(via, 0) + 1
                for r in cache.get(gk) or []:
                    info = slugs.get(linkedin_slug(r.get('url', '')))
                    if info is None:
                        continue
                    txt = normalized_name((r.get('title') or '') + ' ' + (r.get('content') or ''))
                    txt = re.sub(r'(?<![a-z])(?:the hague|s gravenhage)(?![a-z])', 'den haag', txt)
                    txt_places = txt
                    for t in terms:      # 'rotterdam' in 'erasmus universiteit rotterdam' is geen woonplaats
                        if t[0] == 'school':
                            txt_places = re.sub(r'(?<![a-z])' + re.escape(t[2]) + r'(?![a-z])', ' ', txt_places)
                    for lbl, shown, phrase, wgt in extra:
                        hay = txt_places if lbl == 'woonplaats' else txt
                        if re.search(r'(?<![a-z])' + re.escape(phrase) + r'(?![a-z])', hay) or \
                                (lbl != 'woonplaats' and fuzzy_find(phrase, hay)):
                            info['hits'].add((lbl, shown + ' (via Google)', wgt))
        # AI-controle (lokaal): bij elk gevonden LinkedIn-profiel waarop iets uit het cv klopt, vragen of het
        # dezelfde persoon is. 'nee' = naamgenoot -> profiel valt af. 'ja'/'onzeker' tellen mee in de beslissing.
        ai_rejected = []
        if not have['linkedin'] and not confirmed_li:
            try:
                import ai_rechter
                ai_on = ai_rechter.available()
            except Exception:
                ai_on = False
            if ai_on:
                cv_text = _ai_cv_text(db, person)
                li_opts = sorted([(v, i) for (t, v), i in candidates.items() if t == 'linkedin' and i.get('hits')],
                                 key=lambda o: -len(o[1]['hits']))[:4]
                for value, info in li_opts:
                    ak = f"ai|{ai_rechter.MODEL_ID}|{normalized_name(full)}|{linkedin_slug(value)}|" + \
                         hashlib.sha1((cv_text + info.get('tekst', '')).encode('utf-8', 'replace')).hexdigest()[:12]
                    res = cache.get(ak)
                    if res is None:
                        res = ai_rechter.judge(cv_text, info.get('tekst') or info.get('title', ''))
                        if res is not None and not res.get('fout'):
                            cache[ak] = res
                    if not res:
                        continue
                    info['ai'] = res
                    if res['oordeel'] == 'nee':
                        ai_rejected.append((value, res))
                        candidates.pop(('linkedin', value), None)
                if ai_rejected:
                    print(f'AI-CONTROLE: {full} – {len(ai_rejected)} naamgeno(o)t(en) afgewezen', flush=True)
        # Per type de beste kandidaat kiezen en eerlijk labelen.
        summary = []
        zeker_link = None
        namesakes_only = 0    # alleen profielen met dezelfde naam, maar niets uit het cv klopt
        if have['linkedin'] is True and person.get('linkedin'):
            # LinkedIn staat al in het cv: dat profiel is zeker van deze persoon -> daar mail/telefoon zoeken.
            zeker_link = linkedins_in(person['linkedin'])[0] if linkedins_in(person['linkedin']) else None
        extracted = False
        searched = set()      # waar ECHT online naar gezocht is (Google op het zekere profiel)
        for typ in ('linkedin', 'email', 'telefoon'):
            if typ != 'linkedin' and zeker_link and not extracted and (not have['email'] or not have['telefoon']):
                # LinkedIn klopt zeker maar mail/telefoon ontbreken: Google X-ray op dat profiel (Serper).
                # Google toont in de zoektekst het stuk Info-tekst rond de zoekwoorden.
                extracted = True
                skey = os.environ.get('SERPER_API_KEY', '').strip()
                slug = linkedin_slug(zeker_link)
                # a) Tavily Extract ADVANCED direct op de bekende profiel-URL (niet opnieuw zoeken).
                #    Kost alleen iets als het lukt (5 gelukte pagina's = 2 credits).
                ak = f'extadv|{slug}'
                if ak not in cache and os.environ.get('CV_TAVILY_EXTRACT') == '1':
                    # Uit: Tavily geeft bij LinkedIn een oude kopie zonder Info-tekst (getest bij Jan).
                    url_www = 'https://www.linkedin.com/in/' + slug
                    try:
                        page, fail = tavily_extract(url_www, key, depth='advanced')
                        cache[ak] = {'text': own_profile_text(page), 'fout': fail}
                        if page:
                            print(f'ONLINE: LinkedIn-profiel van {full} direct gelezen (Tavily Extract advanced).', flush=True)
                        else:
                            print(f'ONLINE: Tavily kon het profiel van {full} niet lezen: {fail or "geen inhoud"} (gratis).', flush=True)
                    except urllib.error.HTTPError as exc:
                        print('ONLINE: profiel lezen lukte niet (Tavily-fout', exc.code, ')', flush=True)
                    except (urllib.error.URLError, TimeoutError, ValueError) as exc:
                        print('ONLINE: profiel lezen lukte niet:', str(exc)[:100], flush=True)
                page = own_profile_text((cache.get(ak) or {}).get('text', '')) if isinstance(cache.get(ak), dict) else ''
                pn = normalized_name(page)
                if page and len(words) >= 2 and words[0] in pn and words[-1] in pn:
                    text = fix_nl_mobile(ontsluier_email(page))
                    mails = [e for e in emails_in(text) if not GENERIC_EMAIL.match(e)]
                    named = [e for e in mails if any(w in normalized_name(e.split('@')[0].replace('.', ' ').replace('_', ' '))
                                                     for w in words)]
                    for e in (named or mails[:1]):
                        if not have['email'] and not GENERIC_EMAIL.match(e):
                            candidates.setdefault(('email', e), dict(match=set(), hits=set(), url=zeker_link,
                                                  title='LinkedIn-profiel (Tavily Extract advanced)', query='extract'))['from_linkedin'] = zeker_link
                    for t in phones_in(text):
                        if not have['telefoon']:
                            candidates.setdefault(('telefoon', t), dict(match=set(), hits=set(), url=zeker_link,
                                                  title='LinkedIn-profiel (Tavily Extract advanced)', query='extract'))['from_linkedin'] = zeker_link
                if any(t == 'telefoon' for (t, _v) in candidates):
                    have_tel = True
                else:
                    have_tel = False
                xqueries = [('tel', f'site:linkedin.com/in "{full}" ("+31 6" OR "06" OR "+316")'),
                            ('mail', f'site:linkedin.com/in "{full}" ("mail" OR "@" OR "e-mail")')]
                if bd_aan:
                    # Eerst gratis: e-mail/telefoon die de kandidaat zelf in zijn profiel (about/info) heeft gezet,
                    # uit het Apify-profiel. Alleen wat dan nog ontbreekt: Google X-ray (Serper/SerpApi) hieronder.
                    g = profielen.gegevens_van(cache, profielen.slug(zeker_link))
                    eigen_tekst = fix_nl_mobile(ontsluier_email('\n'.join(
                        [g.get('kop', ''), g.get('over', ''), g.get('email', ''), g.get('telefoon', '')])))
                    mails = [e for e in emails_in(eigen_tekst) if not GENERIC_EMAIL.match(e)]
                    named = [e for e in mails if any(w in normalized_name(e.split('@')[0].replace('.', ' ').replace('_', ' '))
                                                     for w in words)]
                    for e in (named or mails)[:1]:
                        if not have['email']:
                            candidates.setdefault(('email', e), dict(match=set(), hits=set(), url=zeker_link,
                                                  title='LinkedIn-profiel (about)', query='profiel'))['from_linkedin'] = zeker_link
                    for t in phones_in(eigen_tekst)[:1]:
                        if not have['telefoon']:
                            candidates.setdefault(('telefoon', t), dict(match=set(), hits=set(), url=zeker_link,
                                                  title='LinkedIn-profiel (about)', query='profiel'))['from_linkedin'] = zeker_link
                            have_tel = True
                for tag, xq in xqueries:
                    if tag == 'tel' and (have['telefoon'] or have_tel):
                        continue
                    if tag == 'mail' and (have['email'] or any(t == 'email' for (t, _v) in candidates)):
                        continue
                    xk = f'xray|{slug}|{tag}'
                    if xk not in cache:
                        res, via = google_search(xq)
                        if res is None:
                            break
                        cache[xk] = res
                        google_used += 1
                        google_via[via] = google_via.get(via, 0) + 1
                    searched.add('telefoon' if tag == 'tel' else 'email')
                    for r in cache.get(xk) or []:
                        if linkedin_slug(r.get('url', '')) != slug:
                            continue          # alleen het profiel dat zeker klopt
                        text = fix_nl_mobile(ontsluier_email((r.get('title') or '') + '\n' + (r.get('content') or '')))
                        for e in emails_in(text):
                            if not have['email'] and not GENERIC_EMAIL.match(e):
                                candidates.setdefault(('email', e), dict(match=set(), hits=set(), url=zeker_link,
                                                      title='Google-zoektekst van het LinkedIn-profiel', query=xq))['from_linkedin'] = zeker_link
                        for t in phones_in(text):
                            if not have['telefoon']:
                                candidates.setdefault(('telefoon', t), dict(match=set(), hits=set(), url=zeker_link,
                                                      title='Google-zoektekst van het LinkedIn-profiel', query=xq))['from_linkedin'] = zeker_link
            options = [(v, info) for (t, v), info in candidates.items() if t == typ]
            if not options:
                continue
            def weight(info):
                # Elk kenmerk telt één keer (het sterkste): 'director' als bedrijf én in de functietitel is
                # 1 ding, en 'woonplaats amsterdam' + 'werkplaats amsterdam' is 1 plaats, niet 2.
                rank = {'sterk': 3, 'middel': 2, 'klein': 1}
                best = {}
                for _lbl, shown, w in info.get('hits', set()):
                    base = normalized_name(shown.split(' (')[0])
                    key = ('plaats', base) if w == 'klein' else ('ding', base)
                    if rank.get(w, 0) > rank.get(best.get(key), 0):
                        best[key] = w
                vals = list(best.values())
                # Losse 'cv-woorden' (zoals 'toepassing') tellen NIET: te toevallig.
                return vals.count('sterk'), vals.count('middel'), vals.count('klein')
            def score_of(info):
                st, me, sm = weight(info)
                weak = sum(1 for x in info.get('hits', set()) if x[2] == 'zwak')
                return (st + me + sm, st, me, weak)     # meeste overeenkomsten eerst; bij gelijk aantal: sterkste
            options.sort(key=lambda o: (str(o[1].get('fixed', '')).startswith('waarschijnlijk'),
                                        score_of(o[1])), reverse=True)
            if typ == 'linkedin' and not options[0][1].get('fixed') and not options[0][1].get('hits'):
                # Alleen de naam komt overeen, verder niets: geen LinkedIn invullen en ook niet
                # 'controleren' noemen. Het wordt 'niet gevonden' met uitleg.
                namesakes_only = len(options)
                continue
            best_n = len(options[0][1]['match'])
            for rank, (value, info) in enumerate(options):
                n = len(info['match'])
                if info.get('fixed') and info['fixed'].startswith('waarschijnlijk (eigen') and 'cv' in info['fixed']:
                    sure = info['fixed']          # door de kandidaat zelf in het cv gezet: betrouwbaar
                elif namesake:
                    sure = 'naamgenoot - controleren'
                elif info.get('fixed'):
                    sure = info['fixed']
                elif typ != 'linkedin' and zeker_link and info.get('from_linkedin') == zeker_link:
                    sure = 'waarschijnlijk (Info-tekst LinkedIn)'   # staat op het profiel dat zeker klopt
                elif typ == 'linkedin' and info.get('ai'):
                    # Met AI-controle: 'ja' + echt bewijs = zeker. Bij een groot bedrijf/school moeten er 2 dingen
                    # kloppen. 'onzeker': alleen zeker met een specifiek (sterk) bewijs volgens de regels.
                    st, me, sm = weight(info)
                    ai_o = info['ai']['oordeel']
                    beats = len(options) == 1 or score_of(info) > score_of(options[1][1]) or \
                        options[1][1].get('ai', {}).get('oordeel') != 'ja'
                    # 0 fouten: 'zeker' alleen met minstens één SPECIFIEK bewijs (eigen werkgever/stagebedrijf
                    # als bedrijf, specifieke school/functie). Groot bedrijf + plaats is nooit genoeg, ook niet met AI-ja.
                    ok = rank == 0 and beats and st >= 1 and (
                        (ai_o == 'ja') or
                        (ai_o == 'onzeker' and st >= 1 and ((sum(weight(info)) >= 2) or len(options) == 1)))
                    sure = 'waarschijnlijk' if ok else 'controleren'
                elif (typ == 'linkedin' and rank == 0 and weight(info)[0] >= 1      # altijd een specifiek bewijs
                      and ((weight(info)[0] >= 1 and sum(weight(info)) >= 2) or weight(info)[1] >= 3
                           or (weight(info)[1] >= 2 and weight(info)[2] >= 1)
                           # Maar één profiel met deze naam gevonden én daarop klopt een echt kenmerk
                           # (werkgever, school, functie of bijbaan; een plaats alleen is te weinig).
                           or (len(options) == 1 and weight(info)[0] + weight(info)[1] >= 1)
                           # Enige profiel en minstens 2 verschillende plaatsen kloppen
                           # (bv. woonplaats Amsterdam + werk/school Rotterdam): ook genoeg.
                           or (len(options) == 1 and weight(info)[2] >= 2)
                           # Enige profiel met een STERK bewijs (specifieke werkgever/stagebedrijf als bedrijf),
                           # en geen enkel ander profiel met dezelfde naam heeft ook maar iets gemeen.
                           or (weight(info)[0] >= 1 and all(sum(weight(o[1])) == 0 for o in options[1:])))
                      # Een ander profiel met dezelfde naam mag niet even goed passen:
                      # 3 tegen 2 overeenkomsten -> de 3 wint; 2 tegen 2 (even sterk) -> controleren.
                      and (len(options) == 1 or score_of(info) > score_of(options[1][1]))):
                    # Zeker: een uniek kenmerk (werkgever of stagebedrijf) + nog iets dat klopt
                    # (functie, school, plaats), en geen ander profiel dat ook zo goed past.
                    sure = 'waarschijnlijk'
                else:
                    sure = 'controleren'
                if typ == 'linkedin' and rank == 0 and sure == 'waarschijnlijk':
                    zeker_link = value
                if sure == 'waarschijnlijk (Info-tekst LinkedIn)':
                    reden = f'staat in de Info-tekst van het LinkedIn-profiel dat zeker klopt ({zeker_link[:60]})'
                elif 'cv' in str(info.get('fixed', '')):
                    reden = f'staat op een link die de kandidaat zelf in het cv zette ({info["url"][:60]})'
                elif str(info.get('fixed', '')).startswith('waarschijnlijk'):
                    reden = f'eigen website met de volledige naam op de pagina ({info["url"][:60]})'
                elif n:
                    parts = []
                    if typ == 'linkedin' and info.get('name_in_url'):
                        parts.append('naam in LinkedIn-adres')
                    phrases = [m for m in info['match'] if ': ' in m]
                    loose = [m for m in info['match'] if ': ' not in m]
                    parts += [m.replace(': ', ' "', 1) + '"' for m in sorted(phrases)]
                    if loose:
                        parts.append('cv-woorden: ' + ', '.join(sorted(loose)[:6]))
                    reden = 'komt overeen: ' + '; '.join(parts)
                else:
                    reden = 'alleen de naam komt overeen (kan een naamgenoot zijn)'
                h = info.get('hits', set())
                ticks = [f'✓ {lbl} {val}' for lbl, val, _w in sorted(h, key=lambda x: ('sterk', 'middel', 'klein', 'zwak').index(x[2]))]
                missing = [f'✗ {lbl} {val}' for lbl, val, _p, wgt in terms
                           if wgt != 'klein' and not any(x[0] == lbl and x[1].startswith(val) for x in h)]
                if typ == 'linkedin' and not info.get('fixed'):
                    reden = ' | '.join(ticks) or 'geen kenmerken uit het cv gevonden'
                    ai = info.get('ai')
                    if ai and ai['oordeel'] == 'ja':
                        reden += ' | ✓ AI-controle: zelfde persoon' + (f" ({'; '.join(ai['past'][:2])})" if ai['past'] else '')
                    elif ai:
                        reden += ' – AI-controle twijfelt' + (f": {'; '.join(ai['botst'][:2])}" if ai['botst'] else
                                                              ' (te weinig informatie op het profiel)')
                if namesake:
                    reden += ' – LET OP: er staan meerdere personen met deze naam in je cv-lijst'
                elif len(options) > 1 and rank == 0:
                    reden += f' – {len(options) - 1} ander(e) profiel(en) met deze naam gevonden'
                elif len(options) == 1 and typ == 'linkedin' and h:
                    reden += ' – enige LinkedIn-profiel met deze naam gevonden'
                collected.append(dict(persoon_id=person['persoon_id'], naam=full, type=typ, gevonden_waarde=value,
                    zekerheid=sure, overeenkomst=reden, bron_url=info['url'], brontitel=info['title'],
                    gevonden_op=dt.date.today().isoformat(), status='SUGGESTIE - HANDMATIG CONTROLEREN',
                    zoekopdracht=info['query'], keuze='beste' if rank == 0 else 'alternatief'))
            best_sure = collected[-len(options)]['zekerheid']
            summary.append(f'{typ}: {len(options)} gevonden, beste = {best_sure}' +
                           (f' ({best_n} overeenkomsten met cv)' if best_n else ' (geen overeenkomst met cv)'))
        for typ in ('email', 'telefoon'):
            if typ in searched and not have[typ] and not any(t == typ for (t, _v) in candidates):
                collected.append(dict(persoon_id=person['persoon_id'], naam=full, type=typ, gevonden_waarde='',
                    zekerheid='niet gevonden', overeenkomst='gezocht via Google op het LinkedIn-profiel, niets gevonden',
                    bron_url='', brontitel='', gevonden_op=dt.date.today().isoformat(), status='', zoekopdracht='',
                    keuze='niets'))
        if not have['linkedin'] and not search_failed and not any(
                c2['persoon_id'] == person['persoon_id'] and c2['type'] == 'linkedin' for c2 in collected):
            # Wel gezocht, geen profiel met precies deze naam: vastleggen zodat de Excel dat zegt.
            collected.append(dict(persoon_id=person['persoon_id'], naam=full, type='linkedin', gevonden_waarde='',
                zekerheid='niet gevonden',
                overeenkomst=(f'{len(ai_rejected)} profiel(en) met dezelfde naam gevonden, maar volgens de AI-controle '
                              'een andere persoon: ' + '; '.join(b for _v, r in ai_rejected for b in r.get('botst', [])[:1])[:200])
                             if ai_rejected else
                             (f'{namesakes_only} profiel(en) met dezelfde naam gevonden, maar niets uit het cv klopt '
                              '(geen werkgever, school, functie of plaats)') if namesakes_only else
                             'geen LinkedIn-profiel met precies deze naam gevonden',
                bron_url='', brontitel='', gevonden_op=dt.date.today().isoformat(), status='', zoekopdracht='',
                keuze='niets'))
        if summary:
            print(f'ONLINE GEVONDEN: {full} – ' + '; '.join(summary), flush=True)
        elif namesakes_only:
            print(f'ONLINE: {full} – {namesakes_only} profiel(en) met dezelfde naam, maar niets klopt: niet ingevuld', flush=True)
        elif search_failed:
            print(f'ONLINE: {full} – zoeken MISLUKT (geen verbinding met de zoekdienst); probeer later opnieuw', flush=True)
        else:
            print(f'ONLINE: {full} – niets gevonden', flush=True)
        if stop:
            break
    try:
        cache_path.write_text(json.dumps(cache, ensure_ascii=False), encoding='utf-8')
    except OSError:
        pass
    save_csv(out / 'online_suggesties.csv', collected, ONLINE_COLS)
    print(f'ONLINE KLAAR: {checked} personen opgezocht, {used} nieuwe zoekopdrachten (credits) gebruikt'
          + (f', Google-zoekopdrachten: ' + ', '.join(f'{n} via {k}' for k, n in google_via.items()) if google_used else '') + '. '
          'Zie in Excel de kolommen *_bron; twijfelgevallen via ‘Online controleren’.', flush=True)
    return True


def ai_naam_bevestigd(ai_naam, doc):
    """AI is alleen een aanwijzing. Accepteer een persoonsnaam uitsluitend
    als die letterlijk als naam in het cv terug te vinden is, met onafhankelijk
    aanvullend bewijs uit kop/naamveld, eigen e-mail of eigen BESTANDSNAAM.
    Niets uit een bovenliggende map wordt ooit gebruikt als persoonsnaam.
    """
    # Gebruik hoofdletters zoals bij gewone naamherkenning, niet 'Jasmijn koster'.
    naam = tidy_name(clean_person_name(tidy_name(ai_naam or '')))
    if not naam or not reasonable_name(naam):
        return ''
    snippet = ' '.join((doc['snippet'] or '').lower().split())
    tokens = snippet.split()
    norm = normalized_name(naam)
    compleet = ' ' + snippet + ' '
    expliciet = f' {norm} ' in compleet or f' {"".join(norm.split())} ' in compleet
    if not expliciet:
        return ''            # AI mag geen naam verzinnen
    kop = ' ' + ' '.join(tokens[:120]) + ' '
    in_kop = f' {norm} ' in kop or f' {"".join(norm.split())} ' in kop
    met_label = bool(re.search(
        r'\b(?:naam|name|fullname|volledige naam|kandidaat|candidate|cv van|'
        r'curriculum vitae|voornaam)\s+' + re.escape(norm) + r'\b',
        snippet[:3500]))
    bestandsnaam = naam_uit_bestandsnaam(doc['path'], '', zonder_tekst=True)
    # 'CV_Jeroen_Bos.docx' mag geen AI-naam 'Jasmijn Koster' krijgen,
    # ook al staat die naam toevallig elders in de uitgelezen tekst.
    if bestandsnaam and normalized_name(bestandsnaam) != norm:
        return ''
    bestand_klopt = normalized_name(bestandsnaam) == norm
    try:
        e_mails = json.loads(doc['emails'] or '[]')
    except (ValueError, TypeError):
        e_mails = []
    eigen_mail = any(email_matches_name(naam, mail) for mail in e_mails
                    if not GENERIC_EMAIL.match(mail))
    return naam if bestand_klopt or met_label or (in_kop and eigen_mail) else ''


def ai_twijfel(db, out):
    """Lokale AI helpt bij twijfelgevallen: cv of geen cv (en welke naam). Jouw eigen keuzes in het
    twijfel-venster gaan altijd voor. Resultaten worden bewaard, dus stoppen en later verder kan."""
    try:
        import ai_rechter
    except Exception:
        return
    if not ai_rechter.available() or not ai_rechter.TWIJFEL_AAN:
        return
    try:
        decided = set(json.loads((out / DECISIONS_FILE).read_text(encoding='utf-8')))
    except (OSError, ValueError):
        decided = set()
    cache_path = out / 'ai_twijfel.json'
    try:
        cache = json.loads(cache_path.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        cache = {}
    # Pas na alle gewone herkenningsregels: review en al herkende cv-documenten
    # waarvan de persoonsnaam nog ontbreekt (nooit bij reeds complete cv's).
    rows = db.execute("SELECT path, sha, name, name_verified, status, snippet, reason, emails, phones, links, leverancier FROM docs "
                      "WHERE (status='review' OR (status='cv' AND (name='' OR name_verified=0))) "
                      "AND classifier_version=?", (CLASSIFIER_VERSION,)).fetchall()
    todo = [r for r in rows if r['sha'] not in decided and (r['snippet'] or '').strip()
            and not (r['reason'] or '').startswith('controleren:')]
    if not todo:
        return
    print(f'AI-TWIJFEL: {len(todo)} onvolledige cv-/twijfelbestanden controleren met lokale AI (stoppen mag, '
          'de volgende keer gaat hij verder waar hij was)…', flush=True)
    counts = collections.Counter()
    seen = {}
    for i, r in enumerate(todo, 1):
        key = f"{ai_rechter.MODEL_ID}|{r['sha']}"
        res = cache.get(key) or seen.get(r['sha'])
        if res is None:
            res = ai_rechter.classify(r['snippet'])
            if res is None:
                break
            if not res.get('fout'):
                cache[key] = res
        seen[r['sha']] = res
        reden = (res.get('reden') or '')[:100]
        emails = [e for e in json.loads(r['emails'] or '[]') if not GENERIC_EMAIL.match(e)]
        contact = bool(emails or json.loads(r['phones'] or '[]') or json.loads(r['links'] or '[]'))
        rs = (r['reason'] or '').lower()
        m = re.search(r'werkperiodes: (\d+)', rs)
        cv_kenmerk = ('werkervaring' in rs.split(';')[0] or 'opleiding' in rs.split(';')[0]
                      or 'cv-kop: ja' in rs or (m and int(m.group(1)) > 0))
        # Een door de regels al herkend cv blijft een cv. AI mag alleen het
        # LEGE NAAMVELD aanvullen, nooit zo'n cv alsnog weggooien.
        # Onbevestigde namen (ook als die eerder al waren ingevuld) moeten
        # door dezelfde onafhankelijke controle als nieuwe AI-namen.
        name = r['name'] or ''
        if r['status'] == 'cv' and not r['name_verified'] and name:
            name = ai_naam_bevestigd(name, r)
        if not name and res.get('naam'):
            name = ai_naam_bevestigd(res['naam'], r)
        if r['status'] == 'cv':
            if name:
                db.execute("UPDATE docs SET name=?, name_verified=1, reason=? WHERE path=?",
                           (name, (r['reason'] or '') + '; AI-naam onafhankelijk bevestigd', r['path']))
                counts['cv'] += 1
            else:
                db.execute("UPDATE docs SET name='', name_verified=0, reason=? WHERE path=?",
                           ((r['reason'] or '') + '; naam na AI-controle niet bevestigd', r['path']))
                counts['onzeker'] += 1
        elif res['soort'] == 'cv':
            if not cv_kenmerk:
                name = ''      # een referentiebrief / losse lijst mag nooit cv worden door AI alleen
            if name and contact and cv_kenmerk and reasonable_name(name):
                db.execute("UPDATE docs SET status='cv', name_verified=1, name=?, reason=? WHERE path=?",
                           (name, (r['reason'] or '') + f'; AI: cv ({reden}); naam gecontroleerd', r['path']))
                counts['cv'] += 1
            else:
                db.execute("UPDATE docs SET reason=? WHERE path=?",
                           ((r['reason'] or '') + '; AI: lijkt een cv, maar ' +
                            ('naam niet bevestigd' if not name else 'onvoldoende contact of cv-bewijs') +
                            ' – zelf kijken', r['path']))
                counts['onzeker'] += 1
        elif res['soort'] == 'geen_cv':
            # Een duidelijke sollicitatiebrief mag niet op review blijven
            # alleen omdat een oud score-etiket 'Werkervaring' suggereerde.
            # Bij echte cv-kenmerken mag AI nooit alleen beslissen.
            brief = bool(re.search(
                r'\b(?:geachte(?: heer| mevrouw)?|met vriendelijke groet|dear sir|dear madam)\b',
                r['snippet'] or ''))
            if cv_kenmerk and not brief and not ('vacature' in rs or 'sjabloon' in rs):
                db.execute("UPDATE docs SET reason=? WHERE path=?",
                           ((r['reason'] or '') + '; AI: geen cv, maar cv-signalen; controle nodig', r['path']))
                counts['onzeker'] += 1
            else:
                db.execute("UPDATE docs SET status='other', reason=? WHERE path=?",
                           ((r['reason'] or '') + f'; AI: geen cv ({reden})', r['path']))
                counts['geen'] += 1
        else:
            db.execute("UPDATE docs SET reason=? WHERE path=?",
                       ((r['reason'] or '') + '; AI: twijfelt ook', r['path']))
            counts['onzeker'] += 1
        if i % 20 == 0 or i == len(todo):
            db.commit()
            try:
                cache_path.write_text(json.dumps(cache, ensure_ascii=False), encoding='utf-8')
            except OSError:
                pass
            print(f"AI-TWIJFEL: {i}/{len(todo)} – {counts['cv']} cv, {counts['geen']} geen cv, "
                  f"{counts['onzeker']} blijft twijfel", flush=True)
    db.commit()


def main(argv=None):
    parser = argparse.ArgumentParser(description='CV Crawler voor Windows/macOS: lokaal scannen + dedupliceren + Excel.')
    parser.add_argument('--root',action='append',help='Scan deze map (meerdere --root mogelijk); standaard gebruikersmap + iCloud')
    parser.add_argument('--output',default=str(Path.home()/'CV_Crawler_Export'),help='Outputmap (standaard ~/CV_Crawler_Export)')
    parser.add_argument('--threshold',type=int,default=70,help='Minimumscore voor bevestigd CV (standaard 70)')
    parser.add_argument('--max-mb',type=int,default=30,help='Max MB per bestand (standaard 30)')
    parser.add_argument('--reader-timeout',type=int,default=90,help='Max seconden per document (standaard 90)')
    parser.add_argument('--no-ocr',action='store_true',help='Sla OCR op ingescande PDFs over')
    parser.add_argument('--ocr-pages',type=int,default=5,help='OCR maximaal deze pagina’s per PDF')
    parser.add_argument('--online-vers', action='store_true',
                        help='Online: iedereen opnieuw opzoeken i.p.v. opgeslagen zoekresultaten gebruiken')
    parser.add_argument('--cloud-verplaatsen', action='store_true',
                        help="Bij opschonen cv's ook uit OneDrive/iCloud verplaatsen (standaard: kopiëren)")
    parser.add_argument('--cloud-download', action='store_true',
                        help='Ook bestanden die alleen online staan (OneDrive/iCloud) downloaden en lezen')
    parser.add_argument('--deep',action='store_true',help='Neem ook verborgen mappen en Library mee (kan zeer lang duren)')
    parser.add_argument('--include-volumes',action='store_true',help='Neem USB/netwerkschijven (Windows) of /Volumes (Mac) mee')
    parser.add_argument('--force',action='store_true',help='Verwerk eerder gescande bestanden opnieuw')
    parser.add_argument('--online',action='store_true',help='Vul ontbrekende LinkedIn/e-mail/telefoon online aan via Tavily (TAVILY_API_KEY); alleen suggesties')
    parser.add_argument('--online-limit',type=int,default=0,help='Maximaal aantal personen voor online zoekstap (0 = geen limiet)')
    parser.add_argument('--no-refresh',action='store_true',help='Sla het bijwerken van verplaatste bestanden en oude leesfouten over')
    parser.add_argument('--opschonen',action='store_true',help='Verplaats losse cv-originelen naar Alle_cvs (zips blijven staan)')
    parser.add_argument('--opschonen-plan',action='store_true',help='Laat alleen zien wat Opschonen zou doen')
    parser.add_argument('--terugzetten',action='store_true',help='Zet opgeschoonde cv\'s terug op hun oude plek')
    parser.add_argument('--export-only',action='store_true',help='Alleen CSV/XLSX uit bestaande database regenereren')
    args = parser.parse_args(argv)
    if args.cloud_download:
        os.environ["CV_CLOUD_DOWNLOAD"] = "1"
    if args.online_vers:
        os.environ["CV_ONLINE_VERS"] = "1"
    global CLOUD_MOVE
    CLOUD_MOVE = args.cloud_verplaatsen
    if sys.version_info < (3,10):
        parser.error('Python 3.10 of nieuwer nodig; installeer bijvoorbeeld Python 3.12 via Homebrew')
    if not 60 <= args.threshold <= 100:
        parser.error('threshold moet tussen 60 en 100 liggen')
    out = Path(args.output).expanduser().resolve()
    global OUT_DIR
    OUT_DIR = out
    if args.root and not (args.export_only or args.opschonen or args.opschonen_plan or args.terugzetten):
        SCOPE[:] = [os.path.normcase(os.path.realpath(str(Path(r).expanduser()))) for r in args.root]
    elif args.opschonen or args.opschonen_plan:
        # Opschonen geldt alleen voor wat de LAATSTE scan bekeek (bij 'Kies één map' dus alleen die map).
        try:
            SCOPE[:] = json.loads((out / 'laatste_scan.json').read_text(encoding='utf-8')).get('scope') or []
        except (OSError, ValueError):
            pass
        if SCOPE:
            print('OPSCHONEN ALLEEN IN: ' + ', '.join(SCOPE), flush=True)
    os.umask(0o077)  # Op Windows beheert NTFS/gebruikersprofiel bestandsrechten.
    db = db_open(out)
    try:
        out.chmod(0o700)
    except OSError:
        pass
    scan_error = None
    if not (args.opschonen or args.opschonen_plan or args.terugzetten):
        try:
            import ai_rechter
            ai_rechter.start_download(log=lambda m: print(m, flush=True))
        except Exception as exc:
            print(f'AI-MODEL: niet beschikbaar ({type(exc).__name__}); verder zonder AI.', flush=True)
    try:
        if args.opschonen_plan or args.opschonen or args.terugzetten:
            if args.terugzetten:
                undo_cleanup(db, out)
            else:
                run_cleanup(db, out, dry_run=args.opschonen_plan)
            if args.opschonen_plan:
                return 0
            args.export_only = True
            args.no_refresh = True
        if not args.export_only:
            try:
                ingest(args,db,out)
            except KeyboardInterrupt:
                print('\nScan onderbroken. Reeds opgeslagen gegevens blijven behouden.')
                db.commit()
            except Exception as exc:
                # Preserve all indexed records and still build an Excel snapshot.
                scan_error = exc
                db.commit()
                print('SCAN ONDERBROKEN:', type(exc).__name__, str(exc)[:500], file=sys.stderr, flush=True)
                print('Probeer Excel alsnog te maken uit tussentijds bewaarde CV-gegevens.', flush=True)
        if not args.no_refresh:
            try:
                refresh_index(args, db, out)
            except KeyboardInterrupt:
                db.commit()
            except Exception as exc:
                db.commit()
                print('WAARSCHUWING: bijwerken mislukt:', type(exc).__name__, str(exc)[:300], flush=True)
        try:
            import ai_rechter
            if ai_rechter._download_thread and ai_rechter._download_thread.is_alive():
                print('AI-MODEL: nog bezig met ophalen; maximaal 15 minuten wachten…', flush=True)
                ai_rechter.wait_download(15 * 60)
            ai_twijfel(db, out)
        except KeyboardInterrupt:
            db.commit()
        except Exception as exc:
            db.commit()
            print('AI-TWIJFEL: overgeslagen (' + type(exc).__name__ + ': ' + str(exc)[:120] + ')', flush=True)
        people = build_exports(db,out)
        if args.online:
            try:
                import ai_rechter
                print('AI-CONTROLE: ' + ('aan (lokaal, gratis, er gaat geen cv-tekst naar internet)' if ai_rechter.available()
                                         else 'uit (model of onderdelen ontbreken); alleen de regels beslissen'), flush=True)
            except Exception:
                pass
            if online_suggestions(people,out,db=db,max_people=args.online_limit or 10**9):
                build_exports(db,out)   # Excel opnieuw, nu met de online-kolommen
    finally:
        db.close()
    return 1 if scan_error is not None else 0


if __name__ == '__main__':
    configure_console()
    sys.exit(main())
