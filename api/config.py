import os
from dataclasses import dataclass
from urllib.parse import urlsplit


def origin_of(url: str) -> str | None:
    # Reduce una URL a su origen (esquema + host + puerto). Devuelve None si no es http(s) valida.
    parts = urlsplit(url.strip())
    if parts.scheme not in ("http", "https") or not parts.netloc:
        return None
    return f"{parts.scheme}://{parts.netloc}"


def parse_origins(raw: str | None, fallback: str) -> tuple[str, ...]:
    # Lista separada por comas; lo invalido se descarta para que nunca llegue a la CSP.
    candidates = raw.split(",") if raw and raw.strip() else [fallback]
    origins = [o for o in (origin_of(c) for c in candidates) if o]
    return tuple(dict.fromkeys(origins))


def positive_int(raw: str | None, default: int) -> int:
    try:
        value = int(raw or "")
    except ValueError:
        return default
    return value if value > 0 else default


@dataclass(frozen=True)
class Settings:
    platform_url: str
    embed_origins: tuple[str, ...]
    livekit_url: str
    livekit_api_key: str
    livekit_api_secret: str
    chat_limit_per_minute: int
    voice_limit_per_ip: int
    voice_limit_window_seconds: int
    voice_limit_global_per_hour: int

    @property
    def voice_enabled(self) -> bool:
        return bool(self.livekit_url and self.livekit_api_key and self.livekit_api_secret)


def load_settings(env=os.environ) -> Settings:
    platform_url = (env.get("PLATFORM_URL") or "http://localhost:3000").rstrip("/")
    return Settings(
        platform_url=platform_url,
        embed_origins=parse_origins(env.get("EMBED_ORIGINS"), platform_url),
        livekit_url=env.get("LIVEKIT_URL") or "",
        livekit_api_key=env.get("LIVEKIT_API_KEY") or "",
        livekit_api_secret=env.get("LIVEKIT_API_SECRET") or "",
        chat_limit_per_minute=positive_int(env.get("CHAT_LIMIT_PER_MINUTE"), 20),
        voice_limit_per_ip=positive_int(env.get("VOICE_LIMIT_PER_IP"), 5),
        voice_limit_window_seconds=positive_int(env.get("VOICE_LIMIT_WINDOW_SECONDS"), 600),
        voice_limit_global_per_hour=positive_int(env.get("VOICE_LIMIT_GLOBAL_PER_HOUR"), 60),
    )
