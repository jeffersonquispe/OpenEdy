import dataclasses

import jwt
import pytest

from api import main

SECRET = "secreto-de-prueba-suficientemente-largo-0123456789"


@pytest.fixture
def voice_settings(monkeypatch):
    settings = dataclasses.replace(
        main.settings, livekit_url="wss://voz.example", livekit_api_key="clave", livekit_api_secret=SECRET
    )
    monkeypatch.setattr(main, "settings", settings)
    return settings


def claims(token: str) -> dict:
    return jwt.decode(token, SECRET, algorithms=["HS256"], options={"verify_aud": False})


def test_sin_configuracion_responde_503(client, monkeypatch):
    monkeypatch.setattr(main, "settings", dataclasses.replace(main.settings, livekit_url=""))
    assert client.post("/voice/token").status_code == 503


def test_token_limitado_a_una_sala_nueva_y_sin_claves(client, voice_settings):
    first, second = client.post("/voice/token").json(), client.post("/voice/token").json()
    assert set(first) == {"url", "token"} and first["url"] == "wss://voz.example"
    assert SECRET not in str(first)

    grants = claims(first["token"])
    video = grants["video"]
    assert video["roomJoin"] is True and video["room"].startswith("edy-")
    assert video["canPublishSources"] == ["microphone"] and video["canPublishData"] is False
    assert not video.get("roomAdmin") and not video.get("roomCreate")
    assert grants["exp"] - grants["nbf"] == 600

    other = claims(second["token"])
    assert other["video"]["room"] != video["room"] and other["sub"] != grants["sub"]


def test_limite_por_ip_responde_429(client, voice_settings):
    for _ in range(main.voice_ip_limiter.limit):
        assert client.post("/voice/token").status_code == 200
    assert client.post("/voice/token").status_code == 429


def test_limite_global_responde_429(client, voice_settings, monkeypatch):
    monkeypatch.setattr(main.voice_global_limiter, "limit", 1)
    assert client.post("/voice/token").status_code == 200
    r = client.post("/voice/token")
    assert r.status_code == 429 and "token" not in r.json()
