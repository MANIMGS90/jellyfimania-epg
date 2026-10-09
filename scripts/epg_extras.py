#!/usr/bin/env python3
"""
epg_extras.py — piezas de merge_epg.py: relleno de canales vacíos, relleno
de huecos dentro de la programación, reporte de canales sin programación,
revisión de una lista M3U y la guía ligera para Roku.
"""
import copy
import datetime
import os
import re
import sys
import urllib.request
import xml.etree.ElementTree as ET
from collections import defaultdict
from xml.sax.saxutils import escape, quoteattr

from epg_core import keys_for, loose_key

# Un hueco más corto que esto se ignora (salvo que tape el momento "ahora")
GAP_MIN = datetime.timedelta(minutes=10)


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


def _placeholder(cid, start, stop, title):
    p = ET.Element("programme", start=start.strftime("%Y%m%d%H%M%S +0000"),
                   stop=stop.strftime("%Y%m%d%H%M%S +0000"), channel=cid)
    t = ET.SubElement(p, "title", lang="es")
    t.text = title
    return p


def fill_gaps(progs, channel_ids, now, keep_past, forward, title, max_hours, fill_empty=False):
    """Rellena los HUECOS de la programación de cada canal dentro de la
    ventana con bloques de relleno (un bloque por hueco, de hasta max_hours).
    Así no quedan espacios en blanco y siempre hay un programa en 'ahora'.
    Por defecto solo se tocan canales que ya tienen algún programa; con
    fill_empty=True también los que no tienen ninguno (ver merge_epg.py).
    Devuelve cuántos bloques se agregaron."""
    lo = now - datetime.timedelta(hours=keep_past)
    hi = now + datetime.timedelta(hours=forward)
    step = datetime.timedelta(hours=max_hours)

    spans = defaultdict(list)
    for (st, sp, el) in progs:
        if st is None or sp is None:
            continue
        if sp > lo and st < hi:
            spans[el.get("channel") or ""].append((st, sp))

    targets = set(c for c in spans if c)
    if fill_empty:
        targets |= set(c for c in channel_ids if c)

    added = 0
    for cid in sorted(targets):
        gaps = []
        cursor = lo
        for st, sp in sorted(spans.get(cid, [])):
            if st > cursor:
                gaps.append((cursor, st))
            if sp > cursor:
                cursor = sp
        if cursor < hi:
            gaps.append((cursor, hi))
        for g0, g1 in gaps:
            t = g0
            while t < g1:
                t2 = min(g1, t + step)
                covers_now = t <= now < t2
                if (t2 - t) >= GAP_MIN or covers_now:
                    progs.append((t, t2, _placeholder(cid, t, t2, title)))
                    added += 1
                t = t2
    return added


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
