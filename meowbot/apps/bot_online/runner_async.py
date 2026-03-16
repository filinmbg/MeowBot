from __future__ import annotations

import asyncio
import time
import logging

log = logging.getLogger("meowbot")


async def run_fixed_rate(name: str, period_s: float, job_coro):
    while True:
        t0 = time.monotonic()
        log.info("[%s] tick start", name)

        try:
            await job_coro()
        except Exception:
            log.exception("[%s] failed", name)

        elapsed = time.monotonic() - t0
        sleep_s = period_s - elapsed
        log.info("[%s] tick done (%.2fs), next in %.2fs", name, elapsed, max(0.0, sleep_s))

        if sleep_s > 0:
            await asyncio.sleep(sleep_s)


async def main_async(entry_job, exit_job):
    entry_task = asyncio.create_task(run_fixed_rate("entry", 60.0, entry_job))
    exit_task = asyncio.create_task(run_fixed_rate("exit", 60.0, exit_job))
    await asyncio.gather(entry_task, exit_task)