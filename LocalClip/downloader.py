from __future__ import annotations

import ipaddress
import math
import re
import socket
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from urllib.parse import urlparse


DISCOVERY_CATEGORIES = {
    "all": "deutsch podcast gaming comedy reaction entertainment",
    "gaming": "gaming deutsch highlights",
    "podcast": "podcast deutsch interview",
    "reaction": "reaction deutsch",
    "comedy": "comedy deutsch lustig",
}

# Mehrere Suchanfragen pro Kategorie ergeben mehr Vielfalt als eine einzelne Suche.
DISCOVERY_QUERIES = {
    "all": ["deutsch podcast folge", "deutsch gaming highlights", "deutsch comedy lustig", "deutsch reaction"],
    "gaming": ["gaming deutsch highlights", "gaming deutsch lustige momente", "gaming deutsch story"],
    "podcast": ["podcast deutsch interview", "podcast deutsch folge gespräch", "podcast deutsch diskussion"],
    "reaction": ["reaction deutsch", "reaktion deutsch lustig", "react deutsch"],
    "comedy": ["comedy deutsch lustig", "stand up comedy deutsch", "sketch comedy deutsch"],
}
COMPILATION_TERMS = ("compilation", "zusammenschnitt", "best of", "bestof")
SORT_MODES = {"score", "views", "length"}

DISCOVERY_HOOKS = {
    "warum", "fehler", "geheimnis", "unglaublich", "wirklich", "beste", "schlimmste",
    "reaction", "erklärt", "test", "gegen", "challenge", "interview", "story", "problem",
}


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


def video_id_from_url(url: str) -> str:
    """YouTube-ID aus einer Video-URL (leer, wenn keine erkannt wird)."""
    match = re.search(r"(?:v=|youtu\.be/|/shorts/|/embed/)([A-Za-z0-9_-]{6,20})", str(url or ""))
    return match.group(1) if match else ""


def score_entry(entry: dict, now: float | None = None) -> tuple[int, list[str]]:
    """Bewertet, wie gut sich ein Video zum Clippen eignet (ohne Anmeldung, nur aus Suchdaten)."""
    title = str(entry.get("title") or "")
    lower = title.lower()
    duration = int(entry.get("duration") or 0)
    views = int(entry.get("view_count") or 0)
    reasons: list[str] = []
    score = 35.0
    if 300 <= duration <= 5400:
        score += 28
        reasons.append("gute Ausgangslänge")
    elif 90 <= duration <= 10800:
        score += 15
    else:
        score -= 8
    if duration and duration < 90 or "#shorts" in lower:
        score -= 20                                   # Shorts sind schon kurz und taugen nicht als Quelle
    if any(term in lower for term in COMPILATION_TERMS):
        score -= 8                                    # Zusammenschnitte sind meist schon geclippt
    hook_hits = sum(1 for term in DISCOVERY_HOOKS if term in lower)
    if hook_hits:
        score += min(18, hook_hits * 7)
        reasons.append("starker Titel")
    if "?" in title:
        score += 3
    if re.search(r"\d", title):
        score += 2
    letters = [char for char in title if char.isalpha()]
    if len(letters) > 8 and sum(char.isupper() for char in letters) / len(letters) > 0.7:
        score -= 3                                    # Titel nur in Großbuchstaben wirken oft reißerisch
    if views:
        score += min(16, max(0, math.log10(max(views, 1)) - 2) * 4)
        if views >= 100_000:
            reasons.append("hohe Reichweite")
    upload_date = str(entry.get("upload_date") or "")
    if re.fullmatch(r"\d{8}", upload_date):
        try:
            uploaded = time.mktime(time.strptime(upload_date, "%Y%m%d"))
            age_days = max(1.0, ((now or time.time()) - uploaded) / 86400)
            if age_days <= 45:
                score += 3
                reasons.append("aktuell")
            if views and age_days <= 365:
                score += min(5, max(0.0, math.log10(views / age_days + 1)) * 1.5)
        except (ValueError, OverflowError):
            pass
    if entry.get("live_status") in {"is_live", "is_upcoming"}:
        score -= 30
    return int(max(0, min(99, round(score)))), reasons[:3] or ["öffentlich auffindbar"]


def _diversify(results: list[dict], per_channel: int = 2) -> list[dict]:
    """Verhindert, dass ein einzelner Kanal die ganze Liste füllt."""
    seen: Counter = Counter()
    first: list[dict] = []
    rest: list[dict] = []
    for item in results:
        (first if seen[item["channel"]] < per_channel else rest).append(item)
        seen[item["channel"]] += 1
    return first + rest


def video_age(entry: dict, now: float | None = None) -> tuple[str, int | None]:
    """Return ISO upload date and age in whole days when the source provides it."""
    uploaded = None
    raw_date = str(entry.get("upload_date") or "")
    if re.fullmatch(r"\d{8}", raw_date):
        try:
            uploaded = time.mktime(time.strptime(raw_date, "%Y%m%d"))
        except (ValueError, OverflowError):
            pass
    if uploaded is None:
        for key in ("timestamp", "release_timestamp"):
            try:
                value = float(entry.get(key) or 0)
            except (TypeError, ValueError):
                value = 0
            if value > 0:
                uploaded = value
                break
    if uploaded is None:
        return "", None
    age_days = max(0, int(((now or time.time()) - uploaded) / 86400))
    return time.strftime("%Y-%m-%d", time.localtime(uploaded)), age_days


def search_public_videos(
    query: str = "",
    category: str = "all",
    limit: int = 12,
    min_minutes: int = 0,
    max_minutes: int = 0,
    sort: str = "score",
) -> list[dict]:
    """Öffentliche YouTube-Suchvorschläge ohne Anmeldung, aus mehreren Suchanfragen gemischt."""
    try:
        from yt_dlp import YoutubeDL
        from yt_dlp.utils import DownloadError
    except ImportError as exc:
        raise RuntimeError("Videosuche fehlt. Bitte requirements.txt installieren.") from exc

    category = category if category in DISCOVERY_QUERIES else "all"
    sort = sort if sort in SORT_MODES else "score"
    clean_query = re.sub(r"\s+", " ", query).strip()[:100]
    limit = max(4, min(36, int(limit)))
    min_seconds = max(0, int(min_minutes)) * 60
    max_seconds = max(0, int(max_minutes)) * 60
    variants = DISCOVERY_QUERIES[category]
    # Mit eigenem Suchbegriff reichen zwei Varianten; ohne Suchbegriff wird breiter gesucht.
    terms = [f"{clean_query} {variant}".strip() for variant in (variants[:2] if clean_query else variants)]
    per_query = max(6, min(25, math.ceil(limit * 1.6 / len(terms))))

    def fetch(search_terms: str) -> list[dict] | None:
        options = {
            "extract_flat": True,
            "skip_download": True,
            "quiet": True,
            "no_warnings": True,
            "playlistend": per_query,
            "socket_timeout": 20,
            "js_runtimes": {"node": {}},
        }
        try:
            with YoutubeDL(options) as downloader:
                data = downloader.extract_info(f"ytsearch{per_query}:{search_terms}", download=False)
        except DownloadError:
            return None
        return list((data or {}).get("entries") or [])

    with ThreadPoolExecutor(max_workers=len(terms)) as pool:
        batches = list(pool.map(fetch, terms))
    if all(batch is None for batch in batches):
        raise RuntimeError(
            "Die öffentliche Videosuche ist gerade nicht erreichbar. Du kannst weiterhin einen erlaubten Videolink direkt einfügen."
        )

    results: list[dict] = []
    seen_ids: set[str] = set()
    for entry in (item for batch in batches for item in (batch or [])):
        video_id = str(entry.get("id") or "").strip()
        if not re.fullmatch(r"[A-Za-z0-9_-]{6,20}", video_id) or video_id in seen_ids:
            continue
        duration = int(entry.get("duration") or 0)
        if (min_seconds or max_seconds) and not duration:
            continue
        if (min_seconds and duration < min_seconds) or (max_seconds and duration > max_seconds):
            continue
        if entry.get("live_status") in {"is_live", "is_upcoming"}:
            continue
        seen_ids.add(video_id)
        score, reasons = score_entry(entry)
        thumbnails = entry.get("thumbnails") or []
        thumbnail = str((thumbnails[-1] if thumbnails else {}).get("url") or entry.get("thumbnail") or "")
        published_at, age_days = video_age(entry)
        results.append({
            "id": video_id,
            "title": str(entry.get("title") or "Unbenanntes Video").strip()[:180],
            "url": f"https://www.youtube.com/watch?v={video_id}",
            "channel": str(entry.get("channel") or entry.get("uploader") or "Unbekannter Kanal")[:100],
            "duration": duration,
            "views": int(entry.get("view_count") or 0),
            "published_at": published_at,
            "age_days": age_days,
            "thumbnail": thumbnail,
            "score": score,
            "reasons": reasons,
        })

    if sort == "views":
        results.sort(key=lambda item: item["views"], reverse=True)
    elif sort == "length":
        results.sort(key=lambda item: item["duration"], reverse=True)
    else:
        results.sort(key=lambda item: item["score"], reverse=True)
        results = _diversify(results)
    return results[:limit]


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
                "YouTube hat den automatischen Abruf blockiert. Speichere das Video auf deinem "
                "Handy und wähle es oben über ‚Video auswählen‘ aus – Cookies werden nicht benötigt."
            ) from exc
        if "cookie" in message.lower() or "decrypt" in message.lower():
            raise RuntimeError(
                "Die Plattform verlangt eine Anmeldung. Speichere dein Video auf dem Handy und "
                "lade die Videodatei direkt hoch – Zugangsdaten werden nicht benötigt."
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

