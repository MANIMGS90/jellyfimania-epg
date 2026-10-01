#!/usr/bin/env python3
"""
generate_extra_epg.py — genera una guía XMLTV genérica para canales
"FAST" / temáticos que no tienen fuente de programación real en ninguna
de las otras fuentes (gatotv.com, m3u4u, epgshare01, iptv-epg.org,
EPGTalk): canales de anime, de películas por género, deportivos de
nicho, etc. que transmiten contenido continuo sin una grilla horaria
publicada en ningún lado.

En vez de mantener un archivo fijo con fechas (que se vence), este
script arma bloques genéricos de 6 horas (madrugada/mañana/tarde/noche)
empezando HOY, para los próximos N días — así nunca queda vencido,
sin depender de ninguna fuente externa.

Uso:
    python3 scripts/generate_extra_epg.py --output extra_epg.xml --days 3
"""
import argparse
import datetime
import xml.etree.ElementTree as ET

# (xmltv_id, nombre a mostrar) — canales sin fuente de programación real
CANALES = [
    ("316772", "LOCOMOTION"),
    ("316782", "BITME"),
    ("316802", "RUN:TIME"),
    ("316803", "RUN:TIME FAMILIA"),
    ("316804", "RUN:TIME THRILLER+ TERROR"),
    ("316805", "RUN:TIME COMEDIA"),
    ("316812", "M★P"),
    ("316827", "DHE"),
    ("316870", "TELERITMO"),
    ("316876", "PEQUERADIO"),
    ("317098", "NUEVE"),
    ("317204", "MAGIC KIDS"),
    ("317206", "ANIME STATION"),
    ("317241", "ANIME ALL DAY"),
    ("317470", "DRAIKO TV"),
    ("317471", "GOGOPLAY+"),
    ("317473", "TALTALTV"),
    ("317474", "TALTALTV2"),
    ("317481", "ENERGEEK"),
    ("317482", "ENERGEEK FAN"),
    ("317483", "CANADE"),
    ("317584", "LIGA1 MAX"),
    ("317596", "DSPORTS"),
    ("317597", "DSPORTS 2"),
    ("317598", "WIN+ SPORTS HD"),
    ("317607", "DAZNF1"),
    ("322312", "SIPSE TVCUN 8.1"),
    ("322328", "CONECTA TV"),
    ("322363", "TELENOVELAS"),
    ("322407", "TRACE LATINA"),
    ("322430", "AZ CLICK"),
    ("322431", "ADULT SWIN"),
    ("327148", "ANIME VISION"),
    ("327149", "ANIME VISION CLASSICS"),
    ("327156", "DRAGON BALL Z"),
    ("327157", "TOM AND JERRY"),
    ("327159", "LOS PITUFOS"),
    ("327160", "LA PANTERA ROSA"),
    ("327161", "LOCOMOTION2"),
    ("327164", "RETRO MAGICO"),
    ("327165", "MAX ANIME"),
    ("329882", "FMH MOVIES"),
    ("332556", "XTREMA CARTOONS"),
    ("332578", "FMHKIDS"),
    ("332583", "DISNEY LIVE ACTION"),
    ("333529", "ALCANCETV HD"),
    ("333602", "LMS|MÉXICO"),
    ("333603", "X MEN"),
    ("333604", "ZKIDS"),
    ("333606", "GAME TOON HD"),
    ("333607", "MAGIC KIDS 2"),
    ("333615", "CINE PREMIUM"),
    ("333616", "FMH FAMILY"),
    ("333617", "MC"),
    ("333621", "ANIMASH"),
    ("333735", "HOMBRE ARAÑA"),
    ("333833", "Yu-Gi-Oh!"),
    ("334008", "JHONNY BRAVO"),
    ("334009", "JONNY QUEST"),
    ("334010", "KUNG FU PANDA"),
    ("334029", "SIRENITA"),
    ("334030", "CHICAS SUPERPODEROSAS"),
    ("334031", "HORA DE AVENTURA"),
    ("334135", "CABALLEROS DEL ZODIACO"),
    ("334151", "RANMA 1/2"),
]

BLOQUES = [
    (0, "madrugada"),
    (6, "mañana"),
    (12, "tarde"),
    (18, "noche"),
]


def build(days):
    tv = ET.Element("tv")
    tv.set("generator-info-name", "jellyfimania-epg-merge-generico")

    for cid, nombre in CANALES:
        ch = ET.SubElement(tv, "channel", id=cid)
        dn = ET.SubElement(ch, "display-name")
        dn.text = nombre

    hoy = datetime.datetime.now(datetime.timezone.utc).replace(
        hour=0, minute=0, second=0, microsecond=0
    )

    for cid, nombre in CANALES:
        for d in range(days):
            dia = hoy + datetime.timedelta(days=d)
            for hora, momento in BLOQUES:
                start = dia + datetime.timedelta(hours=hora)
                stop = start + datetime.timedelta(hours=6)
                p = ET.SubElement(
                    tv,
                    "programme",
                    start=start.strftime("%Y%m%d%H%M%S +0000"),
                    stop=stop.strftime("%Y%m%d%H%M%S +0000"),
                    channel=cid,
                )
                title = ET.SubElement(p, "title", lang="es")
                title.text = nombre
                desc = ET.SubElement(p, "desc", lang="es")
                desc.text = (
                    f"Programación continua ({momento}) — canal de "
                    "contenido continuo, sin horario fijo publicado."
                )
    return tv


def main():
    parser = argparse.ArgumentParser(
        description="Genera una guía XMLTV genérica (siempre vigente) para canales sin fuente real."
    )
    parser.add_argument("--output", "-o", default="extra_epg.xml")
    parser.add_argument("--days", type=int, default=3)
    args = parser.parse_args()

    tv = build(args.days)
    tree = ET.ElementTree(tv)
    try:
        ET.indent(tree, space="  ")
    except AttributeError:
        pass
    tree.write(args.output, encoding="utf-8", xml_declaration=True)
    print(f"Escrito {args.output}: {len(CANALES)} canales, {len(CANALES) * args.days * 4} programas")


if __name__ == "__main__":
    main()
