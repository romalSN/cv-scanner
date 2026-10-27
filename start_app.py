"""Startpunt voor de .exe (Windows) en .app (Mac).

Hetzelfde programma doet drie dingen, afhankelijk van hoe het gestart wordt:
  (geen optie)      -> het venster (gui.py)
  --backend ...     -> de scanner zelf (cv_crawler.py), gestart door het venster
  --reader-worker   -> één document lezen (reader_worker.py), gestart door de scanner
"""
import os
import sys


def _fix_streams():
    # Een .exe zonder console heeft soms geen stdout; gebruik dan de pijp van het venster.
    for fd, name in ((1, 'stdout'), (2, 'stderr')):
        if getattr(sys, name) is None:
            try:
                setattr(sys, name, open(fd, 'w', encoding='utf-8', errors='replace', buffering=1, closefd=False))
            except OSError:
                setattr(sys, name, open(os.devnull, 'w'))


def _certificates():
    # In de .app/.exe vindt Python de beveiligingscertificaten van het systeem soms niet
    # (Mac: 'CERTIFICATE_VERIFY_FAILED'). Gebruik dan de meegeleverde lijst van certifi.
    try:
        import certifi
        os.environ.setdefault('SSL_CERT_FILE', certifi.where())
    except ImportError:
        pass


def run():
    os.environ.setdefault('PYTHONUTF8', '1')
    _certificates()
    if '--ssl-test' in sys.argv:          # alleen voor de bouwtest: werkt https in de app?
        import urllib.request, urllib.error
        try:
            urllib.request.urlopen('https://www.python.org', timeout=20)
            print('SSL OK')
        except urllib.error.HTTPError:
            print('SSL OK')                  # verbinding en certificaat werkten
        except Exception as exc:
            print('SSL FOUT:', exc)
        sys.exit(0)
    if '--reader-worker' in sys.argv:
        sys.argv.remove('--reader-worker')
        _fix_streams()
        import reader_worker
        sys.exit(reader_worker.main())
    if '--backend' in sys.argv:
        sys.argv.remove('--backend')
        _fix_streams()
        import cv_crawler
        cv_crawler.configure_console()
        sys.exit(cv_crawler.main())
    import gui
    gui.main()


if __name__ == '__main__':
    run()
