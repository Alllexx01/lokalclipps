import sys
import types
from pathlib import Path

import downloader


def test_missing_1080p_format_retries_with_ytdlp_default(tmp_path, monkeypatch):
    attempts = []

    class FakeDownloadError(Exception):
        pass

    class FakeYoutubeDL:
        def __init__(self, options):
            self.options = options
            attempts.append(options.copy())

        def __enter__(self):
            return self

        def __exit__(self, *_):
            return False

        def extract_info(self, _url, download=True):
            if self.options.get("format") != "best":
                raise FakeDownloadError("Requested format is not available")
            target = tmp_path / "source.mp4"
            target.write_bytes(b"video")
            return {"requested_downloads": [{"filepath": str(target)}], "id": "abc"}

        def prepare_filename(self, _info):
            return str(tmp_path / "source.mp4")

    module = types.ModuleType("yt_dlp")
    module.YoutubeDL = FakeYoutubeDL
    utils = types.ModuleType("yt_dlp.utils")
    utils.DownloadError = FakeDownloadError
    module.utils = utils
    monkeypatch.setitem(sys.modules, "yt_dlp", module)
    monkeypatch.setitem(sys.modules, "yt_dlp.utils", utils)
    monkeypatch.setattr(downloader, "validate_public_video_url", lambda value: value)
    messages = []
    result = downloader.download_video("https://www.youtube.com/watch?v=abcDEF12345", tmp_path, lambda p, m: messages.append(m))
    assert result == Path(tmp_path / "source.mp4")
    assert [attempt["format"] for attempt in attempts] == [
        "bv*[height<=1080]+ba/b[height<=1080]/b",
        "best[height<=1080]/best",
        "best",
    ]
    assert attempts[0]["js_runtimes"] == {"deno": {}, "node": {}}
    assert any("Videoformate" in message for message in messages)
