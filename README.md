# FitGen for Alexa+

Voice-first AI strength coach, delivered as a self-hosted MCP server
(MCP spec 2025-11-25, Streamable HTTP) with an MCP Apps widget.

Work in progress. Full docs land in Phase 9.

## Local dev

```bash
cp .env.example .env
docker compose -f infra/docker-compose.yml up -d   # Postgres 16 on localhost:5434
uv sync
uv run python -m server.db.seed --reset           # create tables + demo user with 8 weeks of history
uv run ruff check .
uv run pytest                                      # DB tests use the fitgen_test database
```

### Database schema

No Alembic (a deliberate hackathon choice): tables are created from the SQLAlchemy models
with `create_all`. After any model change, rebuild and reseed:

```bash
uv run python -m server.db.seed --reset
```

This wipes all data. Add Alembic (with a baseline migration from the models) before there
is real user data to preserve.

Run the MCP server (Streamable HTTP at `http://localhost:8000/mcp`):

```bash
uv run uvicorn server.app:app --port 8000
```

Connect with MCP Inspector:

```bash
npx @modelcontextprotocol/inspector
```

In the Inspector UI choose transport **Streamable HTTP**, URL `http://localhost:8000/mcp`,
then Connect → Tools → `ping`. With `AUTH_MODE=api_key`, add an `x-api-key` header.

### Voice demo client (stand-in for Alexa+)

A web page with a mic button: speech goes to a LangChain agent, which calls FitGen's MCP
tools and speaks the answer. Needs Chrome or Edge for voice input (typing works anywhere)
and Bedrock access (`BEDROCK_API_KEY` in `.env`, or AWS credentials). Run three processes:

```bash
uv run uvicorn server.app:app --port 8000                                  # 1. FitGen MCP server
cd client/backend && uv run uvicorn fitgen_client.app:app --port 8100      # 2. demo client backend
cd client/web && npm install && npm run dev                                # 3. web app
```

Open http://localhost:5173, tap the mic and say "what's my workout today?", "start my
workout", "done", "only got 6" or "how was my week?". The client backend has its own uv
project; run its tests with
`cd client/backend && uv run pytest` (Postgres needed; the live LLM test is opt-in with
`FITGEN_LIVE_LLM=1`).
# FitPilot
