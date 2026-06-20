import importlib

from media import config


def test_int_env_returns_default_when_unset(monkeypatch):
    monkeypatch.delenv("SOME_VAR", raising=False)
    assert config._int_env("SOME_VAR", 42) == 42


def test_int_env_parses_value(monkeypatch):
    monkeypatch.setenv("SOME_VAR", "123")
    assert config._int_env("SOME_VAR", 42) == 123


def test_int_env_falls_back_on_garbage(monkeypatch):
    monkeypatch.setenv("SOME_VAR", "not-a-number")
    assert config._int_env("SOME_VAR", 42) == 42


def test_int_env_falls_back_on_blank(monkeypatch):
    monkeypatch.setenv("SOME_VAR", "   ")
    assert config._int_env("SOME_VAR", 42) == 42


def test_default_constants():
    assert config.MAX_UPLOAD_BYTES == 10 * 1024 * 1024
    assert config.COMPRESS_TARGET == int(config.MAX_UPLOAD_BYTES * 0.95)
    assert config.FFMPEG_TIMEOUT_SECONDS == 300
    assert config.MAX_COMPRESS_INPUT_BYTES == 500 * 1024 * 1024
    assert config.VIDEO_AUDIO_BITRATE == 128_000
    assert config.VIDEO_MIN_BITRATE == 125_000
    assert config.AUDIO_MIN_BITRATE == 32_000
    assert config.AUDIO_MAX_BITRATE == 192_000


def test_env_override_via_reload(monkeypatch):
    monkeypatch.setenv("MAX_UPLOAD_BYTES", "26214400")
    reloaded = importlib.reload(config)
    try:
        assert reloaded.MAX_UPLOAD_BYTES == 26214400
        assert reloaded.COMPRESS_TARGET == int(26214400 * 0.95)
    finally:
        monkeypatch.delenv("MAX_UPLOAD_BYTES", raising=False)
        importlib.reload(config)
