import json
from pathlib import Path

import auto_fitnacraft


def video(video_id="abcDEF12345", age_days=0):
    return {
        "id": video_id,
        "title": "FitnaCraft eskaliert",
        "channel": "Testkanal",
        "url": f"https://www.youtube.com/watch?v={video_id}",
        "published_at": "2026-11-08",
        "age_days": age_days,
    }


def test_candidate_is_new_rejects_done_and_old_videos():
    state = {"videos": {"done001": {"status": "done"}}}
    assert auto_fitnacraft.candidate_is_new(video(), state, 2)
    assert not auto_fitnacraft.candidate_is_new(video("done001"), state, 2)
    assert not auto_fitnacraft.candidate_is_new(video("old0001", 3), state, 2)
    assert not auto_fitnacraft.candidate_is_new(video("unknown", None), state, 2)


def test_topic_match_ignores_spacing_and_rejects_unrelated_gaming():
    assert auto_fitnacraft.matches_topic(video(), "FitnaCraft")
    assert auto_fitnacraft.matches_topic({"title": "FITNA CRAFT TAG 2"}, "FitnaCraft")
    assert not auto_fitnacraft.matches_topic({"title": "Gaming Highlights der Woche"}, "FitnaCraft")


def test_run_processes_only_limit_and_remembers_results(tmp_path, monkeypatch):
    found = [video("video000001"), video("video000002"), video("video000003")]
    monkeypatch.setattr(auto_fitnacraft, "search_public_videos", lambda *args, **kwargs: found)
    processed = []

    def fake_process(item, data_dir, focus, keep_source):
        processed.append(item["id"])
        target = data_dir / item["id"]
        target.mkdir(parents=True)
        return {"completed_at": 123, "clips": [{"filename": "clip.mp4"}]}

    monkeypatch.setattr(auto_fitnacraft, "process_candidate", fake_process)
    result = auto_fitnacraft.run(
        data_dir=tmp_path,
        query="FitnaCraft",
        focus="lustig",
        max_age_days=2,
        max_per_run=2,
        discover_only=False,
        keep_source=False,
    )
    assert result == 0
    assert processed == ["video000001", "video000002"]
    state = json.loads((tmp_path / "state.json").read_text(encoding="utf-8"))
    assert state["videos"]["video000001"]["status"] == "done"
    assert state["videos"]["video000002"]["clips"] == 1
    assert "video000003" not in state["videos"]


def test_discover_only_does_not_create_state(tmp_path, monkeypatch):
    monkeypatch.setattr(auto_fitnacraft, "search_public_videos", lambda *args, **kwargs: [video()])
    assert auto_fitnacraft.run(
        data_dir=tmp_path,
        query="FitnaCraft",
        focus="lustig",
        max_age_days=2,
        max_per_run=2,
        discover_only=True,
        keep_source=False,
    ) == 0
    assert not (tmp_path / "state.json").exists()


class FakeTikTok:
    def __init__(self, *, configured=True, connected=True):
        self.configured = configured
        self.connected = connected
        self.posts = []

    def status(self):
        return {"configured": self.configured, "connected": self.connected}

    def creator_info(self):
        return {"privacy_level_options": ["PUBLIC_TO_EVERYONE", "SELF_ONLY"]}

    def post_status(self, publish_id):
        return {"status": "PUBLISH_COMPLETE", "publish_id": publish_id}

    def direct_post(self, video, caption, privacy, allow_comment, allow_duet, allow_stitch):
        self.posts.append((video, caption, privacy, allow_comment, allow_duet, allow_stitch))
        return {"publish_id": "pub-123", "status": "PROCESSING_UPLOAD"}


def test_tiktok_publish_posts_best_clip_once(tmp_path):
    video_id = "video000001"
    video_dir = tmp_path / video_id
    video_dir.mkdir()
    (video_dir / "clip_01_score_99.mp4").write_bytes(b"video")
    (video_dir / "clips.json").write_text(
        json.dumps({"clips": [{"index": 1, "transcript": "FitnaCraft eskaliert komplett."}]}),
        encoding="utf-8",
    )
    (video_dir / "manifest.json").write_text(json.dumps({
        "video": {"channel": "Testkanal"},
        "clips": [{"index": 1, "title": "Heftige Reaktion", "filename": "clip_01_score_99.mp4", "hook": "Was passiert hier?"}],
    }), encoding="utf-8")
    state = {"version": 1, "videos": {video_id: {"status": "done", "completed_at": 1}}}
    client = FakeTikTok()
    result = auto_fitnacraft.publish_pending_tiktok(
        state, tmp_path, privacy="PUBLIC_TO_EVERYONE", max_posts=2, client=client
    )
    assert result == {"posted": 1, "errors": []}
    assert len(client.posts) == 1
    assert "#fitnacraft" in client.posts[0][1].lower()
    assert state["videos"][video_id]["tiktok"]["publish_id"] == "pub-123"
    second = auto_fitnacraft.publish_pending_tiktok(
        state, tmp_path, privacy="PUBLIC_TO_EVERYONE", max_posts=2, client=client
    )
    assert second == {"posted": 0, "errors": []}
    assert len(client.posts) == 1


def test_tiktok_publish_waits_for_connection(tmp_path):
    result = auto_fitnacraft.publish_pending_tiktok(
        {"videos": {}}, tmp_path, privacy="PUBLIC_TO_EVERYONE", max_posts=2,
        client=FakeTikTok(configured=False, connected=False),
    )
    assert result["posted"] == 0
    assert "nicht eingerichtet" in result["blocked"]
