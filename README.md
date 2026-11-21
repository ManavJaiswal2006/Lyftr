# Lyftr Chat

A React chatbot with FastAPI, LiteLLM key failover, per-request response metadata, and conversation history in SQLite.

## Live URLs

Add the public frontend, backend, and demo video URLs here after deployment. The backend health check is at `/health`.

The [Render Blueprint](https://render.com/deploy?repo=https://github.com/ManavJaiswal2006/Lyftr) creates a public Docker backend and a public React static site. It links the service URLs for the frontend build and backend CORS. The backend uses a paid Starter instance and a 1 GB persistent disk at `/data` so SQLite history survives redeploys.

1. Open the Blueprint link, authorize Render to access the GitHub repo, and review the resources and pricing.
2. Enter two comma-separated Gemini keys for `LLM_API_KEYS` in Render's setup form. The default model is `gemini/gemini-2.5-flash-lite`. If using another provider, set `LLM_MODEL` on the backend service.
3. After both deployments complete, open their public URLs. Verify the backend's `/health` endpoint and send a chat from the frontend before recording the demo.

Use keys from separate quota pools if the provider applies rate limits per project or account. Store keys only in backend environment settings.

## Run locally: with Docker and without

Copy `.env.example` to `.env` and replace the two placeholder values in `LLM_API_KEYS` with real keys for the configured model.

**With Docker**

```sh
make up
docker compose ps
curl -fsS http://localhost:8000/health
```

Open [http://localhost:3000](http://localhost:3000). `make down` stops both services and preserves history in the named `chat_data` volume.

**Without Docker**

Use Python 3.12+ and Node 22.12+. In one terminal, from the repo root:

```sh
python -m venv .venv
.venv/bin/pip install -r backend/requirements-dev.txt
DATABASE_URL=sqlite:///app.db LITELLM_LOCAL_MODEL_COST_MAP=True \
  .venv/bin/uvicorn --app-dir backend main:app --env-file .env --port 8000
```

In a second terminal:

```sh
cd frontend
npm ci
VITE_API_BASE_URL=http://localhost:8000 npm run dev -- --port 3000
```

The backend integration tests start a local HTTP provider and exercise LiteLLM without real credentials:

```sh
cd backend
LITELLM_LOCAL_MODEL_COST_MAP=True ../.venv/bin/python -m pytest -q
```

## Design decisions: key fallback, caching, SQLite session storage

- **Fallback.** Each request tries the configured keys in order using LiteLLM's async completion client, with a per-attempt timeout and retries disabled. LiteLLM provider errors advance to the next key. On success, the backend reports the key alias and the number of prior failures. Exhaustion returns a JSON 503. The simulation header injects the selected status before contacting that provider.
- **Cache.** LiteLLM's built-in in-memory cache stores up to 1,024 successful answers for up to one hour. Its key hashes the model name and full ordered message list, including the system prompt and prior turns. Cache hits reuse the answer and its original token usage. Simulations bypass reads so that a previously cached answer cannot mask a provider failure. The cache resets on backend restart.
- **Storage.** SQLite creates its schema at startup, and the database lives on a Docker volume. Each successful turn writes the session prompt and both messages in one transaction. Per-session requests are serialized within a single backend worker, and reads see a consistent snapshot. The frontend generates a random session ID on each page load and sends only the new message.
- **Metadata.** Usage comes from the provider response, with LiteLLM token counting as a fallback when usage is missing. Server latency is measured from chat handler entry through answer resolution. Identical prompts across sessions can share a cache entry.

## Verification: how to see failover, cache hits and per-message token usage

Run these commands in a Bash shell with a configured backend and `curl`. Change `API` to your deployed backend to verify the public submission.

```sh
API=http://localhost:8000
RUN_ID=$(date +%s)
ASK="What is the capital of France? Reply briefly. Verification run $RUN_ID."

curl -fsS "$API/health"

curl -fsS "$API/chat" -H 'Content-Type: application/json' \
  -d "{\"session_id\":\"live-$RUN_ID\",\"message\":\"$ASK\"}"

curl -fsS "$API/chat" -H 'Content-Type: application/json' \
  -d "{\"session_id\":\"cache-$RUN_ID\",\"message\":\"$ASK\"}"

curl -fsS "$API/chat" -H 'Content-Type: application/json' \
  -H 'X-Simulate-Failure: key_1=429' \
  -d "{\"session_id\":\"fallback-$RUN_ID\",\"message\":\"$ASK\"}"

curl -sS -w '\nHTTP %{http_code}\n' "$API/chat" -H 'Content-Type: application/json' \
  -H 'X-Simulate-Failure: *=429' \
  -d "{\"session_id\":\"failed-$RUN_ID\",\"message\":\"$ASK\"}"

curl -fsS "$API/sessions/live-$RUN_ID/messages"
curl -sS -w '\nHTTP %{http_code}\n' "$API/sessions/failed-$RUN_ID/messages"
```

Expect `LIVE_INFERENCE` on the first call and `LLM_CACHE` on the second, with identical reply and token counts. The failover call should report `key_2` and one fallback. With every key simulated down, expect 503 and a 404 for the failed session's history. To see per-request token counts in the UI, send a follow-up and compare the input/output badges on the two replies.

**Demo recording outline (2–3 minutes)**

- **0:00–0:40** Open the public frontend. Send “My favorite city is Kyoto” and then “What is my favorite city?” Show the response badges.
- **0:40–1:10** Start a new chat, repeat the first message, and show `LLM_CACHE`.
- **1:10–1:50** Open Settings, select the first-key 429 simulation, and send a prompt. Show the live response, the second key, and one fallback. Select the all-key simulation and show the readable error.
- **1:50–2:30** Use the public backend URL to show `/health`, `POST /chat` and `GET /sessions/{session_id}/messages`. Narrate the result of each step.

## Setup Used: your editor and AI tools

OpenAI Codex in a Linux workspace for implementation and verification. The stack was tested with pytest, Docker Compose, and Chromium.

## Bonus features (if any): what you added beyond the requirements and why

- Session settings can override the system prompt and trigger the demo failure cases from the browser. The backend must have failure simulation enabled.
- Assistant replies render Markdown, including fenced code blocks and tables.
