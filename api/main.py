import json
import logging
import os

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from openai import APIError, AsyncOpenAI
from pydantic import BaseModel, ConfigDict, Field

load_dotenv()  # antes de importar shared.tools, que lee PLATFORM_URL al importarse

from shared.system_prompt import SYSTEM_PROMPT  # noqa: E402
from shared.tools import TOOL_IMPLEMENTATIONS, TOOLS  # noqa: E402

log = logging.getLogger("edy")
app = FastAPI()

MAX_TURNS = 4
MAX_MESSAGE_CHARS = 1000

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


class ChatRequest(BaseModel):
    # FastAPI responde 422 con el detalle si el cuerpo no cumple: JSON invalido, sin message,
    # message que no es texto, vacio, demasiado largo o con campos extra.
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    message: str = Field(min_length=1, max_length=MAX_MESSAGE_CHARS)


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


@app.post("/chat")
async def chat(body: ChatRequest):
    providers = configured_providers()
    messages = [{"role": "system", "content": SYSTEM_PROMPT}, {"role": "user", "content": body.message}]

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
