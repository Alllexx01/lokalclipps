from __future__ import annotations

import hashlib
import hmac
import json
import os
import shutil
import threading
import time
import traceback
import uuid
import re
import urllib.parse
from pathlib import Path

import uvicorn
from starlette.applications import Starlette
from starlette.concurrency import run_in_threadpool
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse
from starlette.routing import Mount, Route
from starlette.staticfiles import StaticFiles

from core import ZOOM_MODES, Clip, Word, make_hook, process_video, render_short
from postcopy import build_variants
from downloader import download_video, search_public_videos, validate_public_video_url, video_age, video_id_from_url
from tiktok import TikTokClient, TikTokError, hashtag_packages


ROOT = Path(__file__).resolve().parent
DATA = Path(os.environ.get("LOCALCLIP_DATA_DIR", ROOT / "data")).resolve()
SETTINGS_FILE = DATA / "settings.json"
JOBS: dict[str, dict] = {}
LOCK = threading.Lock()
REFRAME_MODES = {"auto", "center", "blur"}   # Sprecher folgen / feste Mitte / ganzes Bild mit unscharfem Hintergrund
PROCESSING = threading.Semaphore(max(1, int(os.environ.get("LOCALCLIP_MAX_JOBS", "1") or 1)))
PC_HELPER_ENABLED = os.environ.get("LOCALCLIP_PC_HELPER", "").lower() in {"1", "true", "yes", "on"}
VIDEO_DATE_CACHE: dict[str, float] = {}
METADATA_PENDING: dict[str, float] = {}
HELPER_LAST_SEEN = 0.0
DEFAULT_SETTINGS = {"retention_days": 3}
# Im Server-Betrieb liegen TikTok-Zugangsdaten im dauerhaften Datenvolume statt im Container-Image.
TIKTOK = TikTokClient(DATA if os.environ.get("LOCALCLIP_DATA_DIR") else ROOT)


def load_settings() -> dict:
    if not SETTINGS_FILE.is_file():
        return DEFAULT_SETTINGS.copy()
    try:
        saved = json.loads(SETTINGS_FILE.read_text(encoding="utf-8"))
        return {"retention_days": max(1, min(90, int(saved.get("retention_days", 3))))}
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        return DEFAULT_SETTINGS.copy()


def save_settings(settings: dict) -> None:
    SETTINGS_FILE.write_text(json.dumps(settings, ensure_ascii=False, indent=2), encoding="utf-8")


def save_job(job_id: str) -> None:
    job_dir = DATA / job_id
    job_dir.mkdir(parents=True, exist_ok=True)
    (job_dir / "job.json").write_text(
        json.dumps(JOBS[job_id], ensure_ascii=False, indent=2), encoding="utf-8",
    )


def load_jobs() -> None:
    DATA.mkdir(exist_ok=True)
    for record_path in DATA.glob("*/job.json"):
        try:
            job = json.loads(record_path.read_text(encoding="utf-8"))
            job_id = str(job["id"])
            if not re.fullmatch(r"[0-9a-f]{12}", job_id):
                continue
            if job.get("status") in {"queued", "running"}:
                job.update(status="error", message="Die Verarbeitung wurde durch einen Neustart unterbrochen.")
            elif job.get("status") in {"waiting_for_pc", "helper_downloading"}:
                if PC_HELPER_ENABLED:
                    job.update(status="waiting_for_pc", message="Wartet auf den Download-PC")
                else:
                    job.update(status="error", message="Der PC-Download wurde beendet.", error="Der PC-Download wurde beendet.")
            JOBS[job_id] = job
            save_job(job_id)
        except (OSError, KeyError, TypeError, json.JSONDecodeError):
            continue


def cleanup_expired_jobs() -> int:
    retention_seconds = load_settings()["retention_days"] * 86400
    now = time.time()
    expired: list[str] = []
    with LOCK:
        for job_id, job in list(JOBS.items()):
            last_used = float(job.get("last_accessed_at") or job.get("created_at") or now)
            if now - last_used >= retention_seconds and job.get("status") not in {"queued", "running"}:
                expired.append(job_id)
                JOBS.pop(job_id, None)
    for job_id in expired:
        job_dir = (DATA / job_id).resolve()
        if job_dir.parent == DATA.resolve() and re.fullmatch(r"[0-9a-f]{12}", job_dir.name) and (job_dir / "job.json").is_file():
            shutil.rmtree(job_dir)
    for job_dir in DATA.iterdir():
        if not job_dir.is_dir() or not re.fullmatch(r"[0-9a-f]{12}", job_dir.name):
            continue
        if (job_dir / "job.json").exists():
            continue
        if now - job_dir.stat().st_mtime >= retention_seconds:
            shutil.rmtree(job_dir)
            expired.append(job_dir.name)
    return len(expired)


def update_job(job_id: str, **values) -> None:
    with LOCK:
        JOBS[job_id].update(values)
        JOBS[job_id]["updated_at"] = time.time()
        if values.get("status") in {"done", "error"}:
            JOBS[job_id]["last_accessed_at"] = time.time()
        save_job(job_id)


def start_processing(job_id: str, source_video: Path | None = None, cookie_file: Path | None = None) -> None:
    """Start the CPU-heavy processing queue after a local file is available."""
    def run_job() -> None:
        try:
            update_job(job_id, status="running", message="Wird verarbeitet")
            job = JOBS[job_id]
            video = source_video
            if video is None:
                try:
                    video = download_video(
                        str(job.get("source_url", "")),
                        DATA / job_id,
                        lambda percent, message: update_job(job_id, progress=percent, message=message),
                        cookie_file=cookie_file,
                    )
                finally:
                    if cookie_file:
                        cookie_file.unlink(missing_ok=True)
            results, analysis = process_video(
                video=video,
                output_dir=DATA / job_id,
                progress=lambda percent, message: update_job(job_id, progress=percent, message=message),
                **job["settings"],
            )
            for item in results:
                item["url"] = f"/api/jobs/{job_id}/files/{item['filename']}"
            update_job(job_id, status="done", progress=100, message="Fertig", clips=results, analysis=analysis)
        except Exception as exc:
            traceback.print_exc()
            update_job(job_id, status="error", message=str(exc), error=str(exc))

    def worker() -> None:
        update_job(job_id, status="queued", message="Wartet, bis die vorherige Verarbeitung fertig ist")
        with PROCESSING:
            run_job()

    threading.Thread(target=worker, daemon=True).start()


async def homepage(request):
    return FileResponse(ROOT / "static" / "index.html")


async def healthcheck(request):
    return JSONResponse({"status": "ok"})


def _session_token(username: str, password: str, expires_at: int) -> str:
    payload = f"{username}:{expires_at}".encode("utf-8")
    signature = hmac.new(password.encode("utf-8"), payload, hashlib.sha256).hexdigest()
    return f"{expires_at}.{signature}"


def _valid_session(token: str, username: str, password: str) -> bool:
    try:
        expires_raw, provided_signature = token.split(".", 1)
        expires_at = int(expires_raw)
    except (TypeError, ValueError):
        return False
    if expires_at < int(time.time()):
        return False
    expected = _session_token(username, password, expires_at).split(".", 1)[1]
    return hmac.compare_digest(provided_signature, expected)


async def login(request):
    password = os.environ.get("LOCALCLIP_PASSWORD", "").strip()
    expected_user = os.environ.get("LOCALCLIP_USERNAME", "alex")
    error = ""
    if request.method == "POST":
        form = await request.form()
        username = str(form.get("username", ""))
        provided_password = str(form.get("password", ""))
        if (
            hmac.compare_digest(username, expected_user)
            and hmac.compare_digest(provided_password, password)
        ):
            expires_at = int(time.time()) + 365 * 24 * 60 * 60
            response = RedirectResponse("/", status_code=303)
            response.set_cookie(
                "localclip_session",
                _session_token(expected_user, password, expires_at),
                max_age=365 * 24 * 60 * 60,
                expires=expires_at,
                path="/",
                secure=True,
                httponly=True,
                samesite="lax",
            )
            return response
        error = "Benutzername oder Passwort ist falsch."
    return HTMLResponse(
        f"""<!doctype html>
<html lang="de"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>LokalClipps – Anmeldung</title>
<style>
body{{margin:0;min-height:100vh;display:grid;place-items:center;background:#080b12;color:#fff;font:16px system-ui,sans-serif}}
main{{width:min(88vw,360px);padding:28px;border:1px solid #283142;border-radius:18px;background:#111722;box-shadow:0 18px 60px #0008}}
h1{{margin:0 0 22px;color:#ff315d}}label{{display:block;margin:14px 0 6px;color:#cbd3e1}}
input,button{{box-sizing:border-box;width:100%;padding:13px;border-radius:10px;font:inherit}}
input{{border:1px solid #39445a;background:#080b12;color:#fff}}button{{margin-top:20px;border:0;background:#ff315d;color:#fff;font-weight:700}}
p{{color:#ff9aaf}}
</style></head><body><main><h1>LokalClipps</h1>
<form method="post"><label>Benutzername</label><input name="username" autocomplete="username" required value="{expected_user}">
<label>Passwort</label><input name="password" type="password" autocomplete="current-password" required autofocus>
<button type="submit">Anmelden</button></form>{f'<p>{error}</p>' if error else ''}</main></body></html>"""
    )


async def password_protection(request, call_next):
    """Protect the cloud deployment with a long-lived, signed login cookie."""
    password = os.environ.get("LOCALCLIP_PASSWORD", "").strip()
    if not password or request.url.path in {"/health", "/login"}:
        return await call_next(request)
    expected_user = os.environ.get("LOCALCLIP_USERNAME", "alex")
    if _valid_session(request.cookies.get("localclip_session", ""), expected_user, password):
        return await call_next(request)
    if request.url.path.startswith("/api/"):
        return JSONResponse({"error": "Anmeldung erforderlich."}, status_code=401)
    return RedirectResponse("/login", status_code=303)


async def create_job(request):
    cleanup_expired_jobs()
    form = await request.form()
    upload = form.get("video")
    cookie_upload = form.get("cookies_file")
    video_url = str(form.get("url", "")).strip()
    has_upload = bool(upload and getattr(upload, "filename", ""))
    if not has_upload and not video_url:
        return JSONResponse({"error": "Bitte ein Video auswählen oder eine Video-URL eingeben."}, status_code=400)
    if video_url and not has_upload:
        try:
            video_url = validate_public_video_url(video_url)
        except ValueError as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)

    job_id = uuid.uuid4().hex[:12]
    job_dir = DATA / job_id
    job_dir.mkdir(parents=True, exist_ok=True)
    video = None
    cookie_file = None
    if has_upload:
        suffix = Path(upload.filename).suffix.lower() or ".mp4"
        video = job_dir / f"source{suffix}"
        with video.open("wb") as target:
            shutil.copyfileobj(upload.file, target, length=1024 * 1024)
    elif cookie_upload and getattr(cookie_upload, "filename", ""):
        cookie_file = job_dir / "youtube_cookies.txt"
        with cookie_file.open("wb") as target:
            shutil.copyfileobj(cookie_upload.file, target, length=256 * 1024)
        if cookie_file.stat().st_size > 5 * 1024 * 1024:
            cookie_file.unlink(missing_ok=True)
            shutil.rmtree(job_dir, ignore_errors=True)
            return JSONResponse({"error": "Die cookies.txt-Datei ist ungewöhnlich groß."}, status_code=400)

    try:
        settings = {
            "count": max(1, min(10, int(form.get("count", 5)))),
            "min_seconds": max(15, min(90, int(form.get("min_seconds", 25)))),
            "max_seconds": max(20, min(120, int(form.get("max_seconds", 55)))),
            "model_name": str(form.get("model", "small")),
            "language": str(form.get("language", "auto")),
            "focus": str(form.get("focus", ""))[:240],
            "caption_style": str(form.get("caption_style", "none")),
            "caption_position": str(form.get("caption_position", "bottom")),
            "caption_size": max(44, min(100, int(form.get("caption_size", 72)))),
            "caption_color": str(form.get("caption_color", "#FFFFFF")),
            "highlight_color": str(form.get("highlight_color", "#FFD400")),
            "reframe": str(form.get("reframe", "auto")),
            "tighten": str(form.get("tighten", "")).lower() in {"1", "true", "on", "yes"},
            "hook": str(form.get("hook", "")).lower() in {"1", "true", "on", "yes"},
            "snap_scenes": str(form.get("snap", "scene")).lower() != "speech",
            "zoom": str(form.get("zoom", "off")),
        }
    except ValueError:
        if cookie_file:
            cookie_file.unlink(missing_ok=True)
        shutil.rmtree(job_dir, ignore_errors=True)
        return JSONResponse({"error": "Ungültige Einstellungen."}, status_code=400)
    if settings["max_seconds"] <= settings["min_seconds"]:
        settings["max_seconds"] = settings["min_seconds"] + 10
    if settings["caption_style"] not in {"none", "karaoke", "bold", "boxed", "minimal"}:
        settings["caption_style"] = "none"
    if settings["caption_position"] not in {"top", "middle", "bottom"}:
        settings["caption_position"] = "bottom"
    if settings["reframe"] not in REFRAME_MODES:
        settings["reframe"] = "auto"
    if settings["zoom"] not in ZOOM_MODES:
        settings["zoom"] = "off"
    for color_key, fallback in (("caption_color", "#FFFFFF"), ("highlight_color", "#FFD400")):
        if not re.fullmatch(r"#[0-9a-fA-F]{6}", settings[color_key]):
            settings[color_key] = fallback

    now = time.time()
    given_title = re.sub(r"\s+", " ", str(form.get("title", ""))).strip()[:90]
    display_name = (
        Path(upload.filename).stem if has_upload
        else given_title or video_url.split("/")[-1].split("?")[0] or "URL-Video"
    )
    JOBS[job_id] = {
        "id": job_id,
        "name": display_name[:90],
        "source_url": "" if has_upload else video_url,
        "status": "queued",
        "progress": 0,
        "message": "Wartet",
        "clips": [],
        "analysis": None,
        "settings": settings,
        "created_at": now,
        "updated_at": now,
        "last_accessed_at": now,
    }
    save_job(job_id)
    if video is None and PC_HELPER_ENABLED and video_id_from_url(video_url):
        update_job(job_id, status="waiting_for_pc", message="Wartet auf den Download-PC")
    else:
        start_processing(job_id, video, cookie_file)
    return JSONResponse({"job_id": job_id})


async def job_status(request):
    job_id = request.path_params["job_id"]
    job = JOBS.get(job_id)
    if not job:
        return JSONResponse({"error": "Auftrag nicht gefunden."}, status_code=404)
    update_job(job_id, last_accessed_at=time.time())
    return JSONResponse(job)


async def recent_jobs(request):
    cleanup_expired_jobs()
    jobs = sorted(JOBS.values(), key=lambda item: item.get("updated_at", 0), reverse=True)
    return JSONResponse({"jobs": jobs, "retention_days": load_settings()["retention_days"]})


async def helper_claim(request):
    """Let the authenticated Windows helper claim one pending YouTube download."""
    if not PC_HELPER_ENABLED or request.headers.get("x-localclip-helper") != "windows-v1":
        return JSONResponse({"error": "PC-Helfer ist nicht freigeschaltet."}, status_code=404)
    global HELPER_LAST_SEEN
    now = time.time()
    with LOCK:
        HELPER_LAST_SEEN = now
        metadata_ids = [
            video_id for video_id, claimed_at in METADATA_PENDING.items()
            if video_id not in VIDEO_DATE_CACHE and (not claimed_at or now - claimed_at > 60)
        ][:24]
        for video_id in metadata_ids:
            METADATA_PENDING[video_id] = now
        candidates = []
        for job in JOBS.values():
            status = job.get("status")
            stale = status == "helper_downloading" and now - float(job.get("helper_claimed_at", 0)) > 600
            if status == "waiting_for_pc" or stale:
                candidates.append(job)
        claimed = None
        if candidates:
            job = min(candidates, key=lambda item: item.get("created_at", now))
            job.update(
                status="helper_downloading",
                message="Der PC lädt das Video herunter",
                helper_claimed_at=now,
                updated_at=now,
            )
            save_job(job["id"])
            claimed = {"id": job["id"], "url": job.get("source_url", ""), "name": job.get("name", "Video")}
    return JSONResponse({"job": claimed, "metadata_ids": metadata_ids})


async def helper_metadata(request):
    if not PC_HELPER_ENABLED or request.headers.get("x-localclip-helper") != "windows-v1":
        return JSONResponse({"error": "PC-Helfer ist nicht freigeschaltet."}, status_code=404)
    try:
        dates = (await request.json()).get("dates", {})
    except (TypeError, ValueError):
        dates = {}
    accepted = 0
    with LOCK:
        for video_id, raw_timestamp in dates.items():
            if not re.fullmatch(r"[A-Za-z0-9_-]{6,20}", str(video_id)):
                continue
            try:
                timestamp = float(raw_timestamp or 0)
            except (TypeError, ValueError):
                timestamp = 0
            if timestamp > 0:
                VIDEO_DATE_CACHE[str(video_id)] = timestamp
                accepted += 1
            METADATA_PENDING.pop(str(video_id), None)
    return JSONResponse({"ok": True, "accepted": accepted})


async def helper_upload(request):
    """Receive the source video as a stream; no browser cookies ever reach the server."""
    if not PC_HELPER_ENABLED or request.headers.get("x-localclip-helper") != "windows-v1":
        return JSONResponse({"error": "PC-Helfer ist nicht freigeschaltet."}, status_code=404)
    job_id = request.path_params["job_id"]
    job = JOBS.get(job_id)
    if not job or job.get("status") != "helper_downloading":
        return JSONResponse({"error": "Dieser Auftrag wartet nicht auf den PC."}, status_code=409)
    try:
        content_length = int(request.headers.get("content-length", "0"))
    except ValueError:
        content_length = 0
    if content_length <= 0 or content_length > 12 * 1024 * 1024 * 1024:
        return JSONResponse({"error": "Ungültige oder zu große Videodatei."}, status_code=413)
    filename = Path(request.headers.get("x-video-filename", "source.mp4")).name
    suffix = Path(filename).suffix.lower()
    if suffix not in {".mp4", ".mov", ".mkv", ".webm", ".m4v", ".avi"}:
        suffix = ".mp4"
    target = DATA / job_id / f"source{suffix}"
    written = 0
    try:
        with target.open("wb") as output:
            async for chunk in request.stream():
                written += len(chunk)
                if written > 12 * 1024 * 1024 * 1024:
                    raise ValueError("Videodatei ist zu groß.")
                output.write(chunk)
                if content_length:
                    progress = min(10, max(1, int(written / content_length * 10)))
                    update_job(job_id, progress=progress, message="Der PC überträgt das Video zum Server")
        if written != content_length or written < 1024:
            raise ValueError("Die Videoübertragung war unvollständig.")
    except Exception as exc:
        target.unlink(missing_ok=True)
        update_job(job_id, status="error", message=str(exc), error=str(exc))
        return JSONResponse({"error": str(exc)}, status_code=400)
    update_job(job_id, progress=10, message="Video übertragen; Verarbeitung startet")
    start_processing(job_id, target)
    return JSONResponse({"ok": True, "job_id": job_id})


async def helper_failed(request):
    if not PC_HELPER_ENABLED or request.headers.get("x-localclip-helper") != "windows-v1":
        return JSONResponse({"error": "PC-Helfer ist nicht freigeschaltet."}, status_code=404)
    job_id = request.path_params["job_id"]
    if job_id not in JOBS:
        return JSONResponse({"error": "Auftrag nicht gefunden."}, status_code=404)
    try:
        detail = str((await request.json()).get("error", "Download fehlgeschlagen"))[:500]
    except (TypeError, ValueError):
        detail = "Download auf dem PC fehlgeschlagen."
    update_job(job_id, status="error", message=detail, error=detail)
    return JSONResponse({"ok": True})


async def helper_progress(request):
    if not PC_HELPER_ENABLED or request.headers.get("x-localclip-helper") != "windows-v1":
        return JSONResponse({"error": "PC-Helfer ist nicht freigeschaltet."}, status_code=404)
    job_id = request.path_params["job_id"]
    job = JOBS.get(job_id)
    if not job or job.get("status") != "helper_downloading":
        return JSONResponse({"error": "Dieser Auftrag läuft nicht auf dem PC."}, status_code=409)
    try:
        payload = await request.json()
        progress = max(1, min(20, int(payload.get("progress", 1))))
        message = str(payload.get("message", "Der PC lädt das Video"))[:160]
    except (TypeError, ValueError):
        return JSONResponse({"error": "Ungültiger Fortschritt."}, status_code=400)
    update_job(job_id, progress=progress, message=message, helper_claimed_at=time.time())
    return JSONResponse({"ok": True})


async def discover_videos(request):
    query = str(request.query_params.get("q", ""))[:100]
    category = str(request.query_params.get("category", "all"))
    sort = str(request.query_params.get("sort", "score"))

    def number(name: str, default: int, low: int, high: int) -> int:
        try:
            return max(low, min(high, int(request.query_params.get(name, default))))
        except (TypeError, ValueError):
            return default

    limit = number("limit", 12, 4, 36)
    min_minutes = number("min_minutes", 0, 0, 600)
    max_minutes = number("max_minutes", 0, 0, 600)
    try:
        results = await run_in_threadpool(
            search_public_videos, query, category, limit, min_minutes, max_minutes, sort,
        )
        used = {video_id_from_url(job.get("source_url", "")) for job in JOBS.values()} - {""}
        for item in results:
            item["already_used"] = item["id"] in used      # schon einmal geclippt
        missing = [item["id"] for item in results if item.get("age_days") is None]
        helper_online = time.time() - HELPER_LAST_SEEN < 25
        if missing and helper_online:
            with LOCK:
                for video_id in missing:
                    if video_id not in VIDEO_DATE_CACHE:
                        METADATA_PENDING.setdefault(video_id, 0)
            deadline = time.time() + 45
            while time.time() < deadline:
                with LOCK:
                    unresolved = [video_id for video_id in missing if video_id not in VIDEO_DATE_CACHE and video_id in METADATA_PENDING]
                if not unresolved:
                    break
                await run_in_threadpool(time.sleep, 0.25)
        with LOCK:
            cached_dates = {video_id: VIDEO_DATE_CACHE.get(video_id) for video_id in missing}
        for item in results:
            timestamp = cached_dates.get(item["id"])
            if item.get("age_days") is None and timestamp:
                item["published_at"], item["age_days"] = video_age({"timestamp": timestamp})
        return JSONResponse({"results": results, "query": query, "category": category, "limit": limit})
    except RuntimeError as exc:
        return JSONResponse({"error": str(exc)}, status_code=502)


async def retention_settings(request):
    if request.method == "GET":
        return JSONResponse(load_settings())
    payload = await request.json()
    try:
        days = max(1, min(90, int(payload.get("retention_days", 3))))
    except (TypeError, ValueError):
        return JSONResponse({"error": "Bitte eine Speicherdauer zwischen 1 und 90 Tagen eingeben."}, status_code=400)
    settings = {"retention_days": days}
    save_settings(settings)
    cleanup_expired_jobs()
    return JSONResponse(settings)


async def job_file(request):
    job_id = request.path_params["job_id"]
    filename = Path(request.path_params["filename"]).name
    path = DATA / job_id / filename
    if not path.is_file() or not filename.lower().endswith(".mp4"):
        return JSONResponse({"error": "Datei nicht gefunden."}, status_code=404)
    if job_id in JOBS:
        update_job(job_id, last_accessed_at=time.time())
    return FileResponse(path, media_type="video/mp4", filename=filename)


async def rerender_clip(request):
    job_id = request.path_params["job_id"]
    try:
        clip_index = int(request.path_params["clip_index"])
    except ValueError:
        return JSONResponse({"error": "Ungültiger Clip."}, status_code=400)
    job = JOBS.get(job_id)
    job_dir = DATA / job_id
    manifest_path = job_dir / "clips.json"
    transcript_path = job_dir / "transcript.json"
    if not job or job.get("status") != "done" or not manifest_path.is_file() or not transcript_path.is_file():
        return JSONResponse({"error": "Der Auftrag ist nicht mehr verfügbar."}, status_code=404)

    payload = await request.json()
    enabled = bool(payload.get("enabled", True))
    style = str(payload.get("style", "karaoke"))
    position = str(payload.get("position", "bottom"))
    if style not in {"karaoke", "bold", "boxed", "minimal"}:
        style = "karaoke"
    if position not in {"top", "middle", "bottom"}:
        position = "bottom"
    try:
        size = max(44, min(100, int(payload.get("size", 72))))
    except (TypeError, ValueError):
        size = 72
    text_color = str(payload.get("text_color", "#FFFFFF"))
    highlight_color = str(payload.get("highlight_color", "#FFD400"))
    if not re.fullmatch(r"#[0-9a-fA-F]{6}", text_color):
        text_color = "#FFFFFF"
    if not re.fullmatch(r"#[0-9a-fA-F]{6}", highlight_color):
        highlight_color = "#FFD400"

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    transcript = json.loads(transcript_path.read_text(encoding="utf-8"))
    if clip_index < 1 or clip_index > len(manifest["clips"]):
        return JSONResponse({"error": "Clip nicht gefunden."}, status_code=404)
    clip = Clip(**manifest["clips"][clip_index - 1])
    words = [Word(**item) for item in transcript["words"]]
    source = job_dir / Path(manifest["source"]).name
    if not source.is_file():
        return JSONResponse({"error": "Das Quellvideo fehlt."}, status_code=404)

    version = uuid.uuid4().hex[:8]
    subtitle = job_dir / f"clip_{clip_index:02d}_{version}.ass"
    output = job_dir / f"clip_{clip_index:02d}_custom_{version}.mp4"
    # Ältere Projekte kennen die neuen Optionen nicht und behalten ihr bisheriges Aussehen.
    # Reihenfolge: jetzt im Dialog gewählt > früher für diesen Clip gespeichert > Einstellung des Projekts.
    saved = {**(manifest.get("options") or {}), **((manifest.get("clip_options") or {}).get(str(clip_index)) or {})}
    reframe = payload.get("reframe", saved.get("reframe", "blur"))
    if reframe not in REFRAME_MODES:
        reframe = "blur"
    tighten = bool(payload.get("tighten", saved.get("tighten", False)))
    hook = bool(payload.get("hook", saved.get("hook", False)))
    zoom = payload.get("zoom", saved.get("zoom", "off"))
    if zoom not in ZOOM_MODES:
        zoom = "off"
    caption = {
        "style": style, "position": position, "size": size,
        "color": text_color, "highlight": highlight_color,
    } if enabled else None

    def render_custom_clip():
        return render_short(
            source, words, clip, output, subtitle,
            caption=caption, reframe=reframe, tighten=tighten, hook=hook, zoom=zoom,
        )

    try:
        applied = await run_in_threadpool(render_custom_clip)
    except Exception as exc:
        traceback.print_exc()
        return JSONResponse({"error": str(exc)}, status_code=500)
    chosen = {"reframe": reframe, "tighten": tighten, "hook": hook, "zoom": zoom}
    manifest.setdefault("clip_options", {})[str(clip_index)] = chosen      # beim nächsten Bearbeiten vorbelegt
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    output_url = f"/api/jobs/{job_id}/files/{output.name}"
    clips = list(job.get("clips", []))
    if clip_index <= len(clips):
        clips[clip_index - 1] = {
            **clips[clip_index - 1], "filename": output.name, "url": output_url,
            "layout": applied["reframe"], "tightened": applied["tightened"], "hook": applied["hook"], "zoom": applied["zoom"],
            "duration": round(clip.duration - applied["tightened"], 1), "options": chosen,
        }
    update_job(job_id, clips=clips, last_accessed_at=time.time())
    return JSONResponse({"url": output_url, "filename": output.name, "clip": clips[clip_index - 1] if clip_index <= len(clips) else None})


async def clip_captions(request):
    """Fertige Bildunterschriften (Beschreibung + Hashtags) für einen Clip vorschlagen."""
    job_id = request.path_params["job_id"]
    try:
        clip_index = int(request.path_params["clip_index"])
    except ValueError:
        return JSONResponse({"error": "Ungültiger Clip."}, status_code=400)
    manifest_path = DATA / job_id / "clips.json"
    if job_id not in JOBS or not manifest_path.is_file():
        return JSONResponse({"error": "Der Auftrag ist nicht mehr verfügbar."}, status_code=404)
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        clip = Clip(**manifest["clips"][clip_index - 1]) if clip_index >= 1 else None
    except (IndexError, KeyError, TypeError, ValueError):
        clip = None
    if clip is None:
        return JSONResponse({"error": "Clip nicht gefunden."}, status_code=404)
    language = "de"
    transcript_path = DATA / job_id / "transcript.json"
    if transcript_path.is_file():
        try:
            language = str(json.loads(transcript_path.read_text(encoding="utf-8")).get("language") or "de")
        except (OSError, ValueError):
            pass
    variants = build_variants(clip.title, clip.transcript, make_hook(clip), language)
    return JSONResponse({"variants": variants})


async def tiktok_settings(request):
    if request.method == "GET":
        return JSONResponse(TIKTOK.status())
    try:
        payload = await request.json()
        return JSONResponse(TIKTOK.configure(str(payload.get("client_key", "")), str(payload.get("client_secret", ""))))
    except TikTokError as exc:
        return JSONResponse({"error": str(exc)}, status_code=400)


async def tiktok_login(request):
    try:
        return RedirectResponse(TIKTOK.authorize_url())
    except TikTokError as exc:
        return RedirectResponse(f"/?tiktok_error={urllib.parse.quote(str(exc))}")


async def tiktok_callback(request):
    error = request.query_params.get("error")
    if error:
        description = request.query_params.get("error_description", error)
        return RedirectResponse(f"/?tiktok_error={urllib.parse.quote(description)}")
    try:
        code = request.query_params.get("code", "")
        state = request.query_params.get("state", "")
        if not code or not state:
            raise TikTokError("TikTok hat keine vollständige Anmeldung zurückgegeben.")
        await run_in_threadpool(TIKTOK.exchange_code, code, state)
        return RedirectResponse("/?tiktok=connected")
    except TikTokError as exc:
        return RedirectResponse(f"/?tiktok_error={urllib.parse.quote(str(exc))}")


async def tiktok_logout(request):
    TIKTOK.logout()
    return JSONResponse({"connected": False})


async def tiktok_creator(request):
    try:
        return JSONResponse(await run_in_threadpool(TIKTOK.creator_info))
    except TikTokError as exc:
        return JSONResponse({"error": str(exc)}, status_code=400)


async def tiktok_hashtags(request):
    title = str(request.query_params.get("title", ""))[:300]
    return JSONResponse({"packages": hashtag_packages(title)})


async def tiktok_post(request):
    job_id = request.path_params["job_id"]
    try:
        clip_index = int(request.path_params["clip_index"])
    except ValueError:
        return JSONResponse({"error": "Ungültiger Clip."}, status_code=400)
    job = JOBS.get(job_id)
    if not job or job.get("status") != "done" or not 1 <= clip_index <= len(job.get("clips", [])):
        return JSONResponse({"error": "Clip nicht gefunden."}, status_code=404)
    clip = job["clips"][clip_index - 1]
    video = DATA / job_id / Path(clip["filename"]).name
    if not video.is_file() or video.suffix.lower() != ".mp4":
        return JSONResponse({"error": "Die Clip-Datei fehlt."}, status_code=404)
    try:
        payload = await request.json()
        caption = str(payload.get("caption", "")).strip()[:2200]
        privacy = str(payload.get("privacy_level", "SELF_ONLY"))
        result = await run_in_threadpool(
            TIKTOK.direct_post,
            video,
            caption,
            privacy,
            bool(payload.get("allow_comment", True)),
            bool(payload.get("allow_duet", False)),
            bool(payload.get("allow_stitch", False)),
        )
        update_job(job_id, last_accessed_at=time.time())
        return JSONResponse(result)
    except TikTokError as exc:
        return JSONResponse({"error": str(exc)}, status_code=400)


async def tiktok_publish_status(request):
    try:
        payload = await request.json()
        publish_id = str(payload.get("publish_id", ""))[:100]
        if not publish_id:
            raise TikTokError("Publish-ID fehlt.")
        return JSONResponse(await run_in_threadpool(TIKTOK.post_status, publish_id))
    except TikTokError as exc:
        return JSONResponse({"error": str(exc)}, status_code=400)


routes = [
    Route("/", homepage),
    Route("/health", healthcheck),
    Route("/login", login, methods=["GET", "POST"]),
    Route("/api/jobs", recent_jobs, methods=["GET"]),
    Route("/api/jobs", create_job, methods=["POST"]),
    Route("/api/discover", discover_videos, methods=["GET"]),
    Route("/api/settings", retention_settings, methods=["GET", "POST"]),
    Route("/api/helper/claim", helper_claim, methods=["POST"]),
    Route("/api/helper/metadata", helper_metadata, methods=["POST"]),
    Route("/api/helper/jobs/{job_id}/upload", helper_upload, methods=["POST"]),
    Route("/api/helper/jobs/{job_id}/failed", helper_failed, methods=["POST"]),
    Route("/api/helper/jobs/{job_id}/progress", helper_progress, methods=["POST"]),
    Route("/api/jobs/{job_id}", job_status),
    Route("/api/jobs/{job_id}/clips/{clip_index}/render", rerender_clip, methods=["POST"]),
    Route("/api/jobs/{job_id}/files/{filename}", job_file),
    Route("/api/jobs/{job_id}/clips/{clip_index}/captions", clip_captions, methods=["GET"]),
    Route("/api/tiktok/settings", tiktok_settings, methods=["GET", "POST"]),
    Route("/auth/tiktok/login", tiktok_login),
    Route("/auth/tiktok/callback", tiktok_callback),
    Route("/api/tiktok/logout", tiktok_logout, methods=["POST"]),
    Route("/api/tiktok/creator", tiktok_creator),
    Route("/api/tiktok/hashtags", tiktok_hashtags),
    Route("/api/jobs/{job_id}/clips/{clip_index}/tiktok", tiktok_post, methods=["POST"]),
    Route("/api/tiktok/publish-status", tiktok_publish_status, methods=["POST"]),
    Mount("/static", StaticFiles(directory=ROOT / "static"), name="static"),
]

app = Starlette(debug=False, routes=routes)
app.add_middleware(BaseHTTPMiddleware, dispatch=password_protection)

load_jobs()
cleanup_expired_jobs()


if __name__ == "__main__":
    DATA.mkdir(exist_ok=True)
    host = os.environ.get("HOST", "0.0.0.0")
    port = int(os.environ.get("PORT", "8765"))
    uvicorn.run(app, host=host, port=port, log_level="info")

