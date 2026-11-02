from __future__ import annotations

import base64
import ctypes
import hashlib
import json
import re
import secrets
import time
import urllib.error
import urllib.parse
import urllib.request
from ctypes import wintypes
from pathlib import Path


AUTHORIZE_URL = "https://www.tiktok.com/v2/auth/authorize/"
TOKEN_URL = "https://open.tiktokapis.com/v2/oauth/token/"
API_ROOT = "https://open.tiktokapis.com"
REDIRECT_URI = "http://127.0.0.1:8765/auth/tiktok/callback"
SCOPES = "user.info.basic,video.publish"


class TikTokError(RuntimeError):
    pass


class DATA_BLOB(ctypes.Structure):
    _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_byte))]


def _blob(data: bytes) -> tuple[DATA_BLOB, ctypes.Array]:
    buffer = ctypes.create_string_buffer(data)
    return DATA_BLOB(len(data), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_byte))), buffer


def _protect(data: bytes) -> str:
    source, source_buffer = _blob(data)
    result = DATA_BLOB()
    if not ctypes.windll.crypt32.CryptProtectData(
        ctypes.byref(source), "LokalClipps TikTok", None, None, None, 0,
        ctypes.byref(result),
    ):
        raise TikTokError("TikTok-Daten konnten unter Windows nicht sicher gespeichert werden.")
    try:
        encrypted = ctypes.string_at(result.pbData, result.cbData)
        return base64.b64encode(encrypted).decode("ascii")
    finally:
        ctypes.windll.kernel32.LocalFree(result.pbData)


def _unprotect(value: str) -> bytes:
    source, source_buffer = _blob(base64.b64decode(value))
    result = DATA_BLOB()
    if not ctypes.windll.crypt32.CryptUnprotectData(
        ctypes.byref(source), None, None, None, None, 0, ctypes.byref(result),
    ):
        raise TikTokError("Die gespeicherte TikTok-Anmeldung konnte nicht gelesen werden.")
    try:
        return ctypes.string_at(result.pbData, result.cbData)
    finally:
        ctypes.windll.kernel32.LocalFree(result.pbData)


class TikTokClient:
    def __init__(self, root: Path):
        self.config_file = root / "tiktok_config.json"
        self.token_file = root / "tiktok_token.dat"
        self.pending: dict[str, dict] = {}

    def _read_config(self) -> dict:
        if not self.config_file.is_file():
            return {}
        try:
            config = json.loads(self.config_file.read_text(encoding="utf-8"))
            if config.get("client_secret"):
                config["client_secret"] = _unprotect(config["client_secret"]).decode("utf-8")
            return config
        except (OSError, ValueError, json.JSONDecodeError, TikTokError):
            return {}

    def configure(self, client_key: str, client_secret: str) -> dict:
        client_key = client_key.strip()
        client_secret = client_secret.strip()
        if not re.fullmatch(r"[A-Za-z0-9_-]{8,128}", client_key):
            raise TikTokError("Bitte einen gültigen TikTok Client Key eingeben.")
        old = self._read_config()
        if not client_secret:
            client_secret = old.get("client_secret", "")
        if not client_secret:
            raise TikTokError("Bitte den TikTok Client Secret eingeben.")
        payload = {"client_key": client_key, "client_secret": _protect(client_secret.encode("utf-8"))}
        self.config_file.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        return self.status()

    def _save_token(self, token: dict) -> None:
        now = int(time.time())
        token["expires_at"] = now + int(token.get("expires_in", 0))
        token["refresh_expires_at"] = now + int(token.get("refresh_expires_in", 0))
        self.token_file.write_text(_protect(json.dumps(token).encode("utf-8")), encoding="ascii")

    def _read_token(self) -> dict:
        if not self.token_file.is_file():
            return {}
        try:
            return json.loads(_unprotect(self.token_file.read_text(encoding="ascii")).decode("utf-8"))
        except (OSError, ValueError, json.JSONDecodeError, TikTokError):
            return {}

    def status(self) -> dict:
        config = self._read_config()
        token = self._read_token()
        return {
            "configured": bool(config.get("client_key") and config.get("client_secret")),
            "client_key_hint": (config.get("client_key", "")[:5] + "…") if config.get("client_key") else "",
            "connected": bool(token.get("access_token") and token.get("refresh_token")),
            "scope": token.get("scope", ""),
            "redirect_uri": REDIRECT_URI,
        }

    def logout(self) -> None:
        self.token_file.unlink(missing_ok=True)

    def authorize_url(self) -> str:
        config = self._read_config()
        if not config.get("client_key"):
            raise TikTokError("TikTok ist noch nicht eingerichtet.")
        state = secrets.token_urlsafe(32)
        alphabet = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-._~"
        verifier = "".join(secrets.choice(alphabet) for _ in range(64))
        challenge = hashlib.sha256(verifier.encode("ascii")).hexdigest()
        self.pending[state] = {"verifier": verifier, "expires": time.time() + 600}
        query = urllib.parse.urlencode({
            "client_key": config["client_key"],
            "response_type": "code",
            "scope": SCOPES,
            "redirect_uri": REDIRECT_URI,
            "state": state,
            "code_challenge": challenge,
            "code_challenge_method": "S256",
        })
        return f"{AUTHORIZE_URL}?{query}"

    def exchange_code(self, code: str, state: str) -> None:
        pending = self.pending.pop(state, None)
        if not pending or pending["expires"] < time.time():
            raise TikTokError("Die TikTok-Anmeldung ist abgelaufen. Bitte erneut anmelden.")
        config = self._read_config()
        token = self._request_json(TOKEN_URL, form={
            "client_key": config["client_key"],
            "client_secret": config["client_secret"],
            "code": code,
            "grant_type": "authorization_code",
            "redirect_uri": REDIRECT_URI,
            "code_verifier": pending["verifier"],
        })
        if not token.get("access_token"):
            raise TikTokError(token.get("error_description") or "TikTok hat keine Anmeldung zurückgegeben.")
        self._save_token(token)

    def _access_token(self) -> str:
        token = self._read_token()
        if not token.get("access_token"):
            raise TikTokError("Bitte zuerst mit TikTok anmelden.")
        if int(token.get("expires_at", 0)) <= time.time() + 120:
            config = self._read_config()
            token = self._request_json(TOKEN_URL, form={
                "client_key": config["client_key"],
                "client_secret": config["client_secret"],
                "grant_type": "refresh_token",
                "refresh_token": token["refresh_token"],
            })
            self._save_token(token)
        return token["access_token"]

    def creator_info(self) -> dict:
        return self._api_json("/v2/post/publish/creator_info/query/", {})

    def post_status(self, publish_id: str) -> dict:
        return self._api_json("/v2/post/publish/status/fetch/", {"publish_id": publish_id})

    def direct_post(self, video: Path, caption: str, privacy: str, allow_comment: bool,
                    allow_duet: bool, allow_stitch: bool) -> dict:
        creator = self.creator_info()
        allowed = creator.get("privacy_level_options", [])
        if privacy not in allowed:
            raise TikTokError("Diese Sichtbarkeit ist für das TikTok-Konto nicht verfügbar.")
        size = video.stat().st_size
        if size <= 0:
            raise TikTokError("Der Clip ist leer.")
        if size <= 64 * 1024 * 1024:
            chunk_size, chunk_count = size, 1
        else:
            chunk_size = 32 * 1024 * 1024
            chunk_count = max(1, size // chunk_size)
        payload = {
            "post_info": {
                "title": caption[:2200],
                "privacy_level": privacy,
                "disable_comment": not allow_comment,
                "disable_duet": not allow_duet,
                "disable_stitch": not allow_stitch,
                "video_cover_timestamp_ms": 1000,
            },
            "source_info": {
                "source": "FILE_UPLOAD",
                "video_size": size,
                "chunk_size": chunk_size,
                "total_chunk_count": chunk_count,
            },
        }
        initialized = self._api_json("/v2/post/publish/video/init/", payload)
        upload_url = initialized.get("upload_url", "")
        publish_id = initialized.get("publish_id", "")
        if not upload_url.startswith("https://open-upload.tiktokapis.com/") or not publish_id:
            raise TikTokError("TikTok hat keine sichere Upload-Adresse zurückgegeben.")
        self._upload_chunks(upload_url, video, chunk_size, chunk_count)
        return {"publish_id": publish_id, "status": "PROCESSING_UPLOAD"}

    def _upload_chunks(self, url: str, video: Path, chunk_size: int, chunk_count: int) -> None:
        total = video.stat().st_size
        with video.open("rb") as source:
            for index in range(chunk_count):
                start = index * chunk_size
                length = (total - start) if index == chunk_count - 1 else chunk_size
                data = source.read(length)
                end = start + len(data) - 1
                request = urllib.request.Request(url, data=data, method="PUT", headers={
                    "Content-Type": "video/mp4",
                    "Content-Length": str(len(data)),
                    "Content-Range": f"bytes {start}-{end}/{total}",
                })
                try:
                    with urllib.request.urlopen(request, timeout=180) as response:
                        response.read()
                except urllib.error.HTTPError as exc:
                    raise TikTokError(f"TikTok-Upload fehlgeschlagen ({exc.code}).") from exc

    def _api_json(self, path: str, payload: dict) -> dict:
        result = self._request_json(
            API_ROOT + path,
            json_payload=payload,
            headers={"Authorization": f"Bearer {self._access_token()}"},
        )
        error = result.get("error", {})
        if error and error.get("code") not in {None, "ok"}:
            raise TikTokError(error.get("message") or error.get("code") or "TikTok-Anfrage fehlgeschlagen.")
        return result.get("data", result)

    @staticmethod
    def _request_json(url: str, form: dict | None = None, json_payload: dict | None = None,
                      headers: dict | None = None) -> dict:
        request_headers = dict(headers or {})
        if form is not None:
            data = urllib.parse.urlencode(form).encode("utf-8")
            request_headers["Content-Type"] = "application/x-www-form-urlencoded"
        else:
            data = json.dumps(json_payload or {}).encode("utf-8")
            request_headers["Content-Type"] = "application/json; charset=UTF-8"
        request = urllib.request.Request(url, data=data, method="POST", headers=request_headers)
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                return json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            try:
                body = json.loads(exc.read().decode("utf-8"))
                message = body.get("error_description") or body.get("message") or body.get("error", {}).get("message")
            except Exception:
                message = None
            raise TikTokError(message or f"TikTok-Anfrage fehlgeschlagen ({exc.code}).") from exc
        except (urllib.error.URLError, TimeoutError) as exc:
            raise TikTokError("TikTok ist momentan nicht erreichbar.") from exc


def hashtag_packages(title: str) -> list[dict]:
    words = re.findall(r"[A-Za-zÄÖÜäöüß0-9]{4,}", title)
    stop = {"dass", "diese", "dieser", "einen", "einer", "habe", "haben", "mein", "meine", "nicht", "noch", "oder", "weil", "wird", "würde", "überhaupt"}
    topic = []
    for word in words:
        clean = word.lower().replace("ß", "ss")
        if clean not in stop and clean not in topic:
            topic.append(clean)
    topic = topic[:4]
    return [
        {"name": "Reichweite", "tags": ["fyp", "foryou", "viral", "deutschertiktok", "lokalclipps"]},
        {"name": "Gaming", "tags": ["gaming", "videospiele", "gamingdeutsch", "zocken", "clips"]},
        {"name": "Zum Inhalt", "tags": topic + ["shortvideo", "highlight"]},
    ]
