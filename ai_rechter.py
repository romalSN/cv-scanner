"""Lokale AI-controle (gratis, offline): is een gevonden LinkedIn-profiel van dezelfde persoon als het cv,
of van een naamgenoot? Draait met llama.cpp (llama-cpp-python) en het model Phi-3.5-mini (MIT-licentie).

- Het model (ca. 2,4 GB) wordt één keer opgehaald en bewaard in ~/.cv-scanner-ai/. Daarna werkt het
  zonder internet; er gaat geen cv-tekst naar buiten.
- Ophalen gebeurt op de achtergrond tijdens het scannen. Is het nog niet klaar, of werkt de AI niet
  op deze computer, dan werkt de scanner gewoon zoals zonder AI.
- De AI beslist nooit alleen: 'nee' wijst een profiel af; 'ja' telt alleen samen met echt bewijs
  uit de regels (zie cv_crawler.online_suggestions)."""
from __future__ import annotations

import json
import os
import sys
import threading
import time
import urllib.request
from pathlib import Path

MODEL_URL = ('https://huggingface.co/bartowski/Phi-3.5-mini-instruct-GGUF/resolve/main/'
             'Phi-3.5-mini-instruct-Q4_K_M.gguf')
MODEL_FILE = 'Phi-3.5-mini-instruct-Q4_K_M.gguf'
MODEL_ID = 'phi35mini-q4km-v1'          # in de cache-sleutel: ander model/prompt = opnieuw beoordelen
MIN_BYTES = 2_000_000_000
AI_DIR = Path.home() / '.cv-scanner-ai'
# AI bij twijfelgevallen (cv of niet) pas aan als de test bewijst dat hij geen niet-cv's als cv aanmerkt.
TWIJFEL_AAN = os.environ.get('CV_AI_TWIJFEL', '0') == '1'

INSTRUCTIE = (
    "Je bent een zorgvuldige recruiter. Je krijgt een cv en een kort stukje tekst van een LinkedIn-profiel met "
    "dezelfde naam. Beslis of het profiel van DEZELFDE persoon is, of van een naamgenoot.\n"
    "Let op:\n"
    "- Dezelfde naam alleen zegt niets.\n"
    "- Hetzelfde bedrijf of dezelfde school alleen is NIET genoeg als de rest niet past. Kijk naar functie, niveau, "
    "vakgebied en periode. Voorbeelden van een ANDERE persoon: cv 'stagiair/kassamedewerker/teamleider' en profiel "
    "'CEO/CFO/directeur/hoogleraar/oprichter sinds 1999' bij hetzelfde bedrijf; cv 'student' en profiel 'docent sinds "
    "1998' op dezelfde school; cv 'developer' en profiel 'marketing director'. Dan is het oordeel 'nee'.\n"
    "- Ander beroep, ander vakgebied, ander land, andere werkgevers of andere opleiding: 'nee'.\n"
    "- 'ja' als werkgever of school overeenkomt EN functie, vakgebied en periode ook passen.\n"
    "- 'onzeker' als het profiel te weinig informatie geeft.\n"
    "Antwoord met JSON: 'past' (wat overeenkomt), 'botst' (wat elkaar tegenspreekt), 'oordeel' (ja/nee/onzeker).")
SCHEMA = {"type": "object", "properties": {
    "past": {"type": "array", "items": {"type": "string"}},
    "botst": {"type": "array", "items": {"type": "string"}},
    "oordeel": {"type": "string", "enum": ["ja", "nee", "onzeker"]}},
    "required": ["past", "botst", "oordeel"]}

_llm = None
_llm_error = ''
_lock = threading.Lock()
_download_thread = None
status = {'fase': 'uit', 'procent': 0, 'fout': ''}


def model_path() -> Path:
    custom = os.environ.get('CV_AI_MODEL', '').strip()
    return Path(custom) if custom else AI_DIR / MODEL_FILE


def enabled() -> bool:
    return os.environ.get('CV_AI', '1') != '0'


def engine_ok() -> bool:
    try:
        import llama_cpp  # noqa: F401
        return True
    except Exception:
        return False


def model_ok(path: Path | None = None) -> bool:
    path = path or model_path()
    try:
        if not path.is_file():
            return False
        if not os.environ.get('CV_AI_MODEL') and path.stat().st_size < MIN_BYTES:
            return False
        with path.open('rb') as fh:
            return fh.read(4) == b'GGUF'
    except OSError:
        return False


def available() -> bool:
    return enabled() and engine_ok() and model_ok()


def download(log=print) -> bool:
    """Model ophalen (hervat een afgebroken download). Geeft True als het model klaar staat."""
    target = model_path()
    if model_ok(target):
        status.update(fase='klaar', procent=100)
        return True
    target.parent.mkdir(parents=True, exist_ok=True)
    part = target.with_suffix(target.suffix + '.part')
    done = part.stat().st_size if part.exists() else 0
    req = urllib.request.Request(MODEL_URL, headers={'User-Agent': 'CV-Scanner',
                                                     **({'Range': f'bytes={done}-'} if done else {})})
    status.update(fase='ophalen', procent=0, fout='')
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            if done and resp.status != 206:
                done = 0                      # server hervat niet: opnieuw beginnen
            total = done + int(resp.headers.get('Content-Length') or 0)
            last = -1
            with part.open('ab' if done else 'wb') as fh:
                while True:
                    chunk = resp.read(1 << 20)
                    if not chunk:
                        break
                    fh.write(chunk)
                    done += len(chunk)
                    pct = int(done * 100 / total) if total else 0
                    status['procent'] = pct
                    if pct // 10 != last // 10:
                        last = pct
                        log(f'AI-MODEL: ophalen {pct}% ({done // 1_000_000} van {total // 1_000_000} MB)')
        if total and done < total:
            raise RuntimeError(f'download onvolledig ({done} van {total} bytes)')
        os.replace(part, target)
        if not model_ok(target):
            raise RuntimeError('gedownload bestand is geen geldig model')
        status.update(fase='klaar', procent=100)
        log('AI-MODEL: klaar. De AI-controle wordt gebruikt bij het online zoeken.')
        return True
    except Exception as exc:
        status.update(fase='fout', fout=str(exc)[:160])
        log(f'AI-MODEL: ophalen lukte niet ({type(exc).__name__}: {str(exc)[:120]}). '
            'De scanner werkt gewoon zonder AI; bij de volgende scan probeert hij het opnieuw.')
        return False


def start_download(log=print):
    """Op de achtergrond ophalen (tijdens het scannen)."""
    global _download_thread
    if not enabled() or model_ok() or (_download_thread and _download_thread.is_alive()):
        return
    if not engine_ok():
        log('AI-MODEL: de AI-onderdelen ontbreken in deze versie; de scanner werkt zonder AI.')
        return
    log('AI-MODEL: wordt op de achtergrond opgehaald (eenmalig, ca. 2,4 GB). Scannen gaat gewoon door.')
    _download_thread = threading.Thread(target=download, args=(log,), daemon=True)
    _download_thread.start()


def wait_download(max_seconds: float) -> bool:
    if _download_thread and _download_thread.is_alive():
        _download_thread.join(max_seconds)
    return model_ok()


def _load():
    global _llm, _llm_error
    with _lock:
        if _llm is not None or _llm_error:
            return _llm
        try:
            from llama_cpp import Llama
            gpu = -1 if (sys.platform == 'darwin' and os.uname().machine == 'arm64') else 0
            _llm = Llama(model_path=str(model_path()), n_ctx=4096, n_threads=max(2, (os.cpu_count() or 4) - 1),
                         n_gpu_layers=gpu, verbose=False)
        except Exception as exc:
            _llm_error = f'{type(exc).__name__}: {str(exc)[:150]}'
            print('AI: laden lukte niet (' + _llm_error + '); verder zonder AI.', flush=True)
        return _llm


def judge(cv_text: str, profiel_text: str) -> dict | None:
    """{'oordeel': 'ja'|'nee'|'onzeker', 'past': [...], 'botst': [...], 'sec': float} of None (geen AI)."""
    if not available():
        return None
    llm = _load()
    if llm is None:
        return None
    vraag = f"{INSTRUCTIE}\n\nCV:\n{cv_text[:2500]}\n\nLINKEDIN-PROFIEL:\n{profiel_text[:2000]}"
    t = time.time()
    try:
        with _lock:
            out = llm.create_chat_completion(messages=[{"role": "user", "content": vraag}], temperature=0,
                                             max_tokens=220, response_format={"type": "json_object", "schema": SCHEMA})
        a = json.loads(out['choices'][0]['message']['content'])
        oordeel = a.get('oordeel') if a.get('oordeel') in ('ja', 'nee', 'onzeker') else 'onzeker'
        return {'oordeel': oordeel, 'past': [str(x)[:80] for x in (a.get('past') or [])][:5],
                'botst': [str(x)[:80] for x in (a.get('botst') or [])][:5], 'sec': round(time.time() - t, 1)}
    except Exception as exc:
        # Vastgelopen of onleesbaar antwoord: telt als 'onzeker' (veilig).
        return {'oordeel': 'onzeker', 'past': [], 'botst': [], 'fout': str(exc)[:100], 'sec': round(time.time() - t, 1)}


INSTRUCTIE_CV = (
    "Je beoordeelt documenten voor een recruitmentbureau. De tekst is automatisch uitgelezen (kleine letters, "
    "zonder leestekens). Bepaal wat voor document het is.\n"
    "Een CV is een overzicht van de EIGEN loopbaan van EEN persoon: een lijst met eigen banen/opdrachten en/of "
    "opleidingen (vaak met jaartallen), meestal met naam en contactgegevens. Een profielschets van een kandidaat "
    "met opdrachten telt ook als cv.\n"
    "Let op, dit is GEEN cv, ook al staat er een naam of ervaring in:\n"
    "- vacature (wat ga je doen, wat vragen wij, wat bieden wij)\n"
    "- sollicitatiebrief of motivatiebrief (geachte, met vriendelijke groet)\n"
    "- referentie of aanbeveling (iemand ANDERS schrijft over de persoon)\n"
    "- certificaat of diploma, contract of overeenkomst, offerte of factuur\n"
    "- verslag, scriptie of rapport; leeg formulier; lijst met meerdere kandidaten; korte notitie of e-mail\n"
    "Antwoord met JSON in deze volgorde: 'reden' (kort: waarom), 'documentsoort', 'over_wie' (een_persoon / "
    "meerdere_personen / geen_persoon), 'eigen_loopbaan' (true als er een lijst met eigen banen of opleidingen "
    "van die persoon staat), 'naam' (naam van de persoon of leeg).")
SOORTEN = ["cv", "sollicitatiebrief", "vacature", "referentie", "certificaat", "contract", "offerte_of_factuur",
           "verslag", "formulier", "kandidatenlijst", "notitie_of_email", "anders"]
SCHEMA_CV = {"type": "object", "properties": {
    "reden": {"type": "string"},
    "documentsoort": {"type": "string", "enum": SOORTEN},
    "over_wie": {"type": "string", "enum": ["een_persoon", "meerdere_personen", "geen_persoon"]},
    "eigen_loopbaan": {"type": "boolean"},
    "naam": {"type": "string"}},
    "required": ["reden", "documentsoort", "over_wie", "eigen_loopbaan", "naam"]}


def classify(text: str) -> dict | None:
    """Twijfelgeval: {'soort': 'cv'|'geen_cv'|'onzeker', 'naam', 'reden', 'documentsoort'} of None (geen AI).
    Alleen 'cv' als de AI 'cv' zegt EN het over één persoon gaat EN er een eigen loopbaan in staat."""
    if not available():
        return None
    llm = _load()
    if llm is None:
        return None
    vraag = f"{INSTRUCTIE_CV}\n\nDOCUMENT:\n{text[:3000]}"
    t = time.time()
    try:
        with _lock:
            out = llm.create_chat_completion(messages=[{"role": "user", "content": vraag}], temperature=0,
                                             max_tokens=200, response_format={"type": "json_object", "schema": SCHEMA_CV})
        a = json.loads(out['choices'][0]['message']['content'])
        ds = a.get('documentsoort', 'anders')
        if ds == 'cv' and a.get('over_wie') == 'een_persoon' and a.get('eigen_loopbaan') is True:
            soort = 'cv'
        elif ds not in ('cv', 'anders'):
            soort = 'geen_cv'
        else:
            soort = 'onzeker'
        return {'soort': soort, 'documentsoort': ds, 'naam': str(a.get('naam') or '')[:80],
                'reden': str(a.get('reden') or '')[:160], 'sec': round(time.time() - t, 1)}
    except Exception as exc:
        return {'soort': 'onzeker', 'naam': '', 'reden': '', 'fout': str(exc)[:100], 'sec': round(time.time() - t, 1)}


def self_test(model: str) -> int:
    """Voor de bouwmachine: laad een (klein) model en beoordeel 2 gevallen. Print 'AI OK'."""
    os.environ['CV_AI_MODEL'] = model
    if not engine_ok():
        print('AI-TEST FOUT: llama_cpp ontbreekt'); return 1
    if not model_ok():
        print('AI-TEST FOUT: model ongeldig'); return 1
    r1 = judge("Jan de Vries\nWerkervaring\n2019 - heden Backend developer bij Codewerk B.V.\nOpleiding\nHogeschool van Amsterdam",
               "Jan de Vries - Backend Developer - Codewerk | LinkedIn. Hogeschool van Amsterdam.")
    r2 = judge("Jan de Vries\nWerkervaring\n2019 - heden Backend developer bij Codewerk B.V.",
               "Jan de Vries - Verpleegkundige - Spaarne Gasthuis | LinkedIn. Haarlem.")
    print('AI-TEST', r1, r2)
    if not r1 or not r2 or r1.get('fout') or r2.get('fout'):
        print('AI-TEST FOUT: geen bruikbaar antwoord'); return 1
    print(f"AI OK ({r1['sec']}s + {r2['sec']}s)")
    return 0
