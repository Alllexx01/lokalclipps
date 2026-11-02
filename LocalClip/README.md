# LokalClipps

LokalClipps ist eine selbst gehostete Web-App, die lange Videos transkribiert,
interessante Passagen auswählt und daraus vertikale Short-Clips erstellt.

## Aktueller Funktionsumfang

- Video-Upload und Import öffentlicher Video-URLs
- öffentlicher Video-Finder für Gaming, Podcasts, Reactions und Comedy
- Finder-Score nach Titel, Videolänge und Reichweite
- lokales Speech-to-Text mit `faster-whisper`
- automatische Highlight-Auswahl nach Hook, Thema, Tempo und Sprechpausen
- Erkennung visueller Szenenwechsel und daran ausgerichtete Clipgrenzen
- Clip-Potenzial-Wertung von 0 bis 100 mit Empfehlungen
- 9:16-Export mit FFmpeg
- lesbare Untertitelgruppen und wortgenaues Karaoke-Highlighting
- Untertitelstile, Position, Größe und Farben nachträglich anpassbar
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

Deployment auf dem Server:

```bash
cd /opt/lokalclipps/LocalClip
git pull --ff-only
docker compose up -d --build
```

`render.yaml` ist nur eine frühere, derzeit nicht verwendete Alternative.

## Cookies und Videoquellen

Der Video-Finder arbeitet ohne Benutzer-Cookies. Manche Plattformen verlangen
beim eigentlichen Download trotzdem eine Anmeldung. Eine hochgeladene
`cookies.txt` wird nur für den jeweiligen Download benutzt und anschließend
auch im Fehlerfall gelöscht. Plattform-Sperren werden nicht umgangen.

Es dürfen nur eigene Videos oder Inhalte mit entsprechender Erlaubnis verarbeitet
und veröffentlicht werden.

## Tests

```powershell
python -m pytest tests -q
python -m py_compile app.py core.py downloader.py tiktok.py
node --check static/app.js
```
