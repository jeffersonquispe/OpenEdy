# edy-service

Microservicio de Edy, el asistente de EduTech. Solo consume la plataforma por HTTP
(`GET {PLATFORM_URL}/api/courses/search?q=`); no toca Supabase. Responde sobre el catalogo publico:
no tiene acceso al contenido de las lecciones.

```bash
pip install -r requirements.txt
cp .env.example .env   # y completa las claves
uvicorn api.main:app --reload
curl -X POST localhost:8000/chat -H "Content-Type: application/json" \
  -d '{"message":"quiero aprender marketing digital"}'
```

## Procesos

| Proceso | Comando | Para que |
|---|---|---|
| API y widget | `uvicorn api.main:app` | `/chat`, `/voice/token`, `/config` y `/widget/` |
| Worker de voz | `python -m voice.main dev --no-reload` | Atiende las salas de LiveKit; sin el, la voz termina con "no disponible" a los 15 s |

## Widget

`widget/` se sirve en `/widget/` y se embebe en un iframe desde la plataforma (variable
`NEXT_PUBLIC_EDY_URL` del repo de la plataforma). No necesita compilacion. `widget/tokens.css` es una copia
de los tokens de la plataforma, y `widget/vendor/` contiene `livekit-client` y las fuentes Geist.

La pagina solo puede embeberse desde `EMBED_ORIGINS` (por defecto, `PLATFORM_URL`). Debe incluir el
origen exacto de la plataforma; si no, el navegador bloquea el iframe y el panel muestra un error.

## Variables

Ademas de las claves de los proveedores (`.env.example`):

| Variable | Por defecto | Uso |
|---|---|---|
| `PLATFORM_URL` | `http://localhost:3000` | Catalogo y enlaces de las respuestas |
| `EMBED_ORIGINS` | `PLATFORM_URL` | Origenes autorizados para embeber, separados por coma |
| `VOICE_MAX_SECONDS` | `300` | Duracion maxima de una sesion de voz |
| `CHAT_LIMIT_PER_MINUTE` | `20` | Mensajes por IP |
| `VOICE_LIMIT_PER_IP` / `VOICE_LIMIT_WINDOW_SECONDS` | `5` / `600` | Sesiones de voz por IP |
| `VOICE_LIMIT_GLOBAL_PER_HOUR` | `60` | Total de sesiones de voz nuevas |

## Produccion

- **HTTPS obligatorio:** el navegador no permite usar el microfono sin un contexto seguro.
- **Una sola instancia:** los limites viven en memoria; con varias, cada una cuenta por su lado.
- **IP real detras de un proxy:** usa `uvicorn --proxy-headers --forwarded-allow-ips=<ip del proxy>`. Sin
  eso, todos los clientes comparten el limite; con `*`, un cliente puede falsificar su IP.

## Pruebas

```bash
pip install -r requirements-dev.txt
pytest
```

No usan la red ni claves reales.
