from tiktok import FERNET_PREFIX, TikTokClient, redirect_uri


def test_credentials_are_encrypted_and_readable_again(tmp_path, monkeypatch):
    monkeypatch.setattr("tiktok.sys.platform", "linux")
    client = TikTokClient(tmp_path)
    client.configure("abcd1234efgh", "super-geheim")
    stored = (tmp_path / "tiktok_config.json").read_text(encoding="utf-8")
    assert "super-geheim" not in stored and FERNET_PREFIX in stored
    assert client._read_config()["client_secret"] == "super-geheim"
    client._save_token({"access_token": "a", "refresh_token": "r", "expires_in": 3600, "refresh_expires_in": 86400})
    assert client.status()["connected"] is True
    client.logout()
    assert client.status()["connected"] is False


def test_redirect_uri_follows_server_domain(monkeypatch):
    monkeypatch.delenv("LOCALCLIP_PUBLIC_URL", raising=False)
    monkeypatch.delenv("LOCALCLIP_DOMAIN", raising=False)
    assert redirect_uri() == "http://127.0.0.1:8765/auth/tiktok/callback"
    monkeypatch.setenv("LOCALCLIP_DOMAIN", "example.test")
    assert redirect_uri() == "https://example.test/auth/tiktok/callback"
    monkeypatch.setenv("LOCALCLIP_PUBLIC_URL", "https://clips.example.test/")
    assert redirect_uri() == "https://clips.example.test/auth/tiktok/callback"
