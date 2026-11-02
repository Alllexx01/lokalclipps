# LokalClipps – Übergabe an den nächsten Entwickler

Stand: 2. November 2026

## Kurzfassung

LokalClipps ist produktiv online und wird aus diesem Git-Repository auf einen
Hetzner-CPX12-Server ausgeliefert. Die App findet öffentliche Unterhaltungs-Videos,
transkribiert hochgeladene oder importierte Quellen, bewertet deren Clip-Potenzial
und rendert automatische 9:16-Clips mit anpassbaren Untertiteln.

## Relevante Orte

- Git-Arbeitsverzeichnis: `C:\Users\alexs\Documents\ChatGPT\CLIPPING`
- eigentliche Anwendung: `C:\Users\alexs\Documents\ChatGPT\CLIPPING\LocalClip`
- GitHub: `https://github.com/Alllexx01/lokalclipps`
- Produktion: `https://2-29-154-156.sslip.io/`
- Server-IP: `2.29.154.156`
- Server-Checkout: `/opt/lokalclipps`
- lokale, ignorierte Zugangsdaten: `LocalClip\.deploy\`

Den gesamten Ordner `CLIPPING` als Projekt öffnen. Nicht nur einzelne Python-Dateien
kopieren, weil Git, Docker-Konfiguration, Tests und Übergabedokumente zusammengehören.

## Architektur

- `LocalClip/app.py`: Starlette-Routen, Jobs, Login, Aufbewahrung und TikTok-Endpunkte
- `LocalClip/core.py`: Transkription, Szenenerkennung, Highlight-Scoring, Untertitel und Rendering
- `LocalClip/downloader.py`: URL-Sicherheit, yt-dlp-Download und öffentlicher Video-Finder
- `LocalClip/tiktok.py`: TikTok-Konfiguration und Publishing
- `LocalClip/static/`: responsive Weboberfläche
- `LocalClip/tests/`: Kern- und Downloader-Tests
- `LocalClip/Dockerfile`: Python/FFmpeg-Container
- `LocalClip/docker-compose.yml`: App, Caddy, Daten- und Cache-Volumes
- `LocalClip/Caddyfile`: HTTPS-Reverse-Proxy

## Bereits umgesetzt

1. Docker-/Hetzner-Deployment mit kostenlosem `sslip.io`-Namen und HTTPS.
2. Login-Formular mit sicher signiertem, ein Jahr gültigem HttpOnly-Cookie.
3. Verbesserte Whisper-VAD-Parameter und wortgenaue Zeitstempel.
4. Szenenerkennung mit FFmpeg und an Bildwechseln ausgerichtete Clipgrenzen.
5. Ausgeglichene Untertitelkarten statt starrer Vier-Wort-Blöcke.
6. Clip-Potenzial-Analyse mit Wert, Stärken und Empfehlungen.
7. Video-Finder ohne Cookies mit Finder-Score.
8. Erlaubte Finder-Kategorien: Alle, Gaming, Podcasts, Reactions und Comedy.
   Sport, News und Wissen wurden auf ausdrücklichen Wunsch entfernt.
9. Pro-Video-Upload von `cookies.txt`; die Datei wird danach auch bei Fehlern gelöscht.
10. Desktop-Verknüpfung öffnet die produktive Webseite.

## Sicherheit und Grenzen

- Niemals `LocalClip/.deploy`, `.env`, private SSH-Schlüssel, Login-Passwörter,
  TikTok-Tokens oder `cookies.txt` committen oder in Antworten veröffentlichen.
- Keine Plattform-Sperren, Bot-Prüfungen oder Anmeldeschutzmechanismen umgehen.
- Video-Finder-Ergebnisse sind nur Vorschläge. Nutzer müssen die Veröffentlichungsrechte besitzen.
- Keine weiteren kostenpflichtigen Dienste oder Serveroptionen ohne ausdrückliche Zustimmung buchen.
- Der öffentliche `/health`-Endpunkt ist absichtlich ohne Login erreichbar.

## Deployment

Nach erfolgreichen lokalen Prüfungen:

```powershell
git add <gezielte Dateien>
git commit -m "Beschreibung"
git push origin main
```

Danach über den lokalen Schlüssel aus `LocalClip/.deploy` per SSH aktualisieren:

```bash
git -C /opt/lokalclipps pull --ff-only
cd /opt/lokalclipps/LocalClip
docker compose up -d --build
docker compose ps
```

Abschließend `https://2-29-154-156.sslip.io/health`, Login und die geänderte
Funktion testen. Die Produktions-`.env` nicht überschreiben.

## Bekannte offene Punkte

- `tiktok.py` enthält Windows-spezifische DPAPI-Nutzung (`ctypes.windll`). Das kann
  im Linux-Container beim Speichern von TikTok-Zugangsdaten scheitern und sollte
  durch plattformunabhängige, serverseitige Secret-Verschlüsselung ersetzt werden.
- Öffentliche yt-dlp-Suche funktioniert aktuell, kann aber bei Änderungen der
  Plattform ausfallen. Fehler müssen sauber angezeigt werden; keine Umgehung bauen.
- Ein kompletter realer End-to-End-Test mit mehreren langen Beispielvideos sollte
  Clipgrenzen, Untertitel und RAM-Verbrauch auf dem 2-GB-Server vergleichen.
- Optional: Job-Warteschlange einführen, damit nicht mehrere Whisper-Prozesse
  gleichzeitig den kleinen Server überlasten.
- Optional: CSRF-Schutz für schreibende Web-Endpunkte ergänzen.

## Letzte wichtige Commits

- `f608e2a` – Entertainment Video-Finder
- `fd13237` – Clip-Potenzial-Analyse
- `2e4f1bd` – Szenenerkennung und Untertitel-Timing
- `1023bab` – langlebiger Cookie-Login
- `b2434bb` – HTTPS-Docker-Stack
