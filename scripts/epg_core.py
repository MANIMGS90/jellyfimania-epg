#!/usr/bin/env python3
"""
epg_core.py — piezas básicas de merge_epg.py: comparar nombres de canales
entre fuentes, leer XMLTV "al vuelo" (sin llenar la memoria) y leer las
guías de apoyo (donantes).
"""
import datetime
import re
import sys
import unicodedata
import xml.etree.ElementTree as ET

_TS_RE = re.compile(r"^(\d{4})(\d{2})(\d{2})(\d{2})(\d{2})(\d{2})\s*([+-]\d{4})?$")

# ---------------------------------------------------------------------------
# Comparación de nombres entre fuentes (misma idea que usa la app de Roku)
# ---------------------------------------------------------------------------
_STOP_WORDS = {"hd", "fhd", "sd", "uhd", "4k", "latino", "latinoamerica",
               "latam", "mexico", "mx", "usa", "us", "hevc", "raw", "canal"}
_SEPARATORS = ["¦", "│", "/", "»", "›", ">", "•", "·", ":", " - ", " – ", " — "]


def loose_key(name):
    """'CINE | *HBO* HD' -> 'cinehbo'; 'HBO (Latin America)' -> 'hbo'."""
    t = unicodedata.normalize("NFKD", name.lower())
    t = "".join(c for c in t if not unicodedata.combining(c))
    t = re.sub(r"\([^)]*\)|\[[^\]]*\]", " ", t)
    t = t.replace("c1nemax", "cinemax")
    t = re.sub(r"[^a-z0-9]+", " ", t)
    out = "".join(w for w in t.split() if w not in _STOP_WORDS)
    return out if len(out) >= 2 else ""


def title_parts(name):
    """Pedazos tras separar por | / : - » • (último primero). El primero
    (categoría/país: 'CINE', 'ES') se descarta cuando hay más de uno."""
    x = name
    for sp in _SEPARATORS:
        x = x.replace(sp, "|")
    if "|" not in x:
        return []
    return [p.strip() for p in reversed(x.split("|")[1:]) if p.strip()]


def keys_for(name):
    keys = []
    k = loose_key(name)
    if k:
        keys.append(k)
    for part in title_parts(name):
        pk = loose_key(part)
        if pk and pk not in keys:
            keys.append(pk)
    return keys


def parse_xmltv_time(ts):
    """datetime aware en UTC, o None si no se pudo parsear."""
    if not ts:
        return None
    m = _TS_RE.match(ts.strip())
    if not m:
        return None
    y, mo, d, h, mi, s, off = m.groups()
    try:
        dt = datetime.datetime(int(y), int(mo), int(d), int(h), int(mi), int(s),
                               tzinfo=datetime.timezone.utc)
    except ValueError:
        return None
    if off:
        sign = 1 if off[0] == "+" else -1
        dt -= sign * datetime.timedelta(hours=int(off[1:3]), minutes=int(off[3:5]))
    return dt


def compact(elem):
    """Quita espacios de relleno (solo whitespace) sin tocar ningún dato."""
    for e in elem.iter():
        if e.text is not None and not e.text.strip():
            e.text = None
        e.tail = None


def _iter_top(path):
    """Recorre los <channel> y <programme> de un XMLTV SIN cargarlo entero
    en memoria. Lo que el que llama no conserve se libera enseguida."""
    root = None
    for ev, el in ET.iterparse(path, events=("start", "end")):
        if ev == "start":
            if root is None:
                root = el
                if el.tag != "tv":
                    print(f"AVISO: {path} no tiene <tv> como raíz (tiene <{el.tag}>); se omite.",
                          file=sys.stderr)
                    return
            continue
        if el.tag in ("channel", "programme"):
            yield el
            root.clear()


def collect(paths, now, keep_past, forward):
    """Lee todas las fuentes (en streaming). Solo se conservan los programas
    dentro de la ventana horaria. Devuelve (canales, programas, repetidos)."""
    min_stop = now - datetime.timedelta(hours=keep_past)
    max_start = now + datetime.timedelta(hours=forward)
    channels = []                # elementos <channel> (el primero por id)
    owner = {}                   # id -> archivo que lo aportó
    progs = []                   # (start_dt|None, stop_dt|None, elem)
    prior = {}                   # canal -> [(start, stop)] de fuentes ANTERIORES
    redundant = 0

    for path in paths:
        cur = {}
        n_ch = n_pr = n_red = n_out = 0
        try:
            for child in _iter_top(path):
                if child.tag == "channel":
                    cid = child.get("id")
                    if cid is None:
                        continue
                    if cid in owner:
                        print(f'AVISO: canal id="{cid}" ya venía de {owner[cid]}; '
                              f"se ignora la definición de {path}.", file=sys.stderr)
                        continue
                    owner[cid] = path
                    compact(child)
                    channels.append(child)
                    n_ch += 1
                else:
                    ch = child.get("channel") or ""
                    st = parse_xmltv_time(child.get("start"))
                    sp = parse_xmltv_time(child.get("stop"))
                    if st is not None and sp is not None:
                        if not (sp >= min_stop and st <= max_start):
                            n_out += 1
                            continue
                        # ¿Se solapa con un programa de una fuente anterior?
                        clash = False
                        for (ps, pe) in prior.get(ch, ()):
                            if st < pe and sp > ps:
                                clash = True
                                break
                        if clash:
                            n_red += 1
                            continue
                        cur.setdefault(ch, []).append((st, sp))
                    compact(child)
                    progs.append((st, sp, child))
                    n_pr += 1
        except (ET.ParseError, OSError) as e:
            print(f"AVISO: {path} no se pudo leer completo ({e}); se usa lo leído hasta ahí.",
                  file=sys.stderr)
        for ch, lst in cur.items():
            prior.setdefault(ch, []).extend(lst)
        redundant += n_red
        print(f"{path}: {n_ch} canales nuevos, {n_pr} programas guardados, "
              f"{n_red} repetidos de otra fuente omitidos, {n_out} fuera de horario descartados",
              file=sys.stderr)
    return channels, progs, redundant


def read_donors(paths, now, keep_past, forward, wanted_keys):
    """Lee las guías de APOYO en streaming y se queda SOLO con los canales
    cuyo nombre coincide con algún canal vacío (wanted_keys), y de ellos solo
    los programas dentro de la ventana. Los ids se prefijan por archivo para
    no chocar con los de las fuentes principales."""
    min_stop = now - datetime.timedelta(hours=keep_past)
    max_start = now + datetime.timedelta(hours=forward)
    d_channels, d_progs = [], []
    for i, path in enumerate(paths):
        prefix = f"~d{i}~"
        wanted_ids = set()
        n_ch = n_pr = 0
        try:
            for el in _iter_top(path):
                if el.tag == "channel":
                    cid = el.get("id")
                    if cid is None:
                        continue
                    nms = [dn.text.strip() for dn in el.findall("display-name") if dn.text and dn.text.strip()]
                    ks = set()
                    for nm in (nms or [cid]):
                        ks.update(keys_for(nm))
                    if ks & wanted_keys:
                        compact(el)
                        wanted_ids.add(cid)
                        el.set("id", prefix + cid)
                        d_channels.append(el)
                        n_ch += 1
                else:
                    cid = el.get("channel")
                    if cid in wanted_ids:
                        st = parse_xmltv_time(el.get("start"))
                        sp = parse_xmltv_time(el.get("stop"))
                        if st is not None and sp is not None and sp >= min_stop and st <= max_start:
                            compact(el)
                            el.set("channel", prefix + cid)
                            d_progs.append((st, sp, el))
                            n_pr += 1
        except (ET.ParseError, OSError) as e:
            print(f"AVISO: {path} no se pudo leer completo ({e}); se usa lo leído hasta ahí.",
                  file=sys.stderr)
        print(f"{path}: {n_ch} canales útiles, {n_pr} programas de apoyo guardados", file=sys.stderr)
    return d_channels, d_progs
