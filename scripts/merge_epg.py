#!/usr/bin/env python3
"""
merge_epg.py — combina varias guías XMLTV en una sola, SIN perder contenido.

Uso:
    python3 scripts/merge_epg.py --output guide.xml [--gzip] a.xml b.xml [...]
    (opcional) --m3u mi_lista.m3u   -> escribe missing_channels.txt con los
                                       canales de tu lista que aun asi
                                       siguen sin programacion

El orden de los archivos es la prioridad: si el mismo canal (mismo id)
viene en varias fuentes, gana el PRIMERO (se avisa por stderr).

QUÉ HACE (y qué NO toca):
- <channel>: unión por id (el primero gana).
- <programme>: se conservan TODOS los programas de todas las fuentes,
  salvo los que son redundantes: un programa de una fuente de menor
  prioridad que se SOLAPA en horario con uno de una fuente anterior
  para el mismo canal (es el mismo programa repetido). Los huecos de
  la fuente principal se siguen llenando con las demás.
- NUEVO — RELLENO DE CANALES VACÍOS: si un canal quedó SIN programas en
  la ventana horaria, se busca en TODAS las fuentes otro canal con el
  mismo nombre (aunque tenga otro id, otro país o venga con separadores
  como "CINE | HBO" / "CINE|HBO", asteriscos, HD/FHD/SD, LATINO/MX...)
  y se le copian sus programas. Así casi ningún canal queda en
  "Sin programación". Se avisa por stderr cuántos se rellenaron.
- NUEVO — FUENTES DONANTES (--donors a.xml b.xml ...): guías de APOYO que
  solo sirven para rellenar canales que sigan vacíos. Sus canales NO se
  agregan a la salida (así la guía no engorda); solo se usan sus
  programas como "donante" por nombre. Ponlas AL FINAL del comando.
- NUEVO — REPORTE DE CANALES SIN FUENTE (--empty-report empty_channels.txt):
  lista los canales (id y nombres) que siguen sin programación aun
  después del relleno, para saber qué fuente falta buscar.
- NUEVO — GUÍA LIGERA PARA ROKU (--roku-output guide_roku.xml): además del
  guide.xml completo, escribe una segunda copia pensada para que la app
  la lea rápido: solo la ventana que la app realmente usa (-1 h / +40 h),
  solo los canales que tienen programas y solo los datos que la app
  muestra (título, descripción recortada, categoría). Los canales, los
  nombres y los programas de la ventana quedan TODOS; el guide.xml
  completo se sigue publicando igual.
- Salida COMPACTA: se quitan los espacios/saltos de línea de relleno
  que traen las fuentes (no cambia ningún dato; solo baja el peso).

VENTANA HORARIA (único recorte, y es automático y mínimo):
- Se descarta lo que terminó hace más de --keep-past-hours.
- Hacia adelante se intenta guardar --forward-hours (default 72 h).
- GitHub rechaza archivos de más de 100 MB. Si la ventana pedida no
  cabe en --max-mb (default 95), se baja de a 12 h hasta que quepa
  (nunca menos de --min-forward-hours) y se deja un AVISO visible en
  el log de la Action. Nada más se recorta.
"""
import argparse
import copy
import datetime
import gzip
import io
import os
import re
import shutil
import sys
import unicodedata
import urllib.request
import xml.etree.ElementTree as ET
from collections import defaultdict
from xml.sax.saxutils import escape, quoteattr

_TS_RE = re.compile(r"^(\d{4})(\d{2})(\d{2})(\d{2})(\d{2})(\d{2})\s*([+-]\d{4})?$")
_MIN = datetime.datetime.min.replace(tzinfo=datetime.timezone.utc)

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


def load_tv_root(path):
    try:
        root = ET.parse(path).getroot()
    except (ET.ParseError, OSError) as e:
        print(f"AVISO: no se pudo leer {path} como XML válido ({e}); se omite.", file=sys.stderr)
        return None
    if root.tag != "tv":
        print(f"AVISO: {path} no tiene <tv> como raíz (tiene <{root.tag}>); se omite.", file=sys.stderr)
        return None
    return root


def compact(elem):
    """Quita espacios de relleno (solo whitespace) sin tocar ningún dato."""
    for e in elem.iter():
        if e.text is not None and not e.text.strip():
            e.text = None
        e.tail = None


def collect(paths):
    """Lee todas las fuentes. Devuelve (canales, programas, stats)."""
    channels = []                # elementos <channel> (el primero por id)
    owner = {}                   # id -> archivo que lo aportó
    progs = []                   # (start_dt|None, stop_dt|None, elem)
    prior = {}                   # canal -> [(start, stop)] de fuentes ANTERIORES
    redundant = 0

    for path in paths:
        root = load_tv_root(path)
        if root is None:
            continue
        cur = {}
        n_ch = n_pr = n_red = 0
        for child in root:
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
            elif child.tag == "programme":
                ch = child.get("channel") or ""
                st = parse_xmltv_time(child.get("start"))
                sp = parse_xmltv_time(child.get("stop"))
                if st is not None and sp is not None:
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
        for ch, lst in cur.items():
            prior.setdefault(ch, []).extend(lst)
        redundant += n_red
        print(f"{path}: {n_ch} canales nuevos, {n_pr} programas guardados, "
              f"{n_red} repetidos de otra fuente omitidos", file=sys.stderr)
    return channels, progs, redundant


# ---------------------------------------------------------------------------
# Relleno de canales vacíos + reporte de la lista M3U
# ---------------------------------------------------------------------------
def _in_window(st, sp, min_stop, max_start):
    return st is None or sp is None or (sp >= min_stop and st <= max_start)


def _names_and_index(channels, ids_with_data, extra_ids=()):
    """names: id -> [nombres]; index: clave suelta -> [ids con programas]."""
    names = {}
    for c in channels:
        cid = c.get("id")
        if cid is None:
            continue
        nms = [dn.text.strip() for dn in c.findall("display-name") if dn.text and dn.text.strip()]
        names[cid] = nms or [cid]
    for cid in list(ids_with_data) + list(extra_ids):
        names.setdefault(cid, [cid])
    index = defaultdict(list)
    for cid in ids_with_data:
        for nm in names.get(cid, [cid]):
            for k in keys_for(nm):
                if cid not in index[k]:
                    index[k].append(cid)
    return names, index


def fill_empty_channels(channels, progs, now, keep_past, forward,
                       donor_channels=(), donor_progs=()):
    """Canales sin programas en la ventana -> copiar los de otro canal con
    el mismo nombre (de cualquier fuente, incluidas las donantes).
    Devuelve (rellenados, ids_sin_donante)."""
    min_stop = now - datetime.timedelta(hours=keep_past)
    max_start = now + datetime.timedelta(hours=forward)

    by_chan = defaultdict(list)      # id -> [(st, sp, elem)] dentro de la ventana
    for (st, sp, el) in list(progs) + list(donor_progs):
        if _in_window(st, sp, min_stop, max_start):
            by_chan[el.get("channel") or ""].append((st, sp, el))

    names, index = _names_and_index(list(channels) + list(donor_channels), list(by_chan))
    filled, no_donor = 0, []
    new_progs = []
    for c in channels:
        cid = c.get("id")
        if cid is None or by_chan.get(cid):
            continue
        donor = None
        for nm in names.get(cid, [cid]):
            for k in keys_for(nm):
                cands = [d for d in index.get(k, []) if d != cid]
                if cands:
                    donor = max(cands, key=lambda d: len(by_chan[d]))
                    break
            if donor:
                break
        if donor is None:
            no_donor.append(cid)
            continue
        for (st, sp, el) in by_chan[donor]:
            cp = copy.deepcopy(el)
            cp.set("channel", cid)
            new_progs.append((st, sp, cp))
        filled += 1
    progs.extend(new_progs)
    return filled, no_donor


def write_empty_report(channels, empty_ids, path):
    names = {}
    for c in channels:
        cid = c.get("id")
        if cid is not None:
            names[cid] = [dn.text.strip() for dn in c.findall("display-name") if dn.text and dn.text.strip()]
    with open(path, "w", encoding="utf-8") as f:
        for cid in sorted(empty_ids):
            f.write(" | ".join([cid] + names.get(cid, [])) + "\n")
    print(f"REPORTE: {len(empty_ids)} canales sin programación -> {path}", file=sys.stderr)


def prefix_donor_ids(channels, progs, prefix="~donor~"):
    """Evita choques de id entre fuentes principales y donantes."""
    for c in channels:
        if c.get("id") is not None:
            c.set("id", prefix + c.get("id"))
    for (_, _, el) in progs:
        el.set("channel", prefix + (el.get("channel") or ""))


def check_m3u(m3u, channels, kept, report_path):
    """Escribe los canales de tu lista M3U que siguen sin programación."""
    if m3u.startswith("http"):
        req = urllib.request.Request(m3u, headers={"User-Agent": "Mozilla/5.0"})
        text = urllib.request.urlopen(req, timeout=120).read().decode("utf-8", "replace")
    else:
        with open(m3u, encoding="utf-8", errors="replace") as f:
            text = f.read()
    with_data = set((el.get("channel") or "") for (_, _, el) in kept)
    _, index = _names_and_index(channels, with_data)
    missing, total = [], 0
    for ln in text.splitlines():
        if not ln.startswith("#EXTINF"):
            continue
        total += 1
        tid = re.search(r'tvg-id="([^"]*)"', ln)
        title = ln.split(",", 1)[1].strip() if "," in ln else ""
        ok = bool(tid and tid.group(1) and tid.group(1) in with_data)
        if not ok:
            ok = any(k in index for k in keys_for(title)) or (loose_key(title) in index)
        if not ok:
            missing.append(title)
    with open(report_path, "w", encoding="utf-8") as r:
        r.write("\n".join(missing) + "\n")
    print(f"LISTA M3U: {total} canales, {len(missing)} sin programación -> {report_path}", file=sys.stderr)


def build_roku_guide(channels, kept, now, path, keep_past, forward, desc_max):
    """Escribe la guía LIGERA para la app de Roku (ver --roku-output)."""
    lo = now - datetime.timedelta(hours=keep_past)
    hi = now + datetime.timedelta(hours=forward)
    by_chan = defaultdict(list)
    for (st, sp, el) in kept:
        if st is None or sp is None:
            continue
        if sp >= lo and st <= hi:
            by_chan[el.get("channel") or ""].append((st, el))

    names = {}
    for c in channels:
        cid = c.get("id")
        if cid is None:
            continue
        names[cid] = [dn.text.strip() for dn in c.findall("display-name") if dn.text and dn.text.strip()]

    def txt(el, tag, limit=None):
        t = el.find(tag)
        if t is None or not t.text:
            return ""
        v = " ".join(t.text.split())
        return v[:limit] if limit else v

    n_prog = 0
    with open(path, "w", encoding="utf-8") as out:
        out.write('<?xml version="1.0" encoding="UTF-8"?>\n<tv generator-info-name="jellyfimania-epg-roku">\n')
        for cid in sorted(by_chan):
            out.write("<channel id=%s>" % quoteattr(cid))
            for nm in (names.get(cid) or [cid]):
                out.write("<display-name>%s</display-name>" % escape(nm))
            out.write("</channel>\n")
        for cid in sorted(by_chan):
            for st, el in sorted(by_chan[cid], key=lambda t: t[0]):
                parts = ["<title>%s</title>" % escape(txt(el, "title", 300))]
                d = txt(el, "desc", desc_max)
                if d:
                    parts.append("<desc>%s</desc>" % escape(d))
                c = txt(el, "category", 100)
                if c:
                    parts.append("<category>%s</category>" % escape(c))
                out.write("<programme start=%s stop=%s channel=%s>%s</programme>\n" % (
                    quoteattr(el.get("start") or ""), quoteattr(el.get("stop") or ""),
                    quoteattr(cid), "".join(parts)))
                n_prog += 1
        out.write("</tv>\n")
    mb = os.path.getsize(path) / (1024 * 1024)
    print(f"GUÍA ROKU: {len(by_chan)} canales, {n_prog} programas, ventana "
          f"-{keep_past:g}h/+{forward:g}h -> {path} ({mb:.1f} MB)", file=sys.stderr)


def choose_window(progs, sizes, now, keep_past, forward, min_forward, max_bytes, base_bytes):
    """Elige la ventana hacia adelante más grande que cabe en max_bytes."""
    min_stop = now - datetime.timedelta(hours=keep_past)
    f = forward
    while True:
        max_start = now + datetime.timedelta(hours=f)
        total = base_bytes
        count = 0
        for (st, sp, el), sz in zip(progs, sizes):
            if st is None or sp is None or (sp >= min_stop and st <= max_start):
                total += sz
                count += 1
        if total <= max_bytes or f <= min_forward:
            return f, total, count
        f = max(min_forward, f - 12)


def main():
    ap = argparse.ArgumentParser(description="Combina guías XMLTV sin perder contenido.")
    ap.add_argument("inputs", nargs="+")
    ap.add_argument("--output", "-o", default="guide.xml")
    ap.add_argument("--gzip", action="store_true", help="Además generar una copia .gz")
    ap.add_argument("--keep-past-hours", type=float, default=2)
    ap.add_argument("--forward-hours", type=float, default=72)
    ap.add_argument("--min-forward-hours", type=float, default=24)
    ap.add_argument("--max-mb", type=float, default=95,
                    help="Tamaño máximo del guide.xml (GitHub bloquea >100 MB)")
    ap.add_argument("--donors", nargs="*", default=[],
                    help="Guías de APOYO: solo se usan para rellenar canales vacíos (ponerlas al final)")
    ap.add_argument("--empty-report", help="Escribir aquí los canales que siguen sin programación")
    ap.add_argument("--roku-output", help="Además escribir la guía LIGERA para Roku en este archivo (ej. guide_roku.xml)")
    ap.add_argument("--roku-forward-hours", type=float, default=40)
    ap.add_argument("--roku-keep-past-hours", type=float, default=1)
    ap.add_argument("--roku-desc-max", type=int, default=160)
    ap.add_argument("--no-fill", action="store_true",
                    help="No rellenar canales vacíos con programas de otro canal del mismo nombre")
    ap.add_argument("--m3u", help="Tu lista M3U (archivo o URL) para reportar canales sin programación")
    ap.add_argument("--report", default="missing_channels.txt")
    args = ap.parse_args()

    now = datetime.datetime.now(datetime.timezone.utc)
    channels, progs, redundant = collect(args.inputs)
    if not channels and not progs:
        print("::error::Ninguna fuente se pudo leer; no se escribe guide.xml.", file=sys.stderr)
        sys.exit(1)

    no_donor = []
    if not args.no_fill:
        d_channels, d_progs = [], []
        if args.donors:
            print("--- Fuentes donantes (solo para rellenar) ---", file=sys.stderr)
            d_channels, d_progs, _ = collect(args.donors)
            prefix_donor_ids(d_channels, d_progs)
        filled, no_donor = fill_empty_channels(channels, progs, now,
                                               args.keep_past_hours, args.forward_hours,
                                               d_channels, d_progs)
        print(f"RELLENO: {filled} canales vacíos recibieron programas de otro canal del "
              f"mismo nombre; {len(no_donor)} siguen sin ninguna fuente que los tenga.",
              file=sys.stderr)
        del d_channels, d_progs
    if args.empty_report:
        write_empty_report(channels, no_donor, args.empty_report)

    # Peso (en bytes) de cada elemento ya compactado
    ch_bytes = sum(len(ET.tostring(c, encoding="utf-8")) + 1 for c in channels)
    sizes = [len(ET.tostring(el, encoding="utf-8")) + 1 for (_, _, el) in progs]
    base = 120 + ch_bytes
    max_bytes = int(args.max_mb * 1024 * 1024)

    fwd, est, count = choose_window(progs, sizes, now, args.keep_past_hours,
                                    args.forward_hours, args.min_forward_hours,
                                    max_bytes, base)
    if fwd < args.forward_hours:
        print(f"::warning::La guía completa de {args.forward_hours:g} h pesaría más de "
              f"{args.max_mb:g} MB; se usó una ventana de {fwd:g} h para poder subirla a GitHub.",
              file=sys.stderr)

    min_stop = now - datetime.timedelta(hours=args.keep_past_hours)
    max_start = now + datetime.timedelta(hours=fwd)
    kept = [(st, sp, el) for (st, sp, el) in progs
            if st is None or sp is None or (sp >= min_stop and st <= max_start)]
    kept.sort(key=lambda t: (t[2].get("channel") or "", t[0] or _MIN))

    out = ET.Element("tv")
    out.set("generator-info-name", "jellyfimania-epg-merge")
    out.text = "\n"
    for c in channels:
        c.tail = "\n"
        out.append(c)
    for (_, _, el) in kept:
        el.tail = "\n"
        out.append(el)

    ET.ElementTree(out).write(args.output, encoding="utf-8", xml_declaration=True)
    print(f"TOTAL: {len(channels)} canales, {len(kept)} programas, ventana "
          f"-{args.kee
