---
name: add-mcp-tool
description: Step-by-step recipe for adding or changing a FitGen MCP tool (service, presenter, registration with the widget, tests). Use whenever a new tool is added (e.g. start_session, plate_calculator) or an existing tool's behavior or output changes.
---

# Adding a FitGen MCP tool

A tool is four thin layers. Look at `log_workout` as the reference implementation:
`server/services/workouts.py` → `server/presenters/workouts.py` →
`server/tools/logging_tools.py`, tested in `tests/test_presenters.py` and
`tests/test_tools_db.py`.

## 1. Service (`server/services/`)

- An async function taking `(db, ctx: UserContext, ..., now: datetime)` and returning a
  frozen dataclass result. No formatting of text here.
- Use `ctx.user_id` for every query (it comes from `get_current_user()`; tools never take
  a user id, session id or row id as input).
- Weights in kg internally: `to_kg(value, unit)` on input.
- Resolve exercise names with `resolve_exercise(await get_resolver(db), name)`; it raises
  `UserFacingError` with suggestions.
- Problems the user can fix: raise `UserFacingError("spoken message")`.
- No LLM/Bedrock calls. Keep it well under 500 ms: aggregate in SQL, avoid N+1
  queries, don't reload static data.
- Put pure rules (math, decisions) in their own functions so they can be tested without
  a database.

## 2. Presenter (`server/presenters/`)

- `present_<tool>(result) -> Presentation(text, components, display)`.
- `text` must fully answer when spoken with no screen: short, natural, numbers rounded,
  units in words. Use the helpers in `server/presenters/speech.py` and `format_weight`.
- `components` use only catalog types (`server/catalog/components.py`). Add a new
  component type to the catalog only if none fits, and add its React renderer in Phase 5+.
- `display`: `"fragment"` for quick confirmations, `"full"` for screens worth looking at.

## 3. Tool (`server/tools/`)

```python
@apps.tool(
    resource_uri=WIDGET_URI,
    name="tool_name",
    title="Tool name",
    description="One short sentence: what it does and when to use it.",
)
async def tool_name(
    exercise: Annotated[str, Field(description="Exercise name as spoken, e.g. 'bench'")],
) -> CallToolResult:
    async def handler(db, ctx):
        result = await service.do_thing(db, ctx, ..., now=datetime.now(UTC))
        return present_thing(result)

    return await run_tool(handler)
```

- `run_tool` opens the transaction, loads the user context, turns `UserFacingError`
  into an error result, and validates components (an invalid component raises: it's a
  server bug).
- New tool module? Import it in `server/mcp_server.py` **before** `MCPServer(...)` is
  constructed; extension tools are read at construction.
- Unit parameters accept spoken forms ("pounds", "kilos") via `parse_unit`.

## 4. Tests

- Pure logic: unit tests without a DB (like `tests/test_progression.py`).
- Presenter: build the result dataclass directly, assert the exact spoken text, and run
  the components through `validate_components` (see `check()` in
  `tests/test_presenters.py`).
- Listing: add the name to `UI_TOOLS` in `tests/test_tools_listing.py`.
- End to end: add a test to `tests/test_tools_db.py` that calls the tool over MCP, checks
  the spoken text and `structured_content`, and add the call to the latency test.

## 5. Review

Run the `voice-reviewer` agent on the new text and tool definition, then `test-runner`.
