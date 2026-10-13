#!/usr/bin/env python3
"""
make_fast_epg.py — regenera SOLO la guía rápida de la app (guide_roku_now.xml)
a partir de la guía completa ya publicada (guide_roku.xml), sin volver a
descargar ninguna fuente.

POR QUÉ EXISTE: la guía rápida solo trae las próximas horas (--roku-fast-hours,
6 por defecto) contadas desde el momento en que corre merge_epg.py. Con una
sola corrida al día, quedaba vencida unas 18 horas diarias y la app la
descartaba después de leerla (~30 s perdidos). Este script se corre cada pocas
horas (ver .github/workflows/update-fast-epg.yml) y la mantiene siempre vigente.

Uso:
    python3 scripts/make_fast_epg.py --input guide_roku.xml --output guide_roku_now.xml
"""
import argparse
import datetime
import os
import sys

from epg_core import _iter_top, parse_xmltv_time
from epg_extras import build_roku_guide


def main():
    ap = argparse.ArgumentParser(description="Regenera la guía rápida desde la guía completa.")
    ap.add_argument("--input", default="guide_roku.xml")
    ap.add_argument("--output", default="guide_roku_now.xml")
    ap.add_argument("--hours", type=float, default=6, help="Horas hacia adelante")
    ap.add_argument("--keep-past-hours", type=float, default=1)
    ap.add_argument("--desc-max", type=int, default=100)
    ap.add_argument("--min-programs", type=int, default=1000,
                    help="Si salen menos programas que esto, NO se escribe (se conserva la anterior)")
    args = ap.parse_args()

    if not os.path.exists(args.input) or os.path.getsize(args.input) == 0:
        print(f"::error::No existe o está vacío {args.input}; no se toca la guía rápida.", file=sys.stderr)
        sys.exit(1)

    now = datetime.datetime.now(datetime.timezone.utc)
    lo = now - datetime.timedelta(hours=args.keep_past_hours)
    hi = now + datetime.timedelta(hours=args.hours)

    channels, progs = [], []
    for el in _iter_top(args.input):
        if el.tag == "channel":
            channels.append(el)
        else:
            st = parse_xmltv_time(el.get("start"))
            sp = parse_xmltv_time(el.get("stop"))
            if st is None or sp is None:
                continue
            if sp >= lo and st <= hi:
                progs.append((st, sp, el))

    if len(progs) < args.min_programs:
        print(f"::error::Solo {len(progs)} programas en la ventana (mínimo {args.min_programs}): "
              f"la guía completa parece vencida; no se toca la guía rápida.", file=sys.stderr)
        sys.exit(1)

    tmp = args.output + ".tmp"
    build_roku_guide(channels, progs, now, tmp, args.keep_past_hours, args.hours, args.desc_max)
    os.replace(tmp, args.output)


if __name__ == "__main__":
    main()
