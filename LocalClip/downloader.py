from __future__ import annotations

import ipaddress
import socket
from pathlib import Path
from urllib.parse import urlparse


def validate_public_video_url(value: str) -> str:
    value = value.strip()
    parsed = urlparse(value)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ValueError("Bitte eine vollständige Video-URL mit http:// oder https:// eingeben.")
    hostname = parsed.hostname.lower().rstrip(".")
    if hostname == "localhost" or hostname.endswith(".local"):
        raise ValueError("Lokale Netzwerkadressen sind nicht erlaubt.")

    try:
        addresses = {item[4][0] for item in socket.getaddrinfo(hostname, parsed.port or 443)}
    except socket.gaierror as exc:
        raise ValueError("Die Adresse konnte nicht gefunden werden.") from exc
    for address in addresses:
        ip = ipaddress.ip_address(address)
        if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved or ip.is_multicast:
            raise ValueError("Private oder lokale Netzwerkadressen sind nicht erlaubt.")
    return value


def download_video(
    url: str,
    job_dir: Path,
    progress,
    cookie_browser: str = "none",
    cookie_file: Path | None = None,
) -> Path:
    try:
        from yt_dlp import YoutubeDL
        from yt_dlp.utils import DownloadError
    except ImportError as exc:
        raise RuntimeError("URL-Import fehlt. Bitte requirements.txt installieren.") from exc

    safe_url = validate_public_video_url(url)

    def hook(data: dict) -> None:
        status = data.get("status")
        if status == "downloading":
            total = data.get("total_bytes") or data.get("total_bytes_estimate") or 0
            downloaded = data.get("downloaded_bytes") or 0
            percent = int(downloaded / total * 10) if total else 2
            progress(max(1, min(10, percent)), "Video wird von der URL geladen")
        elif status == "finished":
            progress(10, "Download abgeschlossen")

    options = {
        "format": "bv*[height<=1080]+ba/b[height<=1080]/b",
        "outtmpl": str(job_dir / "source.%(ext)s"),
        "merge_output_format": "mp4",
        "noplaylist": True,
        "restrictfilenames": True,
        "progress_hooks": [hook],
        "js_runtimes": {"node": {}},
        "remote_components": {"ejs:github"},
        "quiet": True,
        "no_warnings": True,
        "socket_timeout": 30,
    }
    if cookie_file and cookie_file.is_file():
        options["cookiefile"] = str(cookie_file)
    elif cookie_browser in {"chrome", "edge", "firefox"}:
        options["cookiesfrombrowser"] = (cookie_browser,)
    try:
        with YoutubeDL(options) as downloader:
            info = downloader.extract_info(safe_url, download=True)
            requested = info.get("requested_downloads") or []
            candidates = [Path(item["filepath"]) for item in requested if item.get("filepath")]
            prepared = Path(downloader.prepare_filename(info))
            candidates.extend([prepared.with_suffix(".mp4"), prepared])
    except DownloadError as exc:
        message = str(exc)
        if "not a bot" in message.lower() or "sign in to confirm" in message.lower():
            raise RuntimeError(
                "YouTube verlangt eine Anmeldung. Öffne beim Link-Import ‚YouTube-Anmeldung‘ "
                "und wähle Firefox, Chrome, Edge oder eine cookies.txt-Datei."
            ) from exc
        if "cookie" in message.lower() or "decrypt" in message.lower():
            raise RuntimeError(
                "Der Browser-Login konnte nicht gelesen werden. Firefox oder eine exportierte "
                "cookies.txt-Datei funktioniert unter Windows meist zuverlässiger."
            ) from exc
        raise RuntimeError(f"Der Videodownload ist fehlgeschlagen: {message}") from exc

    for candidate in candidates:
        if candidate.is_file():
            return candidate
    source_files = sorted(job_dir.glob("source.*"), key=lambda item: item.stat().st_size, reverse=True)
    video_files = [item for item in source_files if item.suffix.lower() not in {".part", ".ytdl", ".json"}]
    if not video_files:
        raise RuntimeError("Das Video konnte von dieser URL nicht heruntergeladen werden.")
    return video_files[0]

