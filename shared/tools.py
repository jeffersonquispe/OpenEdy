import os

import httpx

PLATFORM_URL = os.environ.get("PLATFORM_URL", "http://localhost:3000")
MAX_QUERY_CHARS = 200  # el mismo limite que aplica la plataforma


async def search_courses(query: str) -> dict:
    """Busca cursos publicados por similitud semantica en la plataforma (solo por HTTP)."""
    # El modelo genera estos argumentos: se validan igual que si vinieran de un usuario.
    query = query.strip() if isinstance(query, str) else ""
    if not query or len(query) > MAX_QUERY_CHARS:
        return {"error": f"query debe ser texto de 1 a {MAX_QUERY_CHARS} caracteres."}

    try:
        async with httpx.AsyncClient(timeout=15) as client:
            r = await client.get(f"{PLATFORM_URL}/api/courses/search", params={"q": query})
            r.raise_for_status()
            return r.json()
    except httpx.HTTPError:
        return {"error": "No pude consultar el catalogo en este momento."}


# Esquema en formato OpenAI (la API de NVIDIA es compatible).
TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "search_courses",
            "description": "Busca en el catalogo real de Eduplatform los cursos publicados mas relevantes para lo que la persona escribio.",
            "parameters": {
                "type": "object",
                "properties": {"query": {"type": "string", "description": "Frase de busqueda en lenguaje natural"}},
                "required": ["query"],
            },
        },
    }
]

TOOL_IMPLEMENTATIONS = {"search_courses": search_courses}
