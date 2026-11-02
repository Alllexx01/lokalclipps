from __future__ import annotations

import http.client
import http.cookiejar
import json
import logging
import os
import re
import shutil
import socket
import ssl
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from downloader import download_video


ROOT = Path(__file__).resolve().parent
DEPLOY = ROOT / ".deploy"
WORK = DEPLOY / "helper_work"
LOG = DEPLOY / "pc_helper.log"
DEFAULT_SERVER = "https://2-29-154-156.sslip.io"
POLL_SECONDS = 8


def read_credentials() -> tuple[str, str, str]:
    server = os.environ.get("LOCALCLIP_SERVER", DEFAULT_SERVER).rstrip("/")
    username = os.environ.get("LOCALCLIP_USERNAME", "")
    password = os.environ.get("LOCALCLIP_PASSWORD", "")
    credential_file = DEPLOY / "login.txt"
    if credential_file.is_file():
        for raw in credential_file.read_text(encoding="utf-8").splitlines():
            if ":" not in raw:
                continue
            label, value = (part.strip() for part in raw.split(":", 1))
            if label.lower() in {"adresse", "server"} and value.startswith("https://"):
                server = value.rstrip("/")
            elif label.lower() in {"benutzer", "benutzername"}:
                username = value
            elif label.lower() == "passwort":
                password = value
    if not username or not password:
        raise RuntimeError("Anmeldedaten fehlen in LocalClip/.deploy/login.txt.")
    return server, username, password


class ServerClient:
    def __init__(self, server: str, username: str, password: str):
        self.server = server
        self.cookies = http.cookiejar.CookieJar()
        self.opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(self.cookies))
        body = urllib.parse.urlencode({"username": username, "password": password}).encode()
        response = self.opener.open(server + "/login", body, timeout=30)
        if response.geturl().rstrip("/") != server:
            raise RuntimeError("Anmeldung am LocalClip-Server fehlgeschlagen.")

    @staticmethod
    def _helper_request(url: str, data: bytes = b"", content_type: str = "application/json") -> urllib.request.Request:
        return urllib.request.Request(
            url,
            data=data,
            method="POST",
            headers={"X-LocalClip-Helper": "windows-v1", "Content-Type": content_type},
        )

    def claim(self) -> dict | None:
        request = self._helper_request(self.server + "/api/helper/claim")
        with self.opener.open(request, timeout=35) as response:
            return json.load(response).get("job")

    def fail(self, job_id: str, message: str) -> None:
        data = json.dumps({"error": message[:500]}).encode("utf-8")
        request = self._helper_request(self.server + f"/api/helper/jobs/{job_id}/failed", data)
        self.opener.open(request, timeout=30).close()

    def upload(self, job_id: str, video: Path) -> None:
        parsed = urllib.parse.urlsplit(self.server)
        connection_class = http.client.HTTPSConnection if parsed.scheme == "https" else http.client.HTTPConnection
        connection = connection_class(parsed.hostname, parsed.port, timeout=300, context=ssl.create_default_context()) \
            if parsed.scheme == "https" else connection_class(parsed.hostname, parsed.port, timeout=300)
        cookie_header = "; ".join(f"{cookie.name}={cookie.value}" for cookie in self.cookies)
        path = (parsed.path.rstrip("/") + f"/api/helper/jobs/{job_id}/upload") or "/"
        size = video.stat().st_size
        connection.putrequest("POST", path)
        connection.putheader("Content-Length", str(size))
        connection.putheader("Content-Type", "application/octet-stream")
        connection.putheader("X-LocalClip-Helper", "windows-v1")
        connection.putheader("X-Video-Filename", "source" + video.suffix.lower())
        connection.putheader("Cookie", cookie_header)
        connection.endheaders()
        with video.open("rb") as source:
            while chunk := source.read(1024 * 1024):
                connection.send(chunk)
        response = connection.getresponse()
        payload = response.read().decode("utf-8", "replace")
        connection.close()
        if response.status >= 300:
            try:
                detail = json.loads(payload).get("error", payload)
            except ValueError:
                detail = payload
            raise RuntimeError(f"Upload fehlgeschlagen ({response.status}): {detail}")


def download_on_pc(job: dict, destination: Path) -> Path:
    errors = []
    browser_order = ["none", "edge", "chrome", "firefox"]
    preferred = os.environ.get("LOCALCLIP_HELPER_BROWSER", "").lower()
    if preferred in {"edge", "chrome", "firefox"}:
        browser_order = ["none", preferred]
    for browser in browser_order:
        try:
            logging.info("Download %s mit %s", job["id"], browser)
            return download_video(job["url"], destination, lambda *_: None, cookie_browser=browser)
        except Exception as exc:
            errors.append(f"{browser}: {exc}")
    raise RuntimeError("Download auf dem PC fehlgeschlagen. " + " | ".join(errors))


def run() -> None:
    DEPLOY.mkdir(exist_ok=True)
    WORK.mkdir(exist_ok=True)
    logging.basicConfig(filename=LOG, level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s", encoding="utf-8")
    lock = socket.socket()
    try:
        lock.bind(("127.0.0.1", 48765))
    except OSError:
        return
    server, username, password = read_credentials()
    client = ServerClient(server, username, password)
    logging.info("PC-Helfer gestartet: %s", server)
    while True:
        try:
            job = client.claim()
            if not job:
                time.sleep(POLL_SECONDS)
                continue
            job_id = str(job.get("id", ""))
            if not re.fullmatch(r"[0-9a-f]{12}", job_id):
                raise RuntimeError("Server lieferte eine ungültige Auftragsnummer.")
            destination = WORK / job_id
            shutil.rmtree(destination, ignore_errors=True)
            destination.mkdir(parents=True)
            try:
                video = download_on_pc(job, destination)
                client.upload(job_id, video)
                logging.info("Auftrag %s übertragen", job_id)
            except Exception as exc:
                logging.exception("Auftrag %s fehlgeschlagen", job_id)
                client.fail(job_id, str(exc))
            finally:
                shutil.rmtree(destination, ignore_errors=True)
        except (urllib.error.URLError, TimeoutError, OSError, RuntimeError) as exc:
            logging.warning("Verbindung wird neu aufgebaut: %s", exc)
            time.sleep(15)
            try:
                client = ServerClient(server, username, password)
            except Exception:
                logging.exception("Erneute Anmeldung fehlgeschlagen")
                time.sleep(30)


if __name__ == "__main__":
    try:
        run()
    except Exception:
        logging.exception("PC-Helfer wurde beendet")
        if sys.stdout:
            raise
