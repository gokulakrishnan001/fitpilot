"""Background worker against the seeded test database, with a fake LLM.

The clock is pinned to last Sunday evening, so "this week" is a seeded week with training.
Skipped without Postgres.
"""

import asyncio
from collections.abc import AsyncIterator
from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from server.auth import DEMO_USER
from server.db import repository as repo
from server.db.models import Insight, Job
from server.db.seed import DEMO_TIMEZONE
from server.db.seed import run as run_seed
from server.db.session import make_engine
from server.jobs.insights import InsightRejected
from server.jobs.llm import LLMError
from server.jobs.queue import LocalQueue
from server.jobs.worker import Worker
from server.services import workouts
from server.services.context import load_user_context
from server.services.resolver_cache import get_resolver
from server.services.workouts import INSIGHT_JOB, get_weekly_summary
from tests.db_support import TEST_DATABASE_URL, FakeLLM, db_reachable

pytestmark = pytest.mark.asyncio(loop_scope="module")

TZ = ZoneInfo(DEMO_TIMEZONE)
TODAY = datetime.now(TZ).date()
LAST_SUNDAY = TODAY - timedelta(days=TODAY.weekday() + 1)
NOW = datetime.combine(LAST_SUNDAY, time(20, 0), tzinfo=TZ)
GOOD = (
    "Your bench press moved up again and you trained consistently this week. "
    "Next week, add a paused set on barbell row to push past 145 pounds."
)


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def sessionmaker() -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    if not await db_reachable():
        pytest.skip("Postgres not reachable at TEST_DATABASE_URL")
    await run_seed(TEST_DATABASE_URL, reset=True, today=TODAY)
    engine = make_engine(TEST_DATABASE_URL)
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


@pytest_asyncio.fixture(autouse=True, loop_scope="module")
async def clean_jobs(sessionmaker):
    async with sessionmaker() as db:
        for model in (Job, Insight):
            for row in await db.scalars(select(model)):
                await db.delete(row)
        await db.commit()


def worker(sessionmaker, llm, clock=lambda: NOW) -> Worker:
    return Worker(sessionmaker=sessionmaker, queue=LocalQueue(), llm=llm, clock=clock)


async def enqueue(sessionmaker, run_after=NOW) -> int:
    async with sessionmaker() as db:
        job_id = await repo.enqueue_job(db, DEMO_USER.user_id, INSIGHT_JOB, run_after=run_after)
        await db.commit()
    return job_id


async def get_job(sessionmaker, job_id) -> Job:
    async with sessionmaker() as db:
        return await db.get(Job, job_id)


async def test_insight_job_stores_insight_and_summary_speaks_it(sessionmaker):
    llm = FakeLLM(GOOD)
    job_id = await enqueue(sessionmaker)
    assert await worker(sessionmaker, llm).process_due() == 1

    job = await get_job(sessionmaker, job_id)
    assert (job.status, job.attempts, job.error) == ("done", 1, None)

    async with sessionmaker() as db:
        ctx = await load_user_context(db, DEMO_USER.user_id)
        summary = await get_weekly_summary(db, ctx, weeks_ago=0, now=NOW)
        insight = await repo.get_insight(db, DEMO_USER.user_id, summary.week_start)
    assert summary.insight == GOOD and not summary.insight_is_fallback
    assert insight.model_id == "fake-model"

    # Privacy: only metrics reach the model.
    prompt = llm.prompts[0]
    assert "Demo" not in prompt and "demo" not in prompt
    assert "planned sessions" in prompt and "Bench Press" in prompt


async def test_invalid_reply_is_retried_with_feedback(sessionmaker):
    llm = FakeLLM("**Great week!**", GOOD)
    job_id = await enqueue(sessionmaker)
    await worker(sessionmaker, llm).process_due()
    assert (await get_job(sessionmaker, job_id)).status == "done"
    assert len(llm.prompts) == 2
    assert "rejected because it used markdown" in llm.prompts[1]


async def test_failures_back_off_then_fail(sessionmaker):
    llm = FakeLLM(LLMError("Bedrock call failed: timeout"))
    job_id = await enqueue(sessionmaker)
    clock = [NOW]
    w = worker(sessionmaker, llm, clock=lambda: clock[0])

    await w.process_due()
    job = await get_job(sessionmaker, job_id)
    assert (job.status, job.attempts) == ("queued", 1)
    assert job.run_after == NOW + timedelta(seconds=30)
    assert "timeout" in job.error

    assert await w.process_due() == 0  # not due yet
    for _ in range(2):
        clock[0] += timedelta(hours=1)
        await w.process_due()
    job = await get_job(sessionmaker, job_id)
    assert (job.status, job.attempts) == ("failed", 3)


async def test_twice_invalid_reply_fails_the_attempt(sessionmaker):
    llm = FakeLLM("No specifics here at all. Just vibes for the week ahead.")
    job_id = await enqueue(sessionmaker)
    await worker(sessionmaker, llm).process_due()
    job = await get_job(sessionmaker, job_id)
    assert job.status == "queued" and InsightRejected.__name__ in job.error


async def test_enqueue_debounces(sessionmaker):
    first = await enqueue(sessionmaker, run_after=NOW + timedelta(minutes=10))
    second = await enqueue(sessionmaker, run_after=NOW + timedelta(minutes=20))
    assert first == second
    job = await get_job(sessionmaker, first)
    assert job.run_after == NOW + timedelta(minutes=20)
    assert await worker(sessionmaker, FakeLLM(GOOD)).process_due() == 0  # not due yet


async def test_week_without_training_is_skipped(sessionmaker):
    # A future week: never has seeded training, whatever today's date is.
    future = TODAY + timedelta(weeks=2)
    monday = datetime.combine(future - timedelta(days=future.weekday()), time(9), tzinfo=TZ)
    llm = FakeLLM(GOOD)
    job_id = await enqueue(sessionmaker, run_after=monday)
    await worker(sessionmaker, llm, clock=lambda: monday).process_due()
    assert (await get_job(sessionmaker, job_id)).status == "done"
    assert llm.prompts == []  # nothing to say, no model call


async def test_recover_requeues_stuck_jobs_and_wakes_worker(sessionmaker):
    job_id = await enqueue(sessionmaker)
    async with sessionmaker() as db:
        job = await db.get(Job, job_id)
        job.status = "running"
        await db.commit()
    w = worker(sessionmaker, FakeLLM(GOOD), clock=lambda: NOW + timedelta(days=30))
    assert await w.recover() == 1
    assert (await get_job(sessionmaker, job_id)).status == "queued"
    assert await w.queue.get(timeout=0.1) == job_id


async def test_run_loop_processes_queued_job(sessionmaker):
    job_id = await enqueue(sessionmaker)
    w = worker(sessionmaker, FakeLLM(GOOD))
    w.poll_seconds = 0.05
    task = asyncio.create_task(w.run())
    try:
        for _ in range(100):
            if (await get_job(sessionmaker, job_id)).status == "done":
                break
            await asyncio.sleep(0.05)
    finally:
        task.cancel()
    assert (await get_job(sessionmaker, job_id)).status == "done"


async def _jobs(sessionmaker) -> list[Job]:
    async with sessionmaker() as db:
        return list(await db.scalars(select(Job).order_by(Job.id)))


async def test_log_workout_queues_debounced_job_for_its_week(sessionmaker):
    async with sessionmaker() as db:
        ctx = await load_user_context(db, DEMO_USER.user_id)
        resolver = await get_resolver(db)
        await workouts.log_workout(
            db, ctx, resolver, exercise="curl", sets=1, reps=10, weight=20, unit="lb", now=NOW
        )
        await workouts.log_workout(
            db,
            ctx,
            resolver,
            exercise="curl",
            sets=1,
            reps=9,
            weight=20,
            unit="lb",
            now=NOW + timedelta(minutes=3),
        )
        await db.commit()
    [job] = await _jobs(sessionmaker)  # debounced into one job
    assert job.run_after == NOW + timedelta(minutes=13)
    assert job.payload == {"week_start": workouts.week_start_of(NOW, ctx).isoformat()}

    async with sessionmaker() as db:
        await workouts.undo_last_log(db, ctx, now=NOW + timedelta(minutes=4))
        await workouts.undo_last_log(db, ctx, now=NOW + timedelta(minutes=4))
        await db.commit()
    [job] = await _jobs(sessionmaker)
    assert job.run_after == NOW + timedelta(minutes=4)  # undo refreshes right away


async def test_only_one_concurrent_claim_wins(sessionmaker):
    job_id = await enqueue(sessionmaker)

    async def claim():
        async with sessionmaker() as db:
            job = await repo.claim_job(db, job_id, NOW)
            await db.commit()
            return job

    results = await asyncio.gather(claim(), claim(), claim())
    assert sum(r is not None for r in results) == 1


async def test_enqueue_while_running_creates_a_new_job(sessionmaker):
    first = await enqueue(sessionmaker)
    async with sessionmaker() as db:
        await repo.claim_job(db, first, NOW)
        await db.commit()
    second = await enqueue(sessionmaker)
    assert second != first
    statuses = [(j.id, j.status) for j in await _jobs(sessionmaker)]
    assert statuses == [(first, "running"), (second, "queued")]


async def test_failed_attempt_superseded_by_newer_job(sessionmaker):
    first = await enqueue(sessionmaker)
    llm = FakeLLM(LLMError("timeout"))
    w = worker(sessionmaker, llm)
    async with sessionmaker() as db:
        await repo.claim_job(db, first, NOW)
        await db.commit()
    newer = await enqueue(sessionmaker, run_after=NOW + timedelta(hours=1))
    job = await get_job(sessionmaker, first)
    await w._fail(job, LLMError("timeout"))
    first_job = await get_job(sessionmaker, first)
    assert first_job.status == "failed" and f"superseded by job {newer}" in first_job.error
    assert (await get_job(sessionmaker, newer)).status == "queued"


async def test_job_runs_for_the_week_it_was_queued_for(sessionmaker):
    """Logged Sunday night, processed Monday morning: still that week's insight."""
    monday = NOW + timedelta(hours=10)
    async with sessionmaker() as db:
        await repo.enqueue_job(
            db,
            DEMO_USER.user_id,
            INSIGHT_JOB,
            {"week_start": (LAST_SUNDAY - timedelta(days=6)).isoformat()},
            run_after=NOW,
        )
        await db.commit()
    llm = FakeLLM(GOOD)
    await worker(sessionmaker, llm, clock=lambda: monday).process_due()
    async with sessionmaker() as db:
        stored = await repo.get_insight(db, DEMO_USER.user_id, LAST_SUNDAY - timedelta(days=6))
    assert stored is not None and stored.text == GOOD
    assert "This week:" in llm.prompts[0]  # the week is complete, so it's compared
