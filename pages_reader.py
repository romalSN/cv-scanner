"""Tekst uit Apple Pages-bestanden (.pages) halen, zonder Pages of extra pakketten.

- Pages 2013 en nieuwer: zip met Index/*.iwa (Snappy-gecomprimeerde protobuf).
  De lopende tekst staat in TSWP.StorageArchive (type 2001), veld 3.
- Pages '09: zip met index.xml (tekst in <sf:p> e.d.).
- Lukt dat niet: QuickLook/Preview.pdf (wordt dan als pdf gelezen door de aanroeper).
"""
from __future__ import annotations

import re
import zipfile
import xml.etree.ElementTree as ET


def _varint(buf, pos):
    result = shift = 0
    while True:
        b = buf[pos]
        pos += 1
        result |= (b & 0x7F) << shift
        if not b & 0x80:
            return result, pos
        shift += 7


def snappy_decompress(data):
    """Ruwe Snappy-decompressie (zonder framing/CRC), zoals iWork die gebruikt."""
    length, pos = _varint(data, 0)
    out = bytearray()
    n = len(data)
    while pos < n:
        tag = data[pos]
        pos += 1
        kind = tag & 3
        if kind == 0:                       # letterlijke bytes
            size = tag >> 2
            if size >= 60:
                extra = size - 59
                size = int.from_bytes(data[pos:pos + extra], 'little')
                pos += extra
            size += 1
            out += data[pos:pos + size]
            pos += size
            continue
        if kind == 1:
            size = ((tag >> 2) & 7) + 4
            offset = ((tag >> 5) << 8) | data[pos]
            pos += 1
        elif kind == 2:
            size = (tag >> 2) + 1
            offset = int.from_bytes(data[pos:pos + 2], 'little')
            pos += 2
        else:
            size = (tag >> 2) + 1
            offset = int.from_bytes(data[pos:pos + 4], 'little')
            pos += 4
        if offset <= 0 or offset > len(out):
            raise ValueError('ongeldige snappy-data')
        start = len(out) - offset
        for i in range(size):               # mag overlappen
            out.append(out[start + i])
    if len(out) != length:
        raise ValueError('snappy-lengte klopt niet')
    return bytes(out)


def iwa_decompress(raw):
    """iWork-chunks: 0x00 + 3 bytes lengte (little endian) + snappy-data."""
    out = bytearray()
    pos = 0
    while pos + 4 <= len(raw):
        if raw[pos] != 0:
            raise ValueError('onbekend iwa-blok')
        size = int.from_bytes(raw[pos + 1:pos + 4], 'little')
        pos += 4
        out += snappy_decompress(raw[pos:pos + size])
        pos += size
    return bytes(out)


def _fields(msg):
    """Minimale protobuf-lezer: (veldnummer, wiretype, waarde)."""
    pos, n = 0, len(msg)
    while pos < n:
        key, pos = _varint(msg, pos)
        field, wt = key >> 3, key & 7
        if wt == 0:
            val, pos = _varint(msg, pos)
        elif wt == 1:
            val, pos = msg[pos:pos + 8], pos + 8
        elif wt == 2:
            size, pos = _varint(msg, pos)
            val, pos = msg[pos:pos + size], pos + size
        elif wt == 5:
            val, pos = msg[pos:pos + 4], pos + 4
        else:
            raise ValueError('onbekend protobuf-type')
        yield field, wt, val


def _archives(data):
    """IWA-stroom: [varint len][ArchiveInfo][berichten...] herhaald. Geeft (type, bericht)."""
    pos = 0
    while pos < len(data):
        size, pos = _varint(data, pos)
        info = data[pos:pos + size]
        pos += size
        for field, wt, val in _fields(info):
            if field == 2 and wt == 2:               # MessageInfo
                mtype = mlen = None
                for f2, w2, v2 in _fields(val):
                    if f2 == 1 and w2 == 0:
                        mtype = v2
                    elif f2 == 3 and w2 == 0:
                        mlen = v2
                if mlen is None:
                    continue
                yield mtype, data[pos:pos + mlen]
                pos += mlen


TEXT_TYPES = {2001, 2002, 2005}       # TSWP.StorageArchive en varianten


def _clean(text):
    text = text.replace(' ', '\n').replace(' ', '\n')
    text = re.sub('[￼\u0000-\u0008\u000b\u000c\u000e-\u001f]', ' ', text)
    return text.strip()


def iwa_text(zf):
    names = sorted(n for n in zf.namelist() if n.lower().endswith('.iwa'))
    # Document.iwa eerst: daar staat de hoofdtekst.
    names.sort(key=lambda n: (not n.endswith('Document.iwa'), n))
    parts = []
    for name in names:
        try:
            data = iwa_decompress(zf.read(name))
        except (ValueError, IndexError):
            continue
        try:
            for mtype, msg in _archives(data):
                if mtype not in TEXT_TYPES:
                    continue
                for field, wt, val in _fields(msg):
                    if field == 3 and wt == 2:
                        try:
                            s = _clean(val.decode('utf-8'))
                        except UnicodeDecodeError:
                            continue
                        if s and s not in parts:
                            parts.append(s)
        except (ValueError, IndexError):
            continue
    return '\n'.join(parts)


def xml09_text(zf):
    name = next((n for n in zf.namelist() if n in ('index.xml', 'index.xml.gz')), None)
    if not name:
        return ''
    raw = zf.read(name)
    if name.endswith('.gz'):
        import gzip
        raw = gzip.decompress(raw)
    root = ET.fromstring(raw)
    lines = []
    for el in root.iter():
        tag = el.tag.rsplit('}', 1)[-1]
        if tag in ('p', 'li', 'h'):
            t = ''.join(el.itertext()).strip()
            if t:
                lines.append(t)
    return '\n'.join(lines)


class _Folder:
    """Pages-pakket (map) met dezelfde vorm als een zip. Nieuwere pakketten hebben Index.zip erin."""
    def __init__(self, path):
        from pathlib import Path
        self.root = Path(path)
        self.files = {str(f.relative_to(self.root)).replace('\\', '/'): f
                      for f in self.root.rglob('*') if f.is_file()}
        self.inner = zipfile.ZipFile(self.files['Index.zip']) if 'Index.zip' in self.files else None

    def namelist(self):
        return list(self.files) + (self.inner.namelist() if self.inner else [])

    def read(self, name):
        if name in self.files:
            return self.files[name].read_bytes()
        return self.inner.read(name)

    def __enter__(self):
        return self

    def __exit__(self, *a):
        if self.inner:
            self.inner.close()


def pages_text(path):
    """Geeft (tekst, preview_pdf_bytes_of_None). Werkt voor een .pages-bestand en een .pages-map."""
    import os
    opener = _Folder if os.path.isdir(path) else zipfile.ZipFile
    with opener(path) as zf:
        names = set(zf.namelist())
        text = ''
        try:
            text = iwa_text(zf) if any(n.endswith('.iwa') for n in names) else xml09_text(zf)
        except Exception:
            text = ''
        preview = zf.read('QuickLook/Preview.pdf') if 'QuickLook/Preview.pdf' in names else None
        return _clean(text), preview
