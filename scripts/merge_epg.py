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
"""
import argparse
import gzip
import sys
import xml.etree.ElementTree as ET


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


def merge(paths):
    combined = ET.Element("tv")
    combined.set("generator-info-name", "jellyfimania-epg-merge")

    seen_channel_ids = {}
    channel_count = 0
    programme_elems = []

    for path in paths:
        root = load_tv_root(path)
        if root is None:
            continue

        local_channels = 0
        local_programmes = 0
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
                programme_elems.append(child)
                local_programmes += 1

        print(f"{path}: {local_channels} canales, {local_programmes} programas", file=sys.stderr)

    def sort_key(p):
        return (p.get("channel") or "", p.get("start") or "")

    programme_elems.sort(key=sort_key)
    for p in programme_elems:
        combined.append(p)

    print(
        f"TOTAL combinado: {channel_count} canales, {len(programme_elems)} programas",
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
    args = parser.parse_args()

    combined = merge(args.inputs)
    write_output(combined, args.output, args.gzip)


if __name__ == "__main__":
    main()
