# LokalClipps

Eine lokale, eigenstaendige Alternative fuer automatisches Short-Form-Clipping.

## Funktionen

- Video-Upload im lokalen Browser
- Import über öffentliche Video-URLs (unter anderem YouTube, Twitch und TikTok)
- lokale Transkription mit faster-whisper
- automatische Auswahl interessanter 25- bis 60-Sekunden-Passagen
- 9:16-Export mit unscharfem Hintergrund
- Clips zunächst ohne fest eingebrannte Untertitel erzeugen
- Untertitel anschließend pro Clip bearbeiten und neu rendern
- Karaoke-, Fett-, Box- und Minimal-Stil mit Position, Größe und Farben
- optionaler Suchfokus für Themen, Personen oder Momentarten
- mehrere MP4-Clips pro Quellvideo
- lokale Projektablage über drei Tage, in der Oberfläche auf 1 bis 90 Tage einstellbar
- fertige Projekte bleiben auch nach einem Neustart verfügbar
- keine Cloud-API und kein Benutzerkonto

## Start unter Windows

1. `start.bat` doppelt anklicken.
2. Im Browser ein Video auswaehlen.
3. Auf **Clips erstellen** klicken.

Beim ersten Lauf laedt faster-whisper das ausgewaehlte Sprachmodell herunter. Danach kann
die Verarbeitung lokal erfolgen. FFmpeg muss im PATH liegen.

## Manuell starten

```powershell
python -m pip install -r requirements.txt
python app.py
```

Die App ist danach unter <http://127.0.0.1:8765> erreichbar.

## Render-Betrieb

`Dockerfile` und `render.yaml` sind für einen separaten Render-Webservice
vorbereitet. Der Dienst benötigt FFmpeg, mindestens 2 GB RAM und einen
dauerhaften Datenträger für Videos und Projekte. Setze in Render
`LOCALCLIP_PASSWORD` als Secret; die Oberfläche ist dann per HTTP Basic Auth
geschützt. Die öffentliche Gesundheitsprüfung ist unter `/health` erreichbar.

