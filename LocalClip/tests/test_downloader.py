from unittest.mock import patch

import pytest

from downloader import validate_public_video_url


@patch("downloader.socket.getaddrinfo", return_value=[(2, 1, 6, "", ("142.250.185.238", 443))])
def test_public_url_is_accepted(_lookup):
    assert validate_public_video_url("https://www.youtube.com/watch?v=abc") == "https://www.youtube.com/watch?v=abc"


@patch("downloader.socket.getaddrinfo", return_value=[(2, 1, 6, "", ("127.0.0.1", 443))])
def test_private_url_is_rejected(_lookup):
    with pytest.raises(ValueError):
        validate_public_video_url("http://example.test/video")

