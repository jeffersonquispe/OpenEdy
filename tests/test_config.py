from api.config import load_settings, parse_origins, positive_int
from api.rate_limit import FixedWindowLimiter


def test_origenes_por_defecto_salen_de_platform_url():
    assert load_settings({"PLATFORM_URL": "https://cursos.example/"}).embed_origins == ("https://cursos.example",)


def test_origenes_reducidos_sin_duplicados_ni_valores_invalidos():
    raw = "https://a.example/ruta, http://localhost:3000,javascript:alert(1),,https://a.example"
    assert parse_origins(raw, "http://x") == ("https://a.example", "http://localhost:3000")


def test_enteros_invalidos_usan_el_valor_por_defecto():
    assert positive_int("abc", 7) == 7
    assert positive_int("-1", 7) == 7
    assert positive_int(None, 7) == 7
    assert positive_int("12", 7) == 12


def test_voz_requiere_las_tres_variables():
    assert not load_settings({"LIVEKIT_URL": "wss://x", "LIVEKIT_API_KEY": "k"}).voice_enabled
    assert load_settings({"LIVEKIT_URL": "wss://x", "LIVEKIT_API_KEY": "k", "LIVEKIT_API_SECRET": "s"}).voice_enabled


def test_limitador_se_reinicia_con_la_ventana_y_limpia_claves():
    now = [0.0]
    limiter = FixedWindowLimiter(2, 60, clock=lambda: now[0])
    assert limiter.allow("a") and limiter.allow("a")
    assert not limiter.allow("a")
    assert limiter.allow("b")
    now[0] = 61
    assert limiter.allow("a")
    assert "b" not in limiter.windows
