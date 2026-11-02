# LokalClipps

LokalClipps ist eine selbst gehostete Web-App, die lange Videos transkribiert,
interessante Passagen auswählt und daraus vertikale Short-Clips erstellt.

## Aktueller Funktionsumfang

- Video-Upload und Import öffentlicher Video-URLs
- öffentlicher Video-Finder für Gaming, Podcasts, Reactions und Comedy: mehrere Suchanfragen
  parallel, Filter nach Länge, Sortierung (Eignung, Aufrufe, Länge), "Mehr laden",
  höchstens zwei Videos pro Kanal in der Spitzengruppe, "Schon geclippt"-Kennzeichnung,
  Ansehen-Link und Mehrfachauswahl, die mehrere Videos nacheinander in die Warteschlange stellt
- Finder-Score nach Titel, Videolänge, Reichweite und Aktualität; Shorts, Livestreams und
  Zusammenschnitte werden abgewertet
- lokales Speech-to-Text mit `faster-whisper`
- automatische Highlight-Auswahl nach Hook, Thema, Tempo und Sprechpausen
- Erkennung visueller Szenenwechsel und daran ausgerichtete Clipgrenzen
- Clip-Potenzial-Wertung von 0 bis 100 mit Empfehlungen
- 9:16-Export mit FFmpeg
- Smart-Reframing: der Bildausschnitt folgt dem Sprecher (kostenlose Gesichtserkennung
  mit YuNet/OpenCV, komplett offline). Ohne erkennbares Gesicht, etwa bei Gaming,
  bleibt es beim Layout mit unscharfem Hintergrund
- Bildausschnitt wählbar: Sprecher folgen, feste Bildmitte oder ganzes Bild mit unscharfem
  Hintergrund
- Zoom nur nach Wahl: aus (Standard), dezent (langsame Annäherung) oder betont (kurzer Zoom auf starke Sätze); Untertitel bleiben stehen
- Clipgrenzen wahlweise an Bildschnitten ausrichten oder nur nach Sprache schneiden
- optional: lange Sprechpausen und Leerlauf automatisch herausschneiden
- optional: stärkster Satz als Titel-Einblendung in den ersten Sekunden
- alle Schnitt-Optionen lassen sich pro Clip nachträglich ändern ("Clip bearbeiten" -> neu rendern)
- Warteschlange: standardmäßig läuft nur ein Auftrag gleichzeitig (`LOCALCLIP_MAX_JOBS`)
- lesbare Untertitelgruppen und wortgenaues Karaoke-Highlighting
- Untertitelstile, Position, Größe und Farben nachträglich anpassbar
- fertige Bildunterschriften: pro Clip drei Varianten (Frage an die Community, Teaser, kurz) mit
  wenigen passenden Hashtags aus dem Clip-Inhalt, bearbeitbar und kopierbar, auch im TikTok-Dialog
- Teilen- und TikTok-Oberfläche
- automatische Projektbereinigung nach einstellbarer Aufbewahrungszeit
- HTTPS und langlebiger, signierter Login-Cookie

## Lokaler Start unter Windows

Voraussetzungen: Python 3.12 und FFmpeg im `PATH`.

```powershell
python -m pip install -r requirements.txt
python app.py
```

Danach ist die App unter <http://127.0.0.1:8765> erreichbar. Alternativ kann
`start.bat` verwendet werden.

## Produktion

Die aktive Produktion läuft per Docker Compose auf einem Hetzner-Server:

- Webseite: <https://2-29-154-156.sslip.io/>
- Reverse Proxy und TLS: Caddy
- App-Port im Container-Netz: `10000`
- persistente Projekte: Docker-Volume `lokalclipps_data`
- persistenter Whisper-Cache: Docker-Volume `model_cache`

Benötigte Variablen in der nicht versionierten `.env`:

```text
LOCALCLIP_DOMAIN=...
LOCALCLIP_USERNAME=...
LOCALCLIP_PASSWORD=...
```

Optional: `LOCALCLIP_SECRET_KEY` (Fernet-Schlüssel für gespeicherte TikTok-Zugangsdaten;
ohne Angabe wird einmalig ein Schlüssel im Daten-Volume erzeugt) und `LOCALCLIP_MAX_JOBS`
(gleichzeitige Aufträge, Standard 1). Die TikTok-Rückkehradresse wird aus
`LOCALCLIP_DOMAIN` gebildet und muss genau so in TikTok for Developers eingetragen sein.

Deployment auf dem Server:

```bash
cd /opt/lokalclipps/LocalClip
git pull --ff-only
docker compose up -d --build
```

`render.yaml` ist nur eine frühere, derzeit nicht verwendete Alternative.

## Videoquellen am Handy

Der Video-Finder und öffentliche Links arbeiten ohne Benutzer-Cookies. Falls eine
Plattform den automatischen Abruf blockiert, kann die Videodatei direkt über die
große Dateiauswahl hochgeladen werden. LocalClip fordert keine YouTube-Sitzungsdatei
und keine YouTube-Zugangsdaten an. Plattform-Sperren werden nicht umgangen.

Es dürfen nur eigene Videos oder Inhalte mit entsprechender Erlaubnis verarbeitet
und veröffentlicht werden.

## Tests

```powershell
python -m pytest tests -q
python -m py_compile app.py core.py reframe.py downloader.py tiktok.py
node --check static/app.js
```
