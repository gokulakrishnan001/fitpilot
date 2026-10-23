# FitGen for Alexa+: Progress

Status of the build against the implementation plan. Last updated 2026-10-12.

**Summary:** Phases 0–4, 6, 7a and 8 are done on `feature/alexa-mcp-server` and verified end
to end: 274 server tests and 11 demo-client tests pass against a real Postgres (plus
opt-in live LLM tests), all 15 tools respond in 3–55 ms, AI coaching notes are generated in
the background (now on Amazon Bedrock), and a voice demo client drives FitGen at about 0.7 s per turn.
Signature feature: live voice coaching between sets ("that felt easy" changes the next set,
"you beat last time"). Phase 5 (widget) is deferred.

## Phase status

| Phase | Scope | Status |
|---|---|---|
| 0 | Repo setup | ✅ Done |
| 1 | MCP server skeleton | ✅ Done, verified end to end |
| 2 | Data layer | ✅ Done, verified against Postgres |
| 3 | Catalog, presenters, MVP tools | ✅ Done, verified end to end |
| 4 | Guided session | ✅ Done, verified end to end |
| 5 | Widget (MCP Apps) | ⏸ Deferred: unclear whether Alexa+ renders MCP Apps |
| 6 | Background AI jobs | ✅ Done, verified end to end (incl. live Groq) |
| 7a | Utility tools (plates, form cues, readiness, preferences, macros) | ✅ Done, verified end to end |
| 7b | Plan tools (create_plan, personalize_plan, adjust_plan) | ⏳ Not started |
| 8 | Demo client | ✅ Done, verified end to end (incl. live Groq); voice needs a browser check |
| 9 | Deployment and docs | ⏳ Not started |
| 10 | OAuth (only if Alexa requires it) | ⏸ Needs go-ahead |

## What each phase delivered

### Phase 0: Repo setup

- Repo layout per the plan, MIT `LICENSE`, empty `FRICTION_LOG.md` and `FEEDBACK.md`.
- Python tooling: uv, ruff, pytest (`pyproject.toml`).
- Node scaffolds (Vite + React + TypeScript) in `widget/` and `client/web/`.
- `infra/docker-compose.yml`: Postgres 16 on host port **5434** (5432/5433 are used by other
  projects on the dev machine).
- Import commit `aa19c70` "Import component catalog and renderer from FitGen
  (pre-hackathon)": the 14-component catalog and React renderer copied unchanged.
  Everything after it is hackathon work.

### Phase 1: MCP server skeleton

- `server/app.py`: FastAPI app with the MCP server mounted at `/mcp` (Streamable HTTP).
- `server/mcp_server.py`: the official MCP Python SDK server. `initialize` negotiates
  protocol **2025-11-25**. A middleware logs every tool call as
  `tool=<name> status=<ok|error> duration_ms=<n>`.
- `server/auth.py`: `get_current_user()` returns the demo user. `AUTH_MODE=api_key`
  checks `x-api-key` with a constant-time comparison and returns 401 otherwise.
  `AUTH_MODE=oauth` raises `NotImplementedError`.
- `server/config.py`: settings from env / `.env`.
- `ping` tool.

### Phase 2: Data layer

- `server/db/models.py`: users, user_profiles, exercises, exercise_aliases, plans,
  plan_days, plan_exercises, sessions, logged_sets, body_metrics, insights, jobs.
  - Weights stored in kg (`Numeric(8,3)`); every user-owned table has an indexed `user_id`.
  - The database enforces one active plan and one active session per user.
- `server/db/init_db.py`: creates tables from the models (no Alembic, see decisions).
- `server/db/repository.py`: queries; each user-owned query requires `user_id`.
- `server/services/units.py`: lb/kg conversion, unit parsing ("lbs", "kilos"),
  spoken formatting ("225 pounds").
- `server/services/exercise_resolver.py`: spoken name → exercise. Handles aliases
  ("bench", "rdl"), plurals ("squats"), missing spaces ("benchpress") and typos
  ("squatz"). Vague names ("press") return suggestions. Runs in memory.
- `server/db/seed.py` + `seed_data.py`: demo user (lb, strength goal, 4 days/week),
  28 exercises with aliases and 3 form cues each, a 4-day upper/lower plan, and 8 weeks
  of deterministic history (gradual progression, ~7% missed sets, 4 skipped sessions,
  twice-weekly weigh-ins).

### Phase 3: Catalog, presenters, MVP tools

- **Catalog** (`server/catalog/`): imports adapted; 16 components (the original 14 plus
  `session_card` and `plate_diagram`). Unknown keys are rejected. A validation failure
  is logged and raised as a server bug, never retried.
- **Every tool result has three parts:** spoken text, `structuredContent`
  (`{"components": [...], "display": "fragment" | "full"}`), and `_meta.ui.resourceUri`
  on the tool definition. The widget resource is a placeholder
  (`ui://fitgen/widget.html`, `text/html;profile=mcp-app`, empty CSP) until Phase 5.
- **Tools:**

  | Tool | Inputs | Display |
  |---|---|---|
  | `log_workout` | exercise, sets, reps, weight?, unit? | fragment |
  | `log_body_metric` | bodyweight, unit? | fragment |
  | `undo_last_log` | (none) | fragment |
  | `get_todays_workout` | (none) | full |
  | `get_weekly_summary` | weeks_ago? | full |
  | `get_exercise_progress` | exercise, weeks? | full |

- **Services** (`server/services/`): progressive overload, PR detection (heaviest
  weight, or most reps at a weight), dedupe of identical logs within 60 s, batch undo,
  weekly stats, deterministic fallback coaching line.
- **Presenters** (`server/presenters/`): short spoken text with rounded numbers and
  units in full; helpful voice errors ("Did you mean bench press or overhead press?").
- No LLM calls on the request path.

### Phase 4: Guided session

- **Tools:**

  | Tool | Inputs | Display |
  |---|---|---|
  | `start_session` | (none) | full |
  | `complete_set` | reps?, weight?, unit? | fragment |
  | `skip_exercise` | (none) | fragment |
  | `end_session` | (none) | full |

- **Server-owned position:** `sessions.current_position` / `current_set`; no IDs from the
  client. Targets are snapshotted into a new `session_exercises` table at start, so
  defaults stay fixed for the whole workout. A weight the user changes carries to the
  remaining sets of that exercise.
- **Flow:**
  - `start_session` starts today's plan day, or resumes today's active workout.
  - On a rest day it starts the next scheduled day and says so.
  - An active workout from an earlier day is closed first (deleted if it has no sets).
  - After the last set the workout stays open until `end_session` ("Say end workout to
    wrap up").
  - `end_session` with no sets cancels the workout.
- **Short replies between sets:** `complete_set` says "Set 2 of 4 done." and adds reps or
  weight only when they differ from the target, plus records and the next exercise.
  `session_card`, a rest `timer` (120 s barbell, 90 s other) and control buttons are
  returned for the widget.
- **Undo:** `undo_last_log` on a guided set moves the workout back to that set. Guided sets
  record `logged_sets.session_exercise_id`, so free-form `log_workout` sets never rewind
  the workout.
- **Retries:** a repeated `complete_set` within 8 s is treated as a retried request and not
  logged twice.
- **Background job hook:** `end_session` queues a `regenerate_weekly_insight` row in `jobs`
  (one queued per user) for the Phase 6 worker.
- `get_todays_workout` now includes a "Start workout" button.

### Phase 6: Background AI jobs

- **LLM clients** (`server/jobs/llm.py`): `LLMClient` interface; `BedrockClient` (Converse
  API, Nova Lite, run in a thread) with timeouts and retries. Groq was removed on
  2026-10-23; Bedrock is the only provider (optional `BEDROCK_API_KEY`).
- **Queue** (`server/jobs/queue.py`): `LocalQueue` (default) and `SqsQueue`, selected by
  `QUEUE_BACKEND`. The `jobs` table is the source of truth; messages only wake the worker.
  Jobs are dispatched after the tool's transaction commits (`server/jobs/dispatch.py`).
- **Worker** (`server/jobs/worker.py`): runs inside the server process (FastAPI lifespan).
  Sweeps due jobs, claims each atomically, retries with backoff (30 s, 60 s), fails after
  3 attempts. At startup it requeues jobs left running or queued. A queue configuration
  error disables the worker with a warning instead of crashing.
- **`regenerate_weekly_insight`** (`server/jobs/insights.py`):
  - Computes the week's metrics in Python and sends only numbers, exercise names, the goal
    and the unit (no name, id or other personal data).
  - Validates the reply: 1–2 sentences, ≤ 240 characters, no markdown, no pain, injury or
    diet mentions, must name an exercise or a number from the data. Normalizes for speech
    ("175 lb" → "175 pounds", "175 lb bench press" → "175-pound bench press", "%" →
    "percent", "PR" → "personal record"). One retry with the rejection reason.
  - Stores the note in `insights`; `get_weekly_summary` then speaks it as "Coach's note".
  - The LLM call runs outside any database transaction.
- **Triggers:** `end_session` and closing a stale workout (immediately); `log_workout`
  (debounced 10 minutes after the last log); `undo_last_log` (immediately, so a misheard
  record doesn't stay in the note). Each job carries the `week_start` it's about.
- **Concurrency:** one queued job per user and kind (partial unique index + atomic upsert);
  requests reschedule it. Enqueueing while a job runs creates a new one.
- **Weekly summary:** the current week now says "So far this week" with no volume
  comparison (a partial week vs a full one was misleading); the model is told when the
  week isn't over.
- `personalize_plan` is deferred to Phase 7 (with `create_plan`).

### Phase 7a: Utility tools

| Tool | Inputs | Example answer |
|---|---|---|
| `plate_calculator` | weight, unit? | "For 225 pounds, put two 45s on each side of the 45-pound bar." |
| `get_form_cues` | exercise | "Three cues for deadlift. First, bar over mid foot before you pull. Second, …" |
| `readiness_check` | sleep_hours?, soreness?, energy?, pain_mentioned?, notes? | "With very little sleep, let's keep today light. I've taken 20 percent off today's weights and dropped a set." |
| `set_preferences` | unit?, bar_weight?, goal?, days_per_week?, timezone? | "Done. I'll use kilograms, focus on building muscle and plan for 3 days a week." |
| `calculate_macros` | bodyweight?, unit? | "For strength at 80 kilograms, aim for about 2,750 calories a day. That's roughly 145 grams of protein, …" |

- **Plates:** greedy per side (US 45/35/25/10/5/2.5 lb, kg 25/20/15/10/5/2.5/1.25); bar from
  the profile, or its counterpart in the other unit (45 lb ↔ 20 kg, 35 lb ↔ 15 kg); handles
  below-the-bar, empty-bar and non-loadable requests ("closest is 185"). Returns
  `plate_diagram`.
- **Readiness:** deterministic points → full, reduce (−10%), light (−20%, one set fewer) or
  rest. Stored per day in a new `readiness_checks` table (level and pain flag only; notes
  are never stored) and applied to `get_todays_workout` and `start_session`; a check during
  an active workout lightens the remaining exercises. Lightened days are excluded from
  progression history, and barbell targets never drop below the empty bar.
- **Pain safety:** a broad pain regex or the `pain_mentioned` flag always leads with "If
  something hurts, stop that exercise and see a doctor or physical therapist before doing
  it again." Never "train as planned", never a diagnosis.
- **Preferences:** partial updates; creates the profile on first run (asks for units, goal
  and days with an example when nothing is given). Goals accept phrases ("build muscle",
  "lose weight"). Time zones accept cities, US zones and IANA names, stored in canonical
  form (`tzdata` bundled, so it works on Linux).
- **Macros:** bodyweight-based estimate (about 33 kcal/kg × goal factor; protein 1.6–2.2 g/kg;
  fat 0.9 g/kg; carbs the rest), using the latest weigh-in when no weight is given.

### Phase 8: Demo client (stand-in for Alexa+)

- **Backend** (`client/backend/`, its own uv project, port 8100):
  - `mcp_tools.py` wraps the FitGen MCP tools (official mcp 2.x client) as LangChain tools
    that return (spoken text, components). langchain-mcp-adapters can't be used with mcp 2.x
    (FRICTION_LOG #1).
  - Tools are `return_direct`: one LLM call per turn, and FitGen's voice-complete text is
    spoken verbatim. One tool per turn (`parallel_tool_calls: false`), so the agent can't
    chain an action the user didn't ask for.
  - `create_agent` (LangChain 1.x) with Bedrock (`ChatBedrockConverse`, Nova Lite; Groq
    until 2026-10-23); conversation memory in memory per conversation.
  - `POST /api/chat` streams SSE (`text`, `tool_start`, `tool_result` with components,
    `done`, `error`); `POST /api/action` runs a button's tool directly (allow-listed to the
    server's tools, no LLM).
  - Failed tool calls are always answered ("couldn't reach FitGen"), so memory stays valid;
    SDK argument errors become "Sorry, I didn't catch that."
- **Web app** (`client/web/`, Vite, port 5173): push-to-talk mic (Web Speech API, Chrome or
  Edge), spoken replies (speechSynthesis) with mute, transcript with tool names, and a
  display area (full results replace the screen, fragments stack on top). Uses the imported
  renderer with the dark theme, plus new `session_card` and `plate_diagram` views; buttons
  call `/api/action`.
- **Measured live:** 14-turn workout conversation (start, done, effort, undo, skip, end,
  bodyweight, a vague question, a greeting), correct tool every turn, 0.5–1.6 s per turn.
- **Fixes from the first browser test (Claude in Chrome, 24 steps, 13 passed):**
  - The agent sometimes imitated FitGen's replies ("Set 1 of 4 completed.") without calling
    a tool, so nothing was logged. Every turn must now call a tool (`tool_choice` forced by
    middleware); greetings and clarifying questions go through a small `reply` tool, which
    is told never to claim an action.
  - Slow turns (8–22 s) were Groq's free-tier limit: it counts each request's maximum answer
    length. Capping answers at 300 tokens (a tool call needs < 100) and sending only the last
    3 turns removed the stalls (8 quick turns: 31 s before, 7 s after).
  - The retry guard was 8 s, which refused a real "Log set" 5 s after the previous set;
    now 3 s. Undo inside a workout now returns the session card for the set it moved back
    to, and the web app shows only the newest quick result (no stale buttons).
  - "Did you mean" suggestions need a closer match (no more "zumba → dumbbell bench press");
    the weekly fallback note no longer repeats the session count; chart axes use round
    ticks.

### Signature feature: live effort coaching and "beat last time"

Added after Phase 8 to give FitGen one memorable, voice-only moment. Both live in
`complete_set` (pure rules in `server/services/effort.py`), with no LLM on the request path.

- **"That felt easy" (live autoregulation):** `complete_set` takes an optional `effort`
  (easy, ok, hard, failed; 2+ reps short counts as failed). It adjusts the remaining sets
  of the current exercise in the workout snapshot:

  | Effort | Next sets | Spoken |
  |---|---|---|
  | easy (reps hit) | +1 step, at most +2 per exercise per workout | "That felt easy, so your next set is 225 pounds." |
  | hard | hold | "Tough one, so let's hold 220 pounds." |
  | failed | −5% (at least one step, never below the bar or above the set just done) | "That one was tough, so let's drop your next set to 210 pounds." |

  Effort is stored (`logged_sets.effort`) and feeds next week's progression: a failed set is
  a miss, and a hard or failed top set holds the weight. Sessions are judged on all working
  sets (within 85% of the top weight), so mid-workout bumps never read as misses. Undo
  restores the target and the easy-step budget; an effort said with a retried request is
  kept without logging another set.
- **"Beat last time":** each set is compared with the same set in the exercise's previous
  session (readiness-lightened days skipped). Spoken only when beaten ("That beats last time
  by 2 reps." / "That's 5 pounds more than last time."), never alongside a record or on a
  set that fell short. The session card shows "Last time: 215 lb × 5. Beat it."
- **Demo client:** the agent passes `effort` from natural phrases; verified live with Groq.

## Decisions and deviations from the plan

| Decision | Reason |
|---|---|
| `MCPServer` instead of `FastMCP` | mcp 2.x renamed FastMCP to MCPServer; same official SDK |
| No Alembic | No real data to preserve; `seed --reset` rebuilds the schema. Add Alembic before real users |
| Overload step +2.5 lb / +1.25 kg for dumbbells | A flat +5 lb is a ~20% jump on a 25 lb dumbbell; barbell, machine and cable lifts keep +5 lb / +2.5 kg |
| Undo removes the whole last log | "Undo" after "3 sets of bench" should remove all 3 sets (`logged_sets.batch_id`) |
| `exercises.equipment` field | Bodyweight moves log no weight; plate calculator needs to know barbell lifts |
| `user_profiles.timezone` (demo: `Asia/Kolkata`) | "Today" and "this week" depend on the user's local date |
| `plan_days.weekday` | Lets "today's workout" come from the schedule in the database |
| Spoken weights round to 0.5 lb / 0.25 kg | So a 27.5 lb dumbbell isn't read out as "28 pounds" |
| Postgres on host port 5434 | Another project's container (`todo_postgres`) holds 5433 |
| `session_exercises` snapshot table | Default reps and weights stay fixed during a workout even if other sets are logged |
| Rest day starts the next scheduled day | Training on an off day is common; Alexa says which day it started |
| Workout stays open after the last set | Lets the user add a set before saying "end workout" |
| `complete_set` speaks details only when they differ | It's heard after every set; "Set 2 of 4 done." is enough |
| 8 s retry guard on `complete_set` | Voice requests get retried; a real set never follows the last one that fast |
| Phase 5 (widget) deferred | Unknown whether Alexa+ supports MCP Apps widgets. Voice answers are complete without it; tools keep the placeholder `ui://` resource; the Phase 8 web client can render components itself |
| Bedrock (Nova Lite) as the only LLM | Simpler setup and AWS deployment; replaced Groq `openai/gpt-oss-120b` on 2026-10-23 |
| Worker inside the server process | Matches the plan; jobs table makes it safe to add workers later |
| Insight doesn't prescribe weights or reps | The progression rule and `get_todays_workout` own those; the note covers priorities, consistency and stalls |
| `personalize_plan` deferred to Phase 7 | Its trigger (`create_plan`) is there, and changing a plan with an LLM needs strict validation |
| Readiness changes today's targets | "Go lighter" should change the numbers; stored per day, excluded from progression |
| Macros from bodyweight and goal only | Avoids collecting height, age and sex; clearly labelled as an estimate |
| Plan split into 7a and 7b | The plan-changing tools (with an LLM) need their own design and review |
| Own MCP-to-LangChain wrapper | langchain-mcp-adapters fails to import with mcp 2.x (FRICTION_LOG #1); the plan's fallback |
| Client backend is its own uv project | langchain-groq requires Groq SDK < 1, the server uses 1.7 (FRICTION_LOG #2) |
| Demo tools are `return_direct` | Halves latency and Groq tokens, speaks FitGen's text verbatim, and stopped the agent chaining an unrequested `complete_set` |
| Browser speech APIs | Free, no setup; Amazon Transcribe/Polly would be AWS during development |
| Effort adjusts only the current exercise | Keeps changes predictable; capped at +2 steps; next week's suggestion uses the stored effort |
| "Beat last time" spoken only when beaten | `complete_set` is heard after every set, so silence when there's nothing to celebrate |
| No "record" announced on a set that fell short | "Record!" followed by "let's drop the weight" sounds contradictory; records still count |
| Demo user has fixed id 1 | Keeps `get_current_user()` free of DB lookups until OAuth |
| No tool `outputSchema` | The full catalog schema is large; tools validate against it before sending instead |

## Tests

274 tests, **all passing** against Postgres 16 (2026-10-14), plus one opt-in live test
against the LLM (`FITGEN_LIVE_LLM=1 uv run pytest tests/test_llm_live.py -s`), which passed
on Groq; not yet rerun on Bedrock. Without a reachable database the DB tests skip, which is not full verification.

Measured tool latency (server timing log, local Postgres): `get_todays_workout` 32 ms,
`log_workout` 25 ms, `get_weekly_summary` 14 ms, `log_body_metric` 8 ms,
`undo_last_log` 8 ms, `get_exercise_progress` 6 ms; `start_session` 36 ms, `end_session`
24 ms, `complete_set` 14 ms, `skip_exercise` 6 ms. Budget: 500 ms.

| Area | File | Needs DB |
|---|---|---|
| Server, auth, handshake | `tests/test_server_skeleton.py` | No |
| Units | `tests/test_units.py` | No |
| Exercise resolver, voice errors | `tests/test_exercise_resolver.py` | No |
| Seed history simulation | `tests/test_seed_history.py` | No |
| Catalog validation | `tests/test_catalog.py` | No |
| Progression and PRs | `tests/test_progression.py` | No |
| Presenters (spoken text + components) | `tests/test_presenters.py` | No |
| Tool listing, schemas, widget resource | `tests/test_tools_listing.py` | No |
| Models, seed, repository, user scoping | `tests/test_db.py` | **Yes** |
| All six tools end to end + < 500 ms latency | `tests/test_tools_db.py` | **Yes** |
| Session position state machine | `tests/test_session_state.py` | No |
| Session presenters (spoken text + components) | `tests/test_session_presenters.py` | No |
| Guided session end to end: walk-through, skip, resume, undo, retries, stale, latency | `tests/test_sessions_db.py` | **Yes** |
| Insight prompt, validation, normalization, privacy | `tests/test_insight_validation.py` | No |
| Bedrock client, local/SQS queues (mocked transports) | `tests/test_llm_and_queue.py` | No |
| Worker: success, retries, debounce, concurrency, week targeting, recovery | `tests/test_worker_db.py` | **Yes** |
| end_session → worker → spoken coaching note | `tests/test_jobs_flow_db.py` | **Yes** |
| Real Bedrock call (opt-in) | `tests/test_llm_live.py` | No (network) |
| Plates, readiness rules, pain detection, macros, preferences, spoken text | `tests/test_utilities.py` | No |
| Utility tools end to end, readiness vs progression and active workouts, latency | `tests/test_utilities_db.py` | **Yes** |
| Effort rules, beat-last-time, effort-aware progression | `tests/test_effort.py` | No |
| Demo client: tool wrapping, SSE events, memory, actions, failures (`client/backend`) | `client/backend/tests/test_client.py` | **Yes** |
| Demo client with real Bedrock: workout conversation (opt-in) | `client/backend/tests/test_live.py` | **Yes** + network |

## How to run

```bash
cp .env.example .env
docker compose -f infra/docker-compose.yml up -d    # Postgres 16 on localhost:5434
uv sync
uv run python -m server.db.seed --reset              # tables + demo data
uv run pytest
uv run uvicorn server.app:app --port 8000            # MCP at http://localhost:8000/mcp
npx @modelcontextprotocol/inspector                  # Streamable HTTP → that URL
```

After any model change, rerun `uv run python -m server.db.seed --reset` (wipes all data).

## Open items

1. **MCP Apps capability on 2025-11-25.** The SDK advertises extensions under
   `capabilities.extensions` only for protocol 2026-07-28, so a 2025-11-25 connection
   doesn't announce MCP Apps there. Tools still carry `_meta.ui.resourceUri`. Check the
   ext-apps spec in Phase 5.
2. **Deployment note for Phase 9.** The SDK's DNS-rebinding protection defaults to
   localhost hosts; allowed hosts must be configured behind the ALB.
3. **Bedrock not yet verified live.** Groq was removed on 2026-10-23. The insight job and
   the demo agent were tuned and measured on Groq; rerun both live tests on Bedrock
   (`FITGEN_LIVE_LLM=1`) and check the agent still makes one tool call per turn.
4. **Voice in a real browser.** Mic and speech output can't be tested here; check in Chrome
   or Edge before demoing.
5. **Rotate exposed API keys.** The Groq key (and an OpenRouter key, unused) were pasted in
   chat. Rotate them after the hackathon; keep keys only in `.env`.

## Next up

- **Try the voice demo** in Chrome or Edge (see README: three processes).
- **7b:** `create_plan` (instant template, background `personalize_plan`) and `adjust_plan`.
- **Phase 9:** AWS deployment (ECS Fargate, RDS, SQS, Bedrock) and final docs.

Phase 5 (widget) is deferred. Revisit it if Alexa+ turns out to render MCP Apps; open item
1 belongs to it.

## Claude Code agents and skills

Project-level helpers in `.claude/`. No agent changes git state; only `implementer`
edits files.

| Type | Name | Use it for |
|---|---|---|
| Agent | `implementer` | Implement an approved plan step, tool or fix, with tests, in the repo's conventions |
| Agent | `sdk-researcher` | How an SDK really works (MCP, ext-apps, Bedrock…), from installed source or official docs |
| Agent | `test-runner` | Run ruff + pytest, report failures with likely causes and any skipped DB tests |
| Agent | `voice-reviewer` | Check spoken text and tool descriptions for voice quality |
| Agent | `rules-reviewer` | Check changes against the non-negotiable rules |
| Skill | `phase-workflow` | Run one plan phase: propose, wait, implement, verify, summarize, no commits |
| Skill | `add-mcp-tool` | Recipe for a new tool: service → presenter → `apps.tool` → tests |
| Skill | `verify-db` | Start Postgres, seed, run the DB tests, troubleshoot Docker and ports |

## Working agreements

- One phase at a time: propose a plan, wait for OK, implement, test, summarize.
- Claude does not commit or push; changes are left for the user to commit.
- Read SDK source or official docs before using an API; ask when unclear.
- Friction log entries only for genuine, verified tool or SDK problems.
