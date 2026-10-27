"""Lichte lokale RTF-naar-tekstlezer voor CV-scanning.

Geen externe installatie. Ondersteunt gebruikelijke tekst, Unicode, accolades,
paragrafen, tabbladen en standaard RTF-groepen. Geavanceerde RTF-opmaak wordt
niet volledig ondersteund; daarvoor wordt bij twijfel handmatige controle gevraagd.
"""
from __future__ import annotations
import re

IGNORED = {'fonttbl', 'colortbl', 'stylesheet', 'info', 'pict', 'object',
           'header', 'footer', 'headerl', 'headerr', 'footerl', 'footerr',
           'footnote', 'annotation', 'generator', 'datastore', 'themedata',
           'xmlnstbl', 'filetbl', 'listtable', 'listoverridetable'}
CONTROL = {'par':'\n', 'line':'\n', 'tab':'\t', 'cell':'\t', 'row':'\n',
           'emdash':'—', 'endash':'–', 'bullet':'•', 'lquote':'‘',
           'rquote':'’', 'ldblquote':'“', 'rdblquote':'”', '~':' ',
           '_':'‑', '-':''}


def rtf_to_text(s: str) -> str:
    if not s.lstrip().startswith('{\\rtf'):
        return s
    out: list[str] = []
    # stack element: (is_ignored, current_uc, pending_unicode_fallback, just_opened)
    state = [[False, 1, 0, False]]
    i = 0
    while i < len(s):
        c = s[i]
        ignored, uc, fallback, just_opened = state[-1]
        if c == '{':
            state.append([ignored, uc, 0, True]); i += 1; continue
        if c == '}':
            if len(state)>1: state.pop()
            i += 1; continue
        if c != '\\':
            if c not in '\r\n' and not ignored and not fallback:
                out.append(c)
            elif fallback and c not in '\r\n':
                state[-1][2] -= 1
            i += 1; continue
        if i + 1 >= len(s):
            i += 1; continue
        nxt = s[i+1]
        if nxt in ('\\', '{', '}'):
            if not ignored and not fallback: out.append(nxt)
            elif fallback: state[-1][2] -= 1
            i += 2; continue
        if nxt == "'" and i + 3 < len(s):
            try:
                ch = bytes([int(s[i+2:i+4], 16)]).decode('cp1252', errors='replace')
                if not ignored and not fallback: out.append(ch)
                elif fallback: state[-1][2] -= 1
                i += 4; continue
            except ValueError:
                pass
        if nxt == '*' and state[-1][3]:
            state[-1][0] = True
            i += 2; continue
        match = re.match(r'\\([a-zA-Z]+)(-?\d+)? ?', s[i:])
        if not match:
            if nxt in CONTROL and not ignored and not fallback: out.append(CONTROL[nxt])
            elif fallback: state[-1][2] -= 1
            i += 2; continue
        word = match.group(1).lower()
        value = match.group(2)
        i += len(match.group(0))
        if word in IGNORED and state[-1][3]:
            state[-1][0] = True
        state[-1][3] = False
        if word == 'uc' and value is not None:
            state[-1][1] = max(0, min(10, int(value)))
        elif word == 'u' and value is not None:
            if not state[-1][0]:
                n = int(value)
                if n < 0: n += 65536
                if 0 <= n < 65536: out.append(chr(n))
            state[-1][2] = state[-1][1]
        elif word == 'bin' and value is not None:
            i += max(0, int(value))
        elif word in CONTROL and not state[-1][0]:
            out.append(CONTROL[word])
    return ''.join(out)
