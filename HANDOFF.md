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
- `LocalClip/reframe.py`: kostenloses Smart-Reframing (YuNet-Gesichtserkennung, Kamerafahrt, ffmpeg-Ausdruck)
- `LocalClip/models/`: YuNet-Modell `face_detection_yunet_2023mar.onnx` (MIT-Lizenz, 230 KB, wird mit eingecheckt)
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
11. Smart-Reframing: 9:16-Ausschnitt folgt dem Sprecher (Gesichtserkennung lokal, kostenlos).
    Fallback ist das Layout mit unscharfem Hintergrund, z. B. bei Gaming ohne große Gesichter.
12. Optionen im Formular: Pausen kürzen (Untertitel werden mit umgerechnet) und
    Titel-Einblendung. Die Optionen stehen in `clips.json`, damit "Untertitel bearbeiten"
    denselben Look neu rendert. Ältere Projekte ohne Optionen behalten das alte Layout.
    Der Bildausschnitt hat drei Modi (`auto` = Sprecher folgen, `center` = feste Mitte, `blur`);
    pro Clip gewählte Optionen stehen in `clips.json` unter `clip_options`.
    Zoom ist eine Auswahl (`off` Standard, `subtle`, `punch`) und fällt bei einem ffmpeg-Fehler automatisch auf "aus" zurück. Die Clipgrenzen lassen sich auf "nur Sprache" stellen (`snap`).
13. TikTok-Zugangsdaten werden plattformunabhängig verschlüsselt (Fernet; unter Windows
    weiter DPAPI) und liegen im Server-Betrieb im Daten-Volume. Die Redirect-URI folgt `LOCALCLIP_DOMAIN`.
14. Warteschlange: nur ein Auftrag gleichzeitig (`LOCALCLIP_MAX_JOBS`).
15. Bildunterschriften (`postcopy.py`, Endpunkt `/api/jobs/<id>/clips/<n>/captions`): drei Varianten pro Clip
    aus Titel, Hook-Satz und Wortlaut; Hashtags aus Hauptwörtern und erkannter Nische (Gaming, Podcast,
    Comedy, Reaction), deutsch oder englisch je nach Transkript. Rein heuristisch, kein Sprachmodell.
16. Video-Finder ausgebaut (`downloader.search_public_videos`): bis zu vier Suchanfragen parallel
    pro Kategorie, Länge-Filter, Sortierung, bis 36 Treffer, Kanal-Vielfalt, Aktualitäts-Bonus.
    Jobs speichern `source_url`; der Finder markiert damit bereits geclippte Videos. In der
    Oberfläche lassen sich mehrere Vorschläge auswählen und nacheinander starten; laufende
    Aufträge erscheinen unter "Letzte Projekte". Der Videotitel aus dem Finder wird Projektname.
    Hinweis: Die echte YouTube-Suche war in der Entwicklungsumgebung nicht erreichbar; die
    Logik ist mit einer nachgebauten Suche getestet und sollte einmal live geprüft werden.

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

- Smart-Reframing und Pausen-Kürzen sind mit Testvideos geprüft, aber noch nicht mit
  mehreren echten langen Videos auf dem 2-GB-Server. Dort Laufzeit und RAM vergleichen.
- Die TikTok-Redirect-URI des Servers (`https://<LOCALCLIP_DOMAIN>/auth/tiktok/callback`)
  muss in TikTok for Developers eingetragen werden, bevor Posting auf dem Server klappt.
- Untertitel: Die Gruppenbreite (29 Zeichen) kann bei Schriftgröße 72 breiter als das Bild werden.
  Sinnvoll wäre eine Begrenzung nach Schriftgröße.
- Öffentliche yt-dlp-Suche funktioniert aktuell, kann aber bei Änderungen der
  Plattform ausfallen. Fehler müssen sauber angezeigt werden; keine Umgehung bauen.
- Ein kompletter realer End-to-End-Test mit mehreren langen Beispielvideos sollte
  Clipgrenzen, Untertitel und RAM-Verbrauch auf dem 2-GB-Server vergleichen.
- Optional: CSRF-Schutz für schreibende Web-Endpunkte ergänzen.

## Letzte wichtige Commits

- `f608e2a` – Entertainment Video-Finder
- `fd13237` – Clip-Potenzial-Analyse
- `2e4f1bd` – Szenenerkennung und Untertitel-Timing
- `1023bab` – langlebiger Cookie-Login
- `b2434bb` – HTTPS-Docker-Stack
