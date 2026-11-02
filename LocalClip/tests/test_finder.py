import sys
import time
import types

import pytest

import downloader
from downloader import score_entry, search_public_videos, video_id_from_url


class FakeDownloadError(Exception):
    pass


def install_fake_ytdlp(monkeypatch, catalog, fail_all=False):
    """Ersetzt yt-dlp durch eine Suche über `catalog`: {Suchbegriff-Teil: [Einträge]}."""
    calls = []

    class FakeYoutubeDL:
        def __init__(self, options):
            self.options = options

        def __enter__(self):
            return self

        def __exit__(self, *_):
            return False

        def extract_info(self, search, download=False):
            calls.append(search)
            if fail_all:
                raise FakeDownloadError("offline")
            terms = search.split(":", 1)[1]
            entries = []
            for key, items in catalog.items():
                if key in terms:
                    entries.extend(items)
            return {"entries": entries}

    module = types.ModuleType("yt_dlp")
    module.YoutubeDL = FakeYoutubeDL
    utils = types.ModuleType("yt_dlp.utils")
    utils.DownloadError = FakeDownloadError
    module.utils = utils
    monkeypatch.setitem(sys.modules, "yt_dlp", module)
    monkeypatch.setitem(sys.modules, "yt_dlp.utils", utils)
    return calls


def video(video_id, title="Warum das so ist", channel="Kanal", duration=1800, views=50_000, **extra):
    return {"id": video_id, "title": title, "channel": channel, "duration": duration, "view_count": views, **extra}


def test_search_merges_queries_and_removes_duplicates(monkeypatch):
    calls = install_fake_ytdlp(monkeypatch, {
        "podcast": [video("aaaaaa1", channel="A"), video("bbbbbb2", channel="B")],
        "gaming": [video("bbbbbb2", channel="B"), video("cccccc3", channel="C")],
    })
    results = search_public_videos("", "all", 12)
    assert len(calls) == 4                                           # mehrere Suchanfragen für "Alle Themen"
    assert sorted(item["id"] for item in results) == ["aaaaaa1", "bbbbbb2", "cccccc3"]


def test_duration_filter_and_sorting(monkeypatch):
    install_fake_ytdlp(monkeypatch, {"podcast": [
        video("short01", duration=240, views=900_000, channel="A"),
        video("medium1", duration=1800, views=10_000, channel="B"),
        video("long001", duration=7200, views=500_000, channel="C"),
        video("unknown", duration=0, views=1, channel="D"),
    ]})
    only_medium = search_public_videos("", "podcast", 12, min_minutes=20, max_minutes=60)
    assert [item["id"] for item in only_medium] == ["medium1"]       # unbekannte Länge fällt beim Filter heraus
    by_views = search_public_videos("", "podcast", 12, sort="views")
    assert by_views[0]["id"] == "short01"
    by_length = search_public_videos("", "podcast", 12, sort="length")
    assert by_length[0]["id"] == "long001"


def test_one_channel_cannot_fill_the_whole_list(monkeypatch):
    items = [video(f"same{i:03d}", channel="Dauerkanal", views=900_000) for i in range(5)]
    items += [video("other01", channel="Anderer", views=1_000), video("other02", channel="Dritter", views=1_000)]
    install_fake_ytdlp(monkeypatch, {"podcast": items})
    results = search_public_videos("", "podcast", 6)
    top = [item["channel"] for item in results[:4]]
    assert top.count("Dauerkanal") <= 2


def test_search_error_message_when_everything_fails(monkeypatch):
    install_fake_ytdlp(monkeypatch, {}, fail_all=True)
    with pytest.raises(RuntimeError, match="nicht erreichbar"):
        search_public_videos("", "gaming", 8)


def test_live_streams_are_left_out(monkeypatch):
    install_fake_ytdlp(monkeypatch, {"podcast": [video("live001", live_status="is_live"), video("fine001")]})
    assert [item["id"] for item in search_public_videos("", "podcast", 8)] == ["fine001"]


def test_score_prefers_real_sources_over_shorts_and_live():
    normal, _ = score_entry(video("n", duration=2400))
    short, _ = score_entry(video("s", duration=45, title="Lustig #shorts"))
    live, _ = score_entry(video("l", duration=2400, live_status="is_live"))
    compilation, _ = score_entry(video("c", duration=2400, title="Best of Zusammenschnitt"))
    assert normal > short and normal > live and normal > compilation


def test_recent_uploads_get_a_small_boost():
    now = time.mktime(time.strptime("20261101", "%Y%m%d"))
    fresh, reasons = score_entry(video("f", upload_date="20261020"), now=now)
    old, _ = score_entry(video("o", upload_date="20200101"), now=now)
    assert fresh > old and "aktuell" in reasons


def test_video_id_from_url():
    assert video_id_from_url("https://www.youtube.com/watch?v=abcDEF12345&t=3") == "abcDEF12345"
    assert video_id_from_url("https://youtu.be/abcDEF12345") == "abcDEF12345"
    assert video_id_from_url("") == ""
    assert video_id_from_url("https://example.com/video.mp4") == ""
