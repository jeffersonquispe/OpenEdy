import pytest
from fastapi.testclient import TestClient

from api import main


@pytest.fixture(autouse=True)
def fresh_limiters():
    # Los contadores viven en el modulo: cada prueba empieza de cero.
    for limiter in (main.chat_limiter, main.voice_ip_limiter, main.voice_global_limiter):
        limiter.windows.clear()


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "clave-falsa")
    return TestClient(main.app)


@pytest.fixture
def model_calls(monkeypatch):
    """Sustituye al modelo y guarda los mensajes que recibiria; las pruebas no usan la red."""
    calls = []

    class Message:
        content = "respuesta"
        tool_calls = None

        def model_dump(self, exclude_none=True):
            return {"role": "assistant", "content": self.content}

    class Choice:
        finish_reason = "stop"
        message = Message()

    class Response:
        choices = [Choice()]

    async def fake_complete(providers, messages):
        calls.append(list(messages))
        return Response()

    monkeypatch.setattr(main, "complete", fake_complete)
    return calls
