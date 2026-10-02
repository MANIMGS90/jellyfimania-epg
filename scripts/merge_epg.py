#!/usr/bin/env python3
"""
merge_epg.py — combina varias guías XMLTV en una sola.

Uso:
    python3 scripts/merge_epg.py --output guide.xml [--gzip] archivo1.xml archivo2.xml [...]

Se pueden pasar dos o más archivos XMLTV (raíz <tv>, con <channel> y
<programme> adentro). El orden en que se pasan importa: si el mismo
canal (mismo atributo id) aparece en más de un archivo, gana el
PRIMERO que se haya pasado por línea de comandos, y se avisa por
stderr — así, si el día de mañana se suma una tercera fuente y hay
una colisión de verdad, se nota en el log de la Action en vez de
perderse en silencio.

Reglas de combinado:
- <channel>: se unen por su atributo id (unión simple, sin pisarse).
- <programme>: se concatenan todos los de todos los archivos y se
  ordenan por (channel, start) al final, solo para que el XML
  combinado quede prolijo y fácil de inspeccionar a simple vista —
  el orden no le importa al estándar XMLTV ni a los reproductores.

RECORTE POR VENTANA HORARIA (clave para el tamaño del archivo):
Varias de las fuentes públicas que se combinan acá (epgshare01,
iptv-epg.org, EPGTalk) traen 5-7+ DÍAS de programación por canal,
aunque el reproductor (el canal de Roku de JELLYFIMANIA) solo usa un
máximo de 48 horas hacia adelante. Guardar esos días de más en el
archivo combinado no sirve para nada y es la razón por la que
`guide.xml` sin comprimir llegó a pesar más de 246 MB (el límite de
GitHub es 100 MB). Por eso, al combinar, se descartan los <programme>
que terminaron hace más de `--keep-past-hours` o que empiezan más
allá de `--forward-hours` desde el momento en que corre el workflow
— alineado con lo que el Roku realmente consume, para que el archivo
vuelva a pesar lo que tiene que pesar y se pueda commitear sin
comprimir.
"""
import argparse
import datetime
import gzip
import re
import sys
import xml.etree.ElementTree as ET

# Formato típico: "20260930080000 +0000" o "20260930080000 -0600"
# (con o sin espacio antes del offset). Si no hay offset, se asume UTC.
_TS_RE = re.compile(r"^(\d{4})(\d{2})(\d{2})(\d{2})(\d{2})(\d{2})\s*([+-]\d{4})?$")


def parse_xmltv_time(ts):
    """Devuelve un datetime aware en UTC, o None si no se pudo parsear."""
    if not ts:
        return None
    m = _TS_RE.match(ts.strip())
    if not m:
        return None
    year, month, day, hour, minute, second, offset = m.groups()
    try:
        dt = datetime.datetime(
            int(year), int(month), int(day), int(hour), int(minute), int(second),
            tzinfo=datetime.timezone.utc,
        )
    except ValueError:
        return None
    if offset:
        sign = 1 if offset[0] == "+" else -1
        oh, om = int(offset[1:3]), int(offset[3:5])
        dt = dt - sign * datetime.timedelta(hours=oh, minutes=om)
    return dt


def load_tv_root(path):
    try:
        tree = ET.parse(path)
    except ET.ParseError as e:
        print(f"AVISO: no se pudo parsear {path} como XML válido ({e}); se omite.", file=sys.stderr)
        return None
    root = tree.getroot()
    if root.tag != "tv":
        print(f"AVISO: {path} no tiene <tv> como raíz (tiene <{root.tag}>); se omite.", file=sys.stderr)
        return None
    return root


def merge(paths, keep_past_hours, forward_hours):
    combined = ET.Element("tv")
    combined.set("generator-info-name", "jellyfimania-epg-merge")

    now = datetime.datetime.now(datetime.timezone.utc)
    min_stop = now - datetime.timedelta(hours=keep_past_hours)
    max_start = now + datetime.timedelta(hours=forward_hours)

    seen_channel_ids = {}
    channel_count = 0
    programme_elems = []
    dropped_out_of_window = 0
    dropped_unparseable_kept = 0

    for path in paths:
        root = load_tv_root(path)
        if root is None:
            continue

        local_channels = 0
        local_programmes = 0
        local_kept = 0
        for child in root:
            if child.tag == "channel":
                cid = child.get("id")
                if cid is None:
                    continue
                if cid in seen_channel_ids:
                    print(
                        f'AVISO: el canal id="{cid}" ya había venido de {seen_channel_ids[cid]}; '
                        f"se ignora la versión de {path}.",
                        file=sys.stderr,
                    )
                    continue
                seen_channel_ids[cid] = path
                combined.append(child)
                local_channels += 1
                channel_count += 1
            elif child.tag == "programme":
                local_programmes += 1
                start = parse_xmltv_time(child.get("start"))
                stop = parse_xmltv_time(child.get("stop"))
                if start is None or stop is None:
                    # No se pudo interpretar la fecha: se conserva por las
                    # dudas (mejor de más que perder un programa válido
                    # por un formato de fecha raro de alguna fuente).
                    dropped_unparseable_kept += 1
                    programme_elems.append(child)
                    local_kept += 1
                    continue
                if stop < min_stop or start > max_start:
                    dropped_out_of_window += 1
                    continue
                programme_elems.append(child)
                local_kept += 1

        print(
            f"{path}: {local_channels} canales, {local_programmes} programas "
            f"({local_kept} dentro de la ventana, se guardan)",
            file=sys.stderr,
        )

    def sort_key(p):
        return (p.get("channel") or "", p.get("start") or "")

    programme_elems.sort(key=sort_key)
    for p in programme_elems:
        combined.append(p)

    print(
        f"TOTAL combinado: {channel_count} canales, {len(programme_elems)} programas "
        f"(descartados {dropped_out_of_window} fuera de ventana "
        f"[-{keep_past_hours}h, +{forward_hours}h]; {dropped_unparseable_kept} con fecha "
        "rara se conservaron igual)",
        file=sys.stderr,
    )
    return combined


def write_output(root, output_path, also_gzip):
    tree = ET.ElementTree(root)
    try:
        ET.indent(tree, space="  ")  # Python 3.9+, prolijo pero no obligatorio
    except AttributeError:
        pass
    tree.write(output_path, encoding="utf-8", xml_declaration=True)
    print(f"Escrito {output_path}", file=sys.stderr)

    if also_gzip:
        gz_path = output_path + ".gz"
        with open(output_path, "rb") as f_in, gzip.open(gz_path, "wb") as f_out:
            f_out.writelines(f_in)
        print(f"Escrito {gz_path}", file=sys.stderr)


def main():
    parser = argparse.ArgumentParser(description="Combina varias guías XMLTV en una sola.")
    parser.add_argument("inputs", nargs="+", help="Archivos XMLTV de entrada, en orden de prioridad")
    parser.add_argument("--output", "-o", default="guide.xml", help="Archivo XMLTV combinado de salida")
    parser.add_argument("--gzip", action="store_true", help="Además generar una copia .gz")
    parser.add_argument(
        "--keep-past-hours", type=float, default=6,
        help="Conservar programas que terminaron hace como máximo esta cantidad de horas (default: 6)",
    )
    parser.add_argument(
        "--forward-hours", type=float, default=54,
        help="Conservar programas que empiezan hasta esta cantidad de horas hacia adelante (default: 54)",
    )
    args = parser.parse_args()

    combined = merge(args.inputs, args.keep_past_hours, args.forward_hours)
    write_output(combined, args.output, args.gzip)


if __name__ == "__main__":
    main()
