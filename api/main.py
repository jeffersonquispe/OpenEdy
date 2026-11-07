import json
import logging
import os
import uuid
from datetime import timedelta
from pathlib import Path
from typing import Literal

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Request
from fastapi.staticfiles import StaticFiles
from livekit import api as livekit_api
from openai import APIError, AsyncOpenAI
from pydantic import BaseModel, ConfigDict, Field

load_dotenv()  # antes de importar shared.tools, que lee PLATFORM_URL al importarse

from api.config import load_settings, origin_of  # noqa: E402
from api.rate_limit import FixedWindowLimiter  # noqa: E402
from shared.system_prompt import SYSTEM_PROMPT  # noqa: E402
from shared.tools import TOOL_IMPLEMENTATIONS, TOOLS  # noqa: E402

log = logging.getLogger("edy")
app = FastAPI()

MAX_TURNS = 4
MAX_MESSAGE_CHARS = 1000
MAX_HISTORY_TURNS = 12
MAX_HISTORY_TURN_CHARS = 2000
VOICE_TOKEN_TTL = timedelta(minutes=10)
WIDGET_DIR = Path(__file__).resolve().parent.parent / "widget"

settings = load_settings()
chat_limiter = FixedWindowLimiter(settings.chat_limit_per_minute, 60)
voice_ip_limiter = FixedWindowLimiter(settings.voice_limit_per_ip, settings.voice_limit_window_seconds)
voice_global_limiter = FixedWindowLimiter(settings.voice_limit_global_per_hour, 3600)

# Proveedores en orden de preferencia: el primero con clave es el principal y los siguientes son
# respaldo. Ambos exponen una API compatible con el SDK de OpenAI, asi que comparten el mismo loop.
PROVIDERS = [
    {
        "name": "gemini",
        "key_env": "GEMINI_API_KEY",
        "base_url": "https://generativelanguage.googleapis.com/v1beta/openai/",
        "model": os.environ.get("GEMINI_MODEL", "gemini-3.6-flash"),
        "timeout": 30,
        "extra": {},
    },
    {
        "name": "nvidia",
        "key_env": "NVIDIA_API_KEY",
        "base_url": "https://integrate.api.nvidia.com/v1",
        # Modelo Mistral alojado en NVIDIA (build.nvidia.com). Debe soportar tool use.
        "model": os.environ.get("NVIDIA_MODEL", "mistralai/mistral-large-4-1t-a52b"),
        # Este modelo razona: con el esfuerzo por defecto una respuesta supera los 100 s; "low" funciona con tools.
        # Aun asi cada llamada tarda ~35 s, de ahi el timeout largo.
        "timeout": 120,
        "extra": {"reasoning_effort": os.environ.get("NVIDIA_REASONING_EFFORT", "low")},
    },
]


class HistoryTurn(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    # Solo texto de persona y asistente: el cliente no puede inyectar turnos de sistema ni de tools.
    role: Literal["user", "assistant"]
    content: str = Field(min_length=1, max_length=MAX_HISTORY_TURN_CHARS)


class ChatRequest(BaseModel):
    # FastAPI responde 422 con el detalle si el cuerpo no cumple: JSON invalido, sin message,
    # message que no es texto, vacio, demasiado largo, historial invalido o con campos extra.
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    message: str = Field(min_length=1, max_length=MAX_MESSAGE_CHARS)
    history: list[HistoryTurn] = Field(default_factory=list, max_length=MAX_HISTORY_TURNS)


def client_ip(request: Request) -> str:
    # Detras de un proxy, uvicorn la resuelve con --proxy-headers y --forwarded-allow-ips.
    return request.client.host if request.client else "desconocido"


def widget_csp() -> str:
    livekit_sources = []
    origin = origin_of(settings.livekit_url.replace("wss://", "https://", 1).replace("ws://", "http://", 1))
    if origin:
        host = origin.split("://", 1)[1]
        socket_scheme = "wss" if origin.startswith("https://") else "ws"
        livekit_sources = [origin, f"{socket_scheme}://{host}"]
    ancestors = " ".join(settings.embed_origins) or "'none'"
    return "; ".join(
        [
            "default-src 'self'",
            " ".join(["connect-src 'self'", *livekit_sources]),
            "media-src 'self' blob:",
            "img-src 'self' data:",
            f"frame-ancestors {ancestors}",
            "base-uri 'none'",
            "form-action 'self'",
        ]
    )


@app.middleware("http")
async def widget_headers(request: Request, call_next):
    response = await call_next(request)
    if request.url.path == "/widget" or request.url.path.startswith("/widget/"):
        response.headers["Content-Security-Policy"] = widget_csp()
    return response


def configured_providers() -> list[dict]:
    # Las claves salen del entorno, nunca se hardcodean. Los clientes se crean al usarse (no al importar)
    # para que un .env incompleto dé un error claro, no un crash al arrancar.
    found = [p for p in PROVIDERS if os.environ.get(p["key_env"])]
    if not found:
        names = " o ".join(p["key_env"] for p in PROVIDERS)
        raise HTTPException(status_code=500, detail=f"Falta {names}")
    return found


def for_provider(messages: list[dict], provider: dict) -> list[dict]:
    # `extra_content` lleva la firma de razonamiento de Gemini en cada tool call: solo Gemini la entiende,
    # y quitarla evita que NVIDIA rechace la conversacion si toma el relevo a mitad de camino.
    if provider["name"] == "gemini":
        return messages
    return [
        {**m, "tool_calls": [{k: v for k, v in c.items() if k != "extra_content"} for c in m["tool_calls"]]}
        if m.get("tool_calls")
        else m
        for m in messages
    ]


async def complete(providers: list[dict], messages: list[dict]):
    """Una llamada al modelo: prueba cada proveedor en orden y pasa al siguiente si uno falla."""
    last_error: APIError | None = None
    for p in providers:
        client = AsyncOpenAI(base_url=p["base_url"], api_key=os.environ[p["key_env"]], timeout=p["timeout"], max_retries=0)
        try:
            return await client.chat.completions.create(
                model=p["model"], max_tokens=1024, tools=TOOLS, messages=for_provider(messages, p), **p["extra"]
            )
        except APIError as e:  # incluye timeout, conexion, 4xx y 5xx
            log.warning("Proveedor %s fallo (%s); probando el siguiente", p["name"], type(e).__name__)
            last_error = e
    raise HTTPException(status_code=502, detail=f"El modelo no respondio ({type(last_error).__name__})")


@app.get("/config")
async def config():
    return {
        "platformUrl": settings.platform_url,
        "embedOrigins": list(settings.embed_origins),
        "voiceEnabled": settings.voice_enabled,
    }


@app.post("/voice/token")
async def voice_token(request: Request):
    if not settings.voice_enabled:
        raise HTTPException(status_code=503, detail="La voz no esta disponible")
    # Primero el limite por IP: asi un solo cliente no agota el total por hora.
    if not voice_ip_limiter.allow(client_ip(request)) or not voice_global_limiter.allow("total"):
        raise HTTPException(status_code=429, detail="Demasiadas sesiones de voz")

    room = f"edy-{uuid.uuid4()}"
    token = (
        livekit_api.AccessToken(settings.livekit_api_key, settings.livekit_api_secret)
        .with_identity(f"visitante-{uuid.uuid4()}")
        .with_ttl(VOICE_TOKEN_TTL)
        .with_grants(
            livekit_api.VideoGrants(
                room_join=True,
                room=room,
                can_publish=True,
                can_subscribe=True,
                can_publish_data=False,
                can_publish_sources=["microphone"],
            )
        )
        .to_jwt()
    )
    return {"url": settings.livekit_url, "token": token}


@app.post("/chat")
async def chat(body: ChatRequest, request: Request):
    if not chat_limiter.allow(client_ip(request)):
        raise HTTPException(status_code=429, detail="Demasiados mensajes")

    providers = configured_providers()
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        *[{"role": turn.role, "content": turn.content} for turn in body.history],
        {"role": "user", "content": body.message},
    ]

    for _ in range(MAX_TURNS):
        response = await complete(providers, messages)
        choice = response.choices[0]
        # model_dump conserva campos extra del proveedor (p. ej. la firma de razonamiento de Gemini).
        assistant = choice.message.model_dump(exclude_none=True)
        messages.append({k: v for k, v in assistant.items() if k in ("role", "content", "tool_calls")})

        if choice.finish_reason != "tool_calls" or not choice.message.tool_calls:
            return {"reply": choice.message.content or ""}

        for call in choice.message.tool_calls:
            impl = TOOL_IMPLEMENTATIONS.get(call.function.name)
            try:
                args = json.loads(call.function.arguments or "{}")
                result = await impl(**args) if impl else {"error": "Tool desconocida"}
            except (TypeError, ValueError):
                result = {"error": "Argumentos invalidos"}
            messages.append({"role": "tool", "tool_call_id": call.id, "content": json.dumps(result, ensure_ascii=False)})

    return {"reply": "No pude completar la respuesta."}


# Al final, para que el montaje no tape ninguna ruta. La ruta sale de este archivo, no del directorio de trabajo.
app.mount("/widget", StaticFiles(directory=WIDGET_DIR, html=True), name="widget")
