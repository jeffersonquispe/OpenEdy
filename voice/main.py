import asyncio
import os
import sys
from pathlib import Path

import anthropic as anthropic_sdk
from dotenv import load_dotenv

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))  # permite importar shared/ al lanzar el worker
load_dotenv()  # antes de importar shared.tools, que lee PLATFORM_URL al importarse

from livekit.agents import Agent, AgentSession, JobContext, WorkerOptions, cli, function_tool  # noqa: E402
from livekit.agents.types import APIConnectOptions  # noqa: E402
from livekit.agents.voice.agent_session import SessionConnectOptions  # noqa: E402
from livekit.plugins import anthropic, elevenlabs, google, silero  # noqa: E402

from api.config import positive_int  # noqa: E402
from shared.system_prompt import SYSTEM_PROMPT  # noqa: E402
from shared.tools import TOOL_IMPLEMENTATIONS, TOOLS  # noqa: E402

# Solo estilo para voz: se anexa al prompt compartido, no cambia ninguna regla de negocio.
VOICE_STYLE_NOTE = "\nComo es voz: frases cortas, sin markdown, sin listas largas y sin leer enlaces letra por letra."

GEMINI_MODEL = os.environ.get("GEMINI_MODEL", "gemini-3.6-flash")  # el mismo que usa el chat de texto
ANTHROPIC_MODEL = os.environ.get("ANTHROPIC_MODEL", "claude-sonnet-4-6")


def build_llm():
    # Claude es el LLM de la voz si hay clave; sin ella se usa Gemini, como el chat de texto.
    if os.environ.get("ANTHROPIC_API_KEY"):
        # Cliente propio: el plugin crea uno con httpx, que esta version del SDK de Anthropic (httpx2) rechaza.
        client = anthropic_sdk.AsyncAnthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
        return anthropic.LLM(model=ANTHROPIC_MODEL, client=client)
    return google.LLM(model=GEMINI_MODEL, api_key=os.environ.get("GEMINI_API_KEY"))

ELEVEN_KEY = os.environ.get("ELEVENLABS_API_KEY") or os.environ.get("ELEVEN_API_KEY")
MAX_SESSION_SECONDS = positive_int(os.environ.get("VOICE_MAX_SECONDS"), 300)
VOICE = {"voice_id": os.environ["VOICE_ID"]} if os.environ.get("VOICE_ID") else {}


def livekit_tools() -> list:
    # Envuelve las tools de shared/ (mismo esquema y misma implementacion que el chat) para LiveKit,
    # sin redefinirlas.
    def wrap(name: str, schema: dict):
        impl = TOOL_IMPLEMENTATIONS[name]

        async def handler(raw_arguments: dict):
            try:
                return await impl(**raw_arguments)
            except TypeError:
                return {"error": "Argumentos invalidos"}

        return function_tool(handler, raw_schema=schema)

    return [wrap(t["function"]["name"], t["function"]) for t in TOOLS]


async def end_after(ctx: JobContext, seconds: int) -> None:
    # El TTL del token solo limita la entrada: una conexion establecida se corta borrando la sala.
    await asyncio.sleep(seconds)
    await ctx.delete_room()
    ctx.shutdown(reason="duracion maxima alcanzada")


async def entrypoint(ctx: JobContext):
    session = AgentSession(
        stt=elevenlabs.STT(language_code="es", api_key=ELEVEN_KEY),
        llm=build_llm(),
        tts=elevenlabs.TTS(model="eleven_flash_v2_5", language="es", api_key=ELEVEN_KEY, **VOICE),
        vad=silero.VAD.load(),
        # Sin generacion anticipada: cada fragmento de voz gastaria una llamada a Gemini (cuota limitada).
        preemptive_generation=False,
        # Gemini tarda 7-9 s en responder (el limite por defecto es 10 s) y cada reintento consume cuota.
        conn_options=SessionConnectOptions(llm_conn_options=APIConnectOptions(timeout=30, max_retry=1)),
    )
    agent = Agent(instructions=SYSTEM_PROMPT + VOICE_STYLE_NOTE, tools=livekit_tools())
    await session.start(agent=agent, room=ctx.room)
    limit = asyncio.create_task(end_after(ctx, MAX_SESSION_SECONDS))

    async def cancel_limit() -> None:
        limit.cancel()

    ctx.add_shutdown_callback(cancel_limit)
    await session.generate_reply(instructions="Saluda brevemente como Edy y pregunta que quiere aprender.")


if __name__ == "__main__":
    cli.run_app(WorkerOptions(entrypoint_fnc=entrypoint))
