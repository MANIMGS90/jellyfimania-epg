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
- NUEVO — LECTURA SIN LLENAR LA MEMORIA: los archivos se leen "al vuelo"
  (por pedazos) y se descarta enseguida todo lo que queda fuera de la
  ventana horaria. Así se pueden usar guías enormes (Plex, Samsung TV
  Plus, etc.) sin que la Action se quede sin memoria ni se trabe.
- NUEVO — FUENTES DONANTES (--donors a.xml b.xml ...): guías de APOYO que
  solo sirven para rellenar canales que sigan vacíos. Sus canales NO se
  agregan a la salida (así la guía no engorda); de cada donante solo se
  guardan los canales cuyo nombre coincide con algún canal vacío tuyo.
  Ponlas AL FINAL del comando.
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
- NUEVO — GUÍA RÁPIDA PARA ROKU (--roku-fast-output guide_roku_now.xml):
  una copia MUY chica con solo las próximas horas (--roku-fast-hours, 6 por
  default). La app la lee primero y la guía aparece en segundos; mientras
  tanto sigue leyendo la guía completa de ~40 horas.
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
import os
import shutil
import sys
import xml.etree.ElementTree as ET

from epg_core import collect, keys_for, read_donors
from epg_extras import (build_roku_guide, check_m3u, fill_empty_channels,
                        write_empty_report)

_MIN = datetime.datetime.min.replace(tzinfo=datetime.timezone.utc)


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
    ap.add_argument("--roku-fast-output", help="Además escribir la guía RÁPIDA (solo próximas horas), ej. guide_roku_now.xml")
    ap.add_argument("--roku-fast-hours", type=float, default=6)
    ap.add_argument("--roku-forward-hours", type=float, default=40)
    ap.add_argument("--roku-keep-past-hours", type=float, default=1)
    ap.add_argument("--roku-desc-max", type=int, default=160)
    ap.add_argument("--no-fill", action="store_true",
                    help="No rellenar canales vacíos con programas de otro canal del mismo nombre")
    ap.add_argument("--m3u", help="Tu lista M3U (archivo o URL) para reportar canales sin programación")
    ap.add_argument("--report", default="missing_channels.txt")
    args = ap.parse_args()

    now = datetime.datetime.now(datetime.timezone.utc)
    channels, progs, redundant = collect(args.inputs, now, args.keep_past_hours, args.forward_hours)
    if not channels and not progs:
        print("::error::Ninguna fuente se pudo leer; no se escribe guide.xml.", file=sys.stderr)
        sys.exit(1)

    no_donor = []
    if not args.no_fill:
        d_channels, d_progs = [], []
        if args.donors:
            print("--- Fuentes donantes (solo para rellenar) ---", file=sys.stderr)
            with_data = set((el.get("channel") or "") for (_, _, el) in progs)
            wanted_keys = set()
            for c in channels:
                cid = c.get("id")
                if cid is None or cid in with_data:
                    continue
                nms = [dn.text.strip() for dn in c.findall("display-name") if dn.text and dn.text.strip()]
                for nm in (nms or [cid]):
                    wanted_keys.update(keys_for(nm))
            d_channels, d_progs = read_donors(args.donors, now, args.keep_past_hours,
                                              args.forward_hours, wanted_keys)
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
          f"-{args.keep_past_hours:g}h/+{fwd:g}h, {redundant} repetidos omitidos", file=sys.stderr)

    if args.gzip:
        with open(args.output, "rb") as fi, gzip.open(args.output + ".gz", "wb", compresslevel=9) as fo:
            shutil.copyfileobj(fi, fo)

    mb = os.path.getsize(args.output) / (1024 * 1024)
    print(f"Escrito {args.output}: {mb:.1f} MB", file=sys.stderr)

    if args.roku_output:
        build_roku_guide(channels, progs, now, args.roku_output, args.roku_keep_past_hours,
                         args.roku_forward_hours, args.roku_desc_max)

    if args.roku_fast_output:
        build_roku_guide(channels, progs, now, args.roku_fast_output, args.roku_keep_past_hours,
                         args.roku_fast_hours, 100)

    if args.m3u:
        try:
            check_m3u(args.m3u, channels, kept, args.report)
        except Exception as e:  # el reporte es opcional: nunca debe tumbar la guía
            print(f"AVISO: no se pudo revisar la lista M3U ({e}).", file=sys.stderr)

    if mb > 99:
        print("::error::guide.xml supera 99 MB; GitHub rechazará el push.", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
