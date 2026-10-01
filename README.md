# Guía EPG combinada (6 fuentes)

Este repositorio arma **una sola guía XMLTV** todos los días, combinando:

1. **gatotv.com** (en vivo, scraping propio) — 139 canales mexicanos/latinos
   curados a mano en `channels/mis_canales_channels.xml`.
2. **m3u4u** (en vivo) — tu guía personal, bajada con tu URL secreta.
3. **epgshare01.online** (en vivo) — México + Argentina, Brasil (x2), Chile,
   Colombia, Costa Rica, República Dominicana, Ecuador, Panamá, Perú,
   El Salvador, Uruguay.
4. **iptv-epg.org** (en vivo) — Bolivia, Guatemala, Honduras, Nicaragua,
   Paraguay, Venezuela (los países que epgshare01 no cubre).
5. **EPGTalk** (en vivo) — 607 canales "Latino" adicionales (mayormente
   señales en español disponibles en EE. UU.).
6. **Generador genérico propio** — 65 canales temáticos/FAST (anime,
   películas por género, deportes de nicho) que no existen en ninguna
   fuente real; se les arma una programación de bloques de 6 horas
   siempre vigente, generada de nuevo cada corrida (nunca se vence).

El resultado se commitea de vuelta a este mismo repositorio todos los
días a las 08:00 UTC (2 AM Ciudad de México).

**Importante sobre el tamaño:** con las 6 fuentes combinadas, la guía sin
comprimir pesa más de 100 MB (el límite duro de GitHub para archivos). Por
eso el repositorio **solo guarda `guide.xml.gz`** (comprimido), no
`guide.xml` suelto. Tu reproductor/IPTV debe soportar URLs de EPG en
formato `.gz` (lo soportan la gran mayoría: TiviMate, Perfect Player, GSE
Smart IPTV, IPTV Smarters, Kodi PVR IPTV Simple Client; si tenés una app
propia hecha a medida, hay que confirmar que descomprime gzip al leer la
URL).

## Puesta en marcha (una sola vez)

1. **Creá un repositorio nuevo en GitHub**, público.
2. **Subí los 5 archivos de este repo** a la raíz (ver estructura abajo).
3. **Conseguí tu URL "en vivo" de m3u4u**: m3u4u.com → **EPGs → Manager**
   → flecha de descarga de tu lista → **Copy link**.
4. **Guardala como secret del repositorio**: **Settings → Secrets and
   variables → Actions → New repository secret**, nombre `M3U4U_EPG_URL`,
   valor la URL del paso 3.
5. **Activá el workflow**: pestaña **Actions** → "Actualizar guía EPG
   combinada" → **Run workflow**.
6. Cuando termine (puede tardar varios minutos, son 6 fuentes), tu URL
   final es:
https://raw.githubusercontent.com/TU_USUARIO/TU_REPO/main/guide.xml.gz

## Estructura de archivos

.github/workflows/update-epg.yml   → el workflow con las 6 fuentes
channels/mis_canales_channels.xml  → tus 139 canales de gatotv.com
scripts/merge_epg.py               → combina cualquier cantidad de guías XMLTV
scripts/generate_extra_epg.py      → genera la guía genérica de los 65 canales FAST
.gitignore

## Ajustes opcionales

- **Días de programación de gatotv.com**: flag `--days=3` en el paso
  "Descargar guía de gatotv.com para mis canales".
- **Horario de actualización**: línea `cron: "0 8 * * *"` (UTC).
- **Agregar más canales de gatotv.com**: sumá líneas al mismo formato en
  `channels/mis_canales_channels.xml`.
- **Agregar más países de epgshare01 o iptv-epg.org**: sumá una línea al
  `declare -A paises=(...)` del paso correspondiente — no hace falta
  tocar el resto del workflow (la combinación usa comodines `*.xml`).
- **Agregar más canales genéricos/FAST**: sumá tuplas `(id, nombre)` a la
  lista `CANALES` en `scripts/generate_extra_epg.py`.

## Si algo falla

- La pestaña **Actions** muestra el log completo de cada corrida.
- Si una fuente puntual falla (un país, un canal), no frena a las demás
  — seguís teniendo guía para el resto.
- Si el secret `M3U4U_EPG_URL` no está configurado, el paso de m3u4u
  corta con un mensaje explícito en vez de fallar oscuro.
