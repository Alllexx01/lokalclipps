from pathlib import Path

from starlette.testclient import TestClient


def helper_app(tmp_path, monkeypatch):
    monkeypatch.setenv("LOCALCLIP_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("LOCALCLIP_PC_HELPER", "1")
    monkeypatch.delenv("LOCALCLIP_PASSWORD", raising=False)
    import importlib
    import app as app_module
    return importlib.reload(app_module)


def test_youtube_job_waits_for_pc_and_can_be_claimed(tmp_path, monkeypatch):
    app_module = helper_app(tmp_path, monkeypatch)
    client = TestClient(app_module.app)
    response = client.post("/api/jobs", data={"url": "https://www.youtube.com/watch?v=abcDEF12345"})
    assert response.status_code == 200
    job_id = response.json()["job_id"]
    assert app_module.JOBS[job_id]["status"] == "waiting_for_pc"

    assert client.post("/api/helper/claim").status_code == 404
    claimed = client.post("/api/helper/claim", headers={"X-LocalClip-Helper": "windows-v1"}).json()["job"]
    assert claimed["id"] == job_id
    assert claimed["url"].endswith("abcDEF12345")
    assert app_module.JOBS[job_id]["status"] == "helper_downloading"


def test_helper_streams_video_and_starts_processing(tmp_path, monkeypatch):
    app_module = helper_app(tmp_path, monkeypatch)
    client = TestClient(app_module.app)
    job_id = client.post(
        "/api/jobs", data={"url": "https://www.youtube.com/watch?v=abcDEF12345"},
    ).json()["job_id"]
    headers = {"X-LocalClip-Helper": "windows-v1"}
    client.post("/api/helper/claim", headers=headers)
    started = {}
    monkeypatch.setattr(
        app_module,
        "start_processing",
        lambda claimed_id, video: started.update(job_id=claimed_id, video=video),
    )
    payload = b"fake-video" * 200
    response = client.post(
        f"/api/helper/jobs/{job_id}/upload",
        content=payload,
        headers={**headers, "X-Video-Filename": "aufnahme.webm", "Content-Type": "application/octet-stream"},
    )
    assert response.status_code == 200
    assert started["job_id"] == job_id
    assert Path(started["video"]).name == "source.webm"
    assert Path(started["video"]).read_bytes() == payload


def test_helper_can_report_failure(tmp_path, monkeypatch):
    app_module = helper_app(tmp_path, monkeypatch)
    client = TestClient(app_module.app)
    job_id = client.post(
        "/api/jobs", data={"url": "https://www.youtube.com/watch?v=abcDEF12345"},
    ).json()["job_id"]
    headers = {"X-LocalClip-Helper": "windows-v1"}
    client.post("/api/helper/claim", headers=headers)
    response = client.post(
        f"/api/helper/jobs/{job_id}/failed", json={"error": "PC konnte das Video nicht laden"}, headers=headers,
    )
    assert response.status_code == 200
    assert app_module.JOBS[job_id]["status"] == "error"
    assert "PC konnte" in app_module.JOBS[job_id]["error"]
