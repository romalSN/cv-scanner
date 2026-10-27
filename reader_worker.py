#!/usr/bin/env python3
"""Isolated document reader for CV Scanner Windows V3.

Never run directly: cv_crawler.py starts this process for each document.
Only extracts text locally and serializes the result to stdout as JSON.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--file', required=True)
    parser.add_argument('--no-ocr', action='store_true')
    parser.add_argument('--ocr-pages', type=int, default=5)
    parser.add_argument('--ocr-lang', default='eng')
    args = parser.parse_args()
    if os.environ.get('CV_BLOCK_CRYPTOGRAPHY') == '1':
        # cryptography crasht in deze Python; laat pypdf zijn eigen
        # ingebouwde ontsleuteling gebruiken (AES-PDF's lukken dan niet).
        sys.modules['cryptography'] = None
    from cv_crawler import read_document
    try:
        result = read_document(Path(args.file), use_ocr=not args.no_ocr,
                               ocr_lang=args.ocr_lang,
                               ocr_pages=args.ocr_pages)
    except Exception as exc:
        print(f'{type(exc).__name__}: {exc}', file=sys.stderr)
        return 2
    sys.stdout.write(json.dumps(result, ensure_ascii=True))
    return 0


if __name__ == '__main__':
    sys.exit(main())
