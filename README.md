# edy-service

Microservicio de Edy, el asistente de Eduplatform. Solo consume la plataforma por HTTP
(`GET {PLATFORM_URL}/api/courses/search?q=`); no toca Supabase.

```bash
pip install -r requirements.txt
cp .env.example .env   # y completa GEMINI_API_KEY (principal) y NVIDIA_API_KEY (respaldo)
uvicorn api.main:app --reload
curl -X POST localhost:8000/chat -H "Content-Type: application/json" \
  -d '{"message":"quiero aprender marketing digital"}'
```

`shared/` (prompt y tools) queda listo para reutilizarse en un canal de voz.
