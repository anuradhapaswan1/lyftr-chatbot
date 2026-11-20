# LLM Chatbot with Key Failover and Response Caching

FastAPI + LiteLLM backend, React frontend, SQLite storage.

## 1. Live URLs
- Frontend: `<add after deploy>`
- Backend: `<add after deploy>` (try `/health`)
- Demo video: `<add link>`

## 2. Run locally

**With Docker** (frontend :3000, backend :8000)
```bash
cp .env.example .env     # put 2+ real keys in LLM_API_KEYS
make up                  # make down to stop
```
**Without Docker**
```bash
cd backend && pip install -r requirements-dev.txt
LLM_API_KEYS=k1,k2 DATABASE_URL=sqlite:///./app.db CACHE_DIR=./.cache \
  ENABLE_FAILURE_SIMULATION=true uvicorn app.main:app --port 8000
cd frontend && npm install && VITE_API_URL=http://localhost:8000 npm run dev   # :3000
make test                # backend tests (offline, no real keys needed)
```

## 3. Design decisions
**Key fallback.** `LLM_API_KEYS` becomes the ordered pool `key_1..key_n`. Each request calls `litellm.acompletion` with `key_1`; on *any* failure (429, quota, 5xx, timeout, auth, empty completion) it moves to the next key. LiteLLM's own retries are off (`num_retries=0`) so one failure means one fallback and `fallback_attempts` is exact. If every key fails, the API returns `503 {"detail":"all providers unavailable"}` and nothing is saved. Keys are never logged or returned; only `key_N` ids are.
`X-Simulate-Failure` (only when `ENABLE_FAILURE_SIMULATION=true`) marks listed keys as failed for that request, then the remaining keys make a real call. While simulating, the cache *read* is skipped so the demo always exercises failover.

**Caching.** LiteLLM's built-in cache (`litellm.cache`), on disk under `/data/llm_cache` by default (survives restarts; `CACHE_TYPE=local` for in-memory). LiteLLM's cache key is model + the full message list (system + history + new message), not the API key, so the same first message in a new session hits the cache and any different message misses. Hits are detected from LiteLLM's `cache_hit` flag; the stored reply and token usage are returned, `key_id` is `null`.

**SQLite sessions.** `messages` table exactly as specified (+ an index), plus a `sessions` table that remembers the session's system prompt. Schema is created on startup at `DATABASE_URL`, under the `/data` volume. A user/assistant pair is written in one transaction only after success, so failed requests (422/503) leave no trace. The last `MAX_HISTORY_MESSAGES` (50) are sent to the model to bound cost. Token counts come from the provider's usage for that request only.

## 4. Verification
```bash
B=http://localhost:8000
curl $B/health
# live, then cache hit from a NEW session (same message)
curl -s $B/chat -H 'content-type: application/json' -d '{"session_id":"a","message":"What is the capital of France?"}'
curl -s $B/chat -H 'content-type: application/json' -d '{"session_id":"b","message":"What is the capital of France?"}'   # LLM_CACHE, key_id null
# failover
curl -s $B/chat -H 'content-type: application/json' -H 'X-Simulate-Failure: key_1=429' -d '{"session_id":"c","message":"hello"}'   # key_2, fallback_attempts 1
curl -si $B/chat -H 'content-type: application/json' -H 'X-Simulate-Failure: *=429' -d '{"session_id":"d","message":"hi"}'        # 503
curl -s $B/sessions/c/messages        # stored history, 404 for unknown ids
```
In the UI, open *Session settings* to pick a simulated failure; each reply shows source, model, latency and that request's input/output tokens (plus which key answered).

## 5. Setup Used
- Editor / AI tools: `<fill in, e.g. VS Code + Claude>`

## 6. Bonus features
- In-UI failover simulator (header selector) for easy demos.
- Persistent disk cache, per-session system prompt stored server-side, runtime-configurable frontend image (`API_URL`), container healthcheck, 23 offline backend tests that run LiteLLM's real cache path through a fake provider.

## Deploying
- **Backend:** Render/Fly/Railway/HF Spaces from `backend/` (Dockerfile honors `$PORT`). Set `LLM_API_KEYS`, `ALLOWED_ORIGINS=<frontend URL>`, `ENABLE_FAILURE_SIMULATION=true`, `DATABASE_URL`. For history to survive restarts, attach a persistent disk/volume mounted at `/data` (Fly volumes or a Render disk; free tiers without one lose data on redeploy).
- **Frontend:** Vercel/Netlify with root `frontend`, build `npm run build`, output `dist`, env `VITE_API_URL=<backend URL>` (build-time). Or deploy `frontend/Dockerfile` with runtime `API_URL`.
