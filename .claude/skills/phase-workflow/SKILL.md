---
name: phase-workflow
description: How to run one phase of the FitGen for Alexa+ implementation plan - propose, wait for OK, implement, verify, summarize, and never commit. Use when starting, continuing or finishing any plan phase (e.g. "let's do the next phase", "start phase 4").
---

# Running a FitGen phase

The plan has phases 0–10 (see `PROGRESS.md` for status). Work on exactly one at a time.

## 1. Orient

- Read `PROGRESS.md` for what's done, open items and decisions already made. Don't
  re-litigate settled decisions (no Alembic, MCPServer not FastMCP, dumbbell steps, etc.).
- Check `git status` and the current branch so you know what's uncommitted.
- If the phase touches an SDK or spec (MCP, MCP Apps, langchain-mcp-adapters, boto3
  Bedrock), use the `sdk-researcher` agent or read the installed source first.
  Never guess an API; if behavior is unclear, stop and ask the user.

## 2. Propose and wait

Give the user a short plan: what you found in the SDK/source, numbered steps with file
paths, any deviations from the written plan and why, and decisions that are genuinely
theirs (with a recommended option). Then stop and wait for an explicit OK.

If the user says go ahead without answering a decision, use your recommended option and
say so in the summary.

## 3. Implement

- Implement directly, or hand independent, well-scoped parts of the approved plan to the
  `implementer` agent (give it the exact approved scope; review its report and diff).
- Follow the repo's shape: thin tool handlers in `server/tools/`, domain logic in
  `server/services/`, spoken text and components in `server/presenters/`, queries in
  `server/db/repository.py`. For a new tool, follow the `add-mcp-tool` skill.
- Keep the non-negotiable rules: voice-complete text, < 500 ms per tool call (no LLM on
  the request path), three-part tool results, `user_id` only from `get_current_user()`,
  weights stored in kg, AWS behind `LLM_PROVIDER` / `QUEUE_BACKEND` interfaces.
- Schema change? There's no Alembic: update `server/db/seed.py` if needed and tell the
  user to run `uv run python -m server.db.seed --reset`.

## 4. Verify

- Run the `test-runner` agent (or `uv run ruff check .`, `uv run ruff format --check .`,
  `uv run pytest -q`). If DB tests skip, follow the `verify-db` skill or report clearly
  that the phase is not verified end to end.
- Run the `rules-reviewer` agent on the changes, and `voice-reviewer` if spoken text or
  tool definitions changed. Fix verified issues.

## 5. Summarize and stop

- Tell the user what changed (files and behavior), what was verified and what wasn't, any
  decisions you made, and open questions.
- Update `PROGRESS.md`: phase status table, the phase's "what it delivered" section,
  decisions, test counts, open items, next up.
- Suggest a commit message. **Do not stage, commit or push**; the user commits.
- If you hit a genuine, verified problem with a tool, SDK or doc, add an entry to
  `FRICTION_LOG.md` with: task, steps, expected, actual, severity, workaround, suggestion.
  Never invent entries.

Then stop. Don't start the next phase until the user asks.
