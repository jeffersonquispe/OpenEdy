import os
import sys
from pathlib import Path

from dotenv import load_dotenv

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))  # permite importar shared/ al lanzar el worker
load_dotenv()  # antes de importar shared.tools, que lee PLATFORM_URL al importarse

from livekit.agents import Agent, AgentSession, JobContext, WorkerOptions, cli, function_tool  # noqa: E402
from livekit.agents.types import APIConnectOptions  # noqa: E402
from livekit.agents.voice.agent_session import SessionConnectOptions  # noqa: E402
from livekit.plugins import elevenlabs, google, silero  # noqa: E402

from shared.system_prompt import SYSTEM_PROMPT  # noqa: E402
from shared.tools import TOOL_IMPLEMENTATIONS, TOOLS  # noqa: E402

# Solo estilo para voz: se anexa al prompt compartido, no cambia ninguna regla de negocio.
VOICE_STYLE_NOTE = "\nComo es voz: frases cortas, sin markdown, sin listas largas y sin leer enlaces letra por letra."

GEMINI_MODEL = os.environ.get("GEMINI_MODEL", "gemini-3.6-flash")  # el mismo que usa el chat de texto

ELEVEN_KEY = os.environ.get("ELEVENLABS_API_KEY") or os.environ.get("ELEVEN_API_KEY")
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


async def entrypoint(ctx: JobContext):
    session = AgentSession(
        stt=elevenlabs.STT(language_code="es", api_key=ELEVEN_KEY),
        llm=google.LLM(model=GEMINI_MODEL, api_key=os.environ.get("GEMINI_API_KEY")),
        tts=elevenlabs.TTS(model="eleven_flash_v2_5", language="es", api_key=ELEVEN_KEY, **VOICE),
        vad=silero.VAD.load(),
        # Sin generacion anticipada: cada fragmento de voz gastaria una llamada a Gemini (cuota limitada).
        preemptive_generation=False,
        # Gemini tarda 7-9 s en responder (el limite por defecto es 10 s) y cada reintento consume cuota.
        conn_options=SessionConnectOptions(llm_conn_options=APIConnectOptions(timeout=30, max_retry=1)),
    )
    agent = Agent(instructions=SYSTEM_PROMPT + VOICE_STYLE_NOTE, tools=livekit_tools())
    await session.start(agent=agent, room=ctx.room)
    await session.generate_reply(instructions="Saluda brevemente como Edy y pregunta que quiere aprender.")


if __name__ == "__main__":
    cli.run_app(WorkerOptions(entrypoint_fnc=entrypoint))
