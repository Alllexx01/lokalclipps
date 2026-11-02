import json
import shutil
import subprocess
import time

import pytest

try:
    from starlette.testclient import TestClient   # braucht je nach Version httpx; ohne wird dieser Test übersprungen
except Exception:
    pytest.skip("Starlette-TestClient nicht verfügbar", allow_module_level=True)
HAS_FFMPEG = bool(shutil.which("ffmpeg") and shutil.which("ffprobe"))
pytestmark = pytest.mark.skipif(not HAS_FFMPEG, reason="ffmpeg nicht installiert")


@pytest.fixture()
def client_and_job(tmp_path, monkeypatch):
    monkeypatch.setenv("LOCALCLIP_DATA_DIR", str(tmp_path))
    monkeypatch.delenv("LOCALCLIP_PASSWORD", raising=False)
    import importlib
    import app as app_module
    app_module = importlib.reload(app_module)
    job_id = "abcdef012345"
    job_dir = app_module.DATA / job_id
    job_dir.mkdir(parents=True)
    subprocess.run([
        "ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", "testsrc=size=1280x720:rate=25:duration=12",
        "-f", "lavfi", "-i", "sine=frequency=440:duration=12", "-c:v", "libx264", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-shortest", str(job_dir / "source.mp4"),
    ], check=True)
    words = [{"start": i * 0.4, "end": i * 0.4 + 0.3, "text": f"a{i}"} for i in range(9)]
    words += [{"start": 7.0 + i * 0.4, "end": 7.3 + i * 0.4, "text": f"b{i}"} for i in range(9)]
    (job_dir / "transcript.json").write_text(json.dumps({"language": "de", "words": words}))
    clip = {"start": 0.0, "end": 12.0, "score": 90, "title": "Warum?", "transcript": "Warum geht das? Weil es geht.", "reasons": []}
    (job_dir / "clips.json").write_text(json.dumps({"source": "source.mp4", "options": {"reframe": "blur", "tighten": False, "hook": False}, "clips": [clip]}))
    now = time.time()
    app_module.JOBS[job_id] = {
        "id": job_id, "name": "t", "status": "done", "progress": 100, "message": "Fertig",
        "clips": [{"index": 1, "filename": "x.mp4", "url": "/x", "duration": 12.0, "options": {"reframe": "blur", "tighten": False, "hook": False}}],
        "created_at": now, "updated_at": now, "last_accessed_at": now,
    }
    return TestClient(app_module.app), job_id, job_dir


def _render(client, job_id, **options):
    body = {"enabled": False, "style": "karaoke", "position": "bottom", "size": 72,
            "text_color": "#FFFFFF", "highlight_color": "#FFD400", **options}
    return client.post(f"/api/jobs/{job_id}/clips/1/render", json=body)


def test_cut_options_can_be_changed_afterwards_per_clip(client_and_job):
    client, job_id, job_dir = client_and_job
    first = _render(client, job_id, reframe="center", tighten=True, hook=True)
    assert first.status_code == 200
    clip = first.json()["clip"]
    assert clip["layout"] == "center" and clip["tightened"] > 3 and clip["duration"] < 9 and clip["hook"]
    assert clip["options"] == {"reframe": "center", "tighten": True, "hook": True, "zoom": "off"}

    again = _render(client, job_id, reframe="blur", tighten=False, hook=False)      # Entscheidung rückgängig machen
    changed = again.json()["clip"]
    assert changed["layout"] == "blur" and changed["tightened"] == 0 and changed["duration"] == 12.0

    saved = json.loads((job_dir / "clips.json").read_text())
    assert saved["clip_options"]["1"] == {"reframe": "blur", "tighten": False, "hook": False, "zoom": "off"}


def test_unknown_reframe_value_falls_back_safely(client_and_job):
    client, job_id, _ = client_and_job
    response = _render(client, job_id, reframe="zoom")
    assert response.status_code == 200 and response.json()["clip"]["layout"] == "blur"


def test_discover_marks_already_used_videos_and_passes_filters(client_and_job, monkeypatch):
    client, job_id, _ = client_and_job
    import app as app_module
    app_module.JOBS[job_id]["source_url"] = "https://www.youtube.com/watch?v=usedUsed123"
    seen = {}

    def fake_search(query, category, limit, min_minutes, max_minutes, sort):
        seen.update(query=query, category=category, limit=limit, min=min_minutes, max=max_minutes, sort=sort)
        return [
            {"id": "usedUsed123", "title": "A", "url": "https://www.youtube.com/watch?v=usedUsed123", "channel": "K", "duration": 600, "views": 1, "thumbnail": "", "score": 50, "reasons": []},
            {"id": "freshFresh1", "title": "B", "url": "https://www.youtube.com/watch?v=freshFresh1", "channel": "K", "duration": 600, "views": 1, "thumbnail": "", "score": 40, "reasons": []},
        ]

    monkeypatch.setattr(app_module, "search_public_videos", fake_search)
    data = client.get("/api/discover", params={"q": "x", "category": "podcast", "limit": "24", "min_minutes": "20", "max_minutes": "60", "sort": "views"}).json()
    assert [item["already_used"] for item in data["results"]] == [True, False]
    assert seen == {"query": "x", "category": "podcast", "limit": 24, "min": 20, "max": 60, "sort": "views"}
    broken = client.get("/api/discover", params={"limit": "kaputt", "min_minutes": "-5"})
    assert broken.status_code == 200 and seen["limit"] == 12 and seen["min"] == 0


def test_chosen_video_title_becomes_project_name(client_and_job, monkeypatch):
    client, _, _ = client_and_job
    import app as app_module
    monkeypatch.setattr(app_module, "validate_public_video_url", lambda value: value)
    monkeypatch.setattr(app_module, "download_video", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("Test")))
    response = client.post("/api/jobs", data={"url": "https://www.youtube.com/watch?v=abcDEF12345", "title": "Mein   toller\nPodcast"})
    job = app_module.JOBS[response.json()["job_id"]]
    for _ in range(100):                      # Hintergrund-Thread abwarten, damit der Test sauber endet
        if job["status"] == "error":
            break
        time.sleep(0.05)
    assert job["name"] == "Mein toller Podcast"
    assert job["source_url"].endswith("abcDEF12345")


def test_zoom_can_be_chosen_and_reverted_per_clip(client_and_job):
    client, job_id, job_dir = client_and_job
    words = [{"start": 0.5 + i * 0.5, "end": 0.5 + i * 0.5 + 0.4, "text": t} for i, t in enumerate(
        "Warum geht das so? Das ist unglaublich! Und noch eine Frage? Genau so ist es.".split())]
    (job_dir / "transcript.json").write_text(json.dumps({"language": "de", "words": words}))
    punched = _render(client, job_id, reframe="center", zoom="punch").json()["clip"]
    assert punched["options"]["zoom"] == "punch" and punched["zoom"] == "punch"
    off = _render(client, job_id, reframe="center", zoom="off").json()["clip"]
    assert off["zoom"] == "off"
    unknown = _render(client, job_id, reframe="center", zoom="wild").json()["clip"]
    assert unknown["options"]["zoom"] == "off"
    assert json.loads((job_dir / "clips.json").read_text())["clip_options"]["1"]["zoom"] == "off"


def test_caption_suggestions_endpoint(client_and_job):
    client, job_id, job_dir = client_and_job
    manifest = json.loads((job_dir / "clips.json").read_text())
    manifest["clips"][0].update(title="Warum geht das nicht?", transcript="Warum geht das nicht? Der Boss im Spiel ist schwer. Gameplay mit Controller.")
    (job_dir / "clips.json").write_text(json.dumps(manifest))
    response = client.get(f"/api/jobs/{job_id}/clips/1/captions")
    assert response.status_code == 200
    variants = response.json()["variants"]
    assert len(variants) == 3 and all("#" in item["text"] for item in variants)
    assert client.get(f"/api/jobs/{job_id}/clips/9/captions").status_code == 404
    assert client.get(f"/api/jobs/{job_id}/clips/0/captions").status_code == 404
    assert client.get("/api/jobs/ffffffffffff/clips/1/captions").status_code == 404


def test_mobile_page_accepts_one_time_youtube_cookie_file(client_and_job):
    client, _, _ = client_and_job
    page = client.get("/").text
    assert "cookies_file" in page
    assert "cookieBrowser" not in page
    assert "Nur einmalig" in page
    assert "direkt danach" in page


def test_cookie_file_is_deleted_after_failed_download(client_and_job, monkeypatch):
    client, _, _ = client_and_job
    import app as app_module
    monkeypatch.setattr(app_module, "validate_public_video_url", lambda value: value)
    seen = {}

    def fail_download(*args, **kwargs):
        cookie_file = kwargs["cookie_file"]
        seen["content"] = cookie_file.read_text()
        seen["path"] = cookie_file
        raise RuntimeError("Testdownload fehlgeschlagen")

    monkeypatch.setattr(app_module, "download_video", fail_download)
    response = client.post(
        "/api/jobs",
        data={"url": "https://www.youtube.com/watch?v=abcDEF12345"},
        files={"cookies_file": ("cookies.txt", b"# Netscape HTTP Cookie File\n", "text/plain")},
    )
    job_id = response.json()["job_id"]
    for _ in range(100):
        if app_module.JOBS[job_id]["status"] == "error":
            break
        time.sleep(0.02)
    assert seen["content"].startswith("# Netscape")
    assert not seen["path"].exists()
