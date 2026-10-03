#!/usr/bin/env python3
"""
merge_epg.py — combina varias guías XMLTV en una sola, SIN perder contenido.

Uso:
    python3 scripts/merge_epg.py --output guide.xml [--gzip] a.xml b.xml [...]

El orden de los archivos es la prioridad: si el mismo canal (mismo id)
viene en varias fuentes, gana el PRIMERO (se avisa por stderr).

QUÉ HACE (y qué NO toca):
- <channel>: unión por id (el primero gana).
- <programme>: se conservan TODOS los programas de todas las fuentes,
  salvo los que son redundantes: un programa de una fuente de menor
  prioridad que se SOLAPA en horario con uno de una fuente anterior
  para el mismo canal (es el mismo programa repetido). Los huecos de
  la fuente principal se siguen llenando con las demás.
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
import datetime
import gzip
import re
import shutil
import sys
import xml.etree.ElementTree as ET

_TS_RE = re.compile(r"^(\d{4})(\d{2})(\d{2})(\d{2})(\d{2})(\d{2})\s*([+-]\d{4})?$")
_MIN = datetime.datetime.min.replace(tzinfo=datetime.timezone.utc)


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
    args = ap.parse_args()

    now = datetime.datetime.now(datetime.timezone.utc)
    channels, progs, redundant = collect(args.inputs)

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
          f"-{args.keep_past_hours:g}h/+{fwd:g}h, {redundant} repetidos omitidos", file=sys.stderr)

    if args.gzip:
        with open(args.output, "rb") as fi, gzip.open(args.output + ".gz", "wb", compresslevel=9) as fo:
            shutil.copyfileobj(fi, fo)

    import os
    mb = os.path.getsize(args.output) / (1024 * 1024)
    print(f"Escrito {args.output}: {mb:.1f} MB", file=sys.stderr)
    if mb > 99:
        print("::error::guide.xml supera 99 MB; GitHub rechazará el push.", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
