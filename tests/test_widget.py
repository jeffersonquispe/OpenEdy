import dataclasses

from fastapi.testclient import TestClient

from api import main


def test_config_expone_solo_datos_publicos(client):
    body = client.get("/config").json()
    assert set(body) == {"platformUrl", "embedOrigins", "voiceEnabled"}
    assert isinstance(body["voiceEnabled"], bool)


def test_widget_se_sirve_con_cualquier_directorio_de_trabajo(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    r = TestClient(main.app).get("/widget/")
    assert r.status_code == 200 and "text/html" in r.headers["content-type"]


def test_csp_limita_quien_embebe_y_a_donde_conecta(client, monkeypatch):
    monkeypatch.setattr(
        main,
        "settings",
        dataclasses.replace(main.settings, embed_origins=("https://cursos.example",), livekit_url="wss://voz.example"),
    )
    csp = client.get("/widget/").headers["content-security-policy"]
    assert "frame-ancestors https://cursos.example" in csp
    assert "connect-src 'self' https://voz.example wss://voz.example" in csp
    assert "default-src 'self'" in csp and "unsafe" not in csp


def test_sin_origenes_nadie_puede_embeber(client, monkeypatch):
    monkeypatch.setattr(main, "settings", dataclasses.replace(main.settings, embed_origins=()))
    assert "frame-ancestors 'none'" in client.get("/widget/").headers["content-security-policy"]


def test_api_no_lleva_la_csp_del_widget(client):
    assert "content-security-policy" not in client.get("/config").headers
