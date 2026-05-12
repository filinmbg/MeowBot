from __future__ import annotations

import asyncio
import signal
import sys
from contextlib import suppress
from dataclasses import dataclass


@dataclass(frozen=True)
class ManagedProcess:
    name: str
    module: str


PROCESSES: tuple[ManagedProcess, ...] = (
    ManagedProcess(name="telegram", module="meowbot.apps.telegram_bot.main"),
    ManagedProcess(name="event-worker", module="meowbot.apps.telegram_event_worker.main"),
    ManagedProcess(name="online-bot", module="meowbot.apps.bot_online.main"),
)

TERMINATE_TIMEOUT_SECONDS = 10.0


async def _stream_output(stream: asyncio.StreamReader | None, prefix: str) -> None:
    if stream is None:
        return

    while True:
        line = await stream.readline()
        if not line:
            return
        text = line.decode("utf-8", errors="replace").rstrip("\r\n")
        print(f"[{prefix}] {text}", flush=True)


async def _start_process(spec: ManagedProcess) -> asyncio.subprocess.Process:
    process = await asyncio.create_subprocess_exec(
        sys.executable,
        "-m",
        spec.module,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    print(f"[runner] started [{spec.name}] pid={process.pid} module={spec.module}", flush=True)
    return process


async def _terminate_process(name: str, process: asyncio.subprocess.Process) -> None:
    if process.returncode is not None:
        return

    print(f"[runner] terminating [{name}] pid={process.pid}", flush=True)
    with suppress(ProcessLookupError):
        process.terminate()

    try:
        await asyncio.wait_for(process.wait(), timeout=TERMINATE_TIMEOUT_SECONDS)
    except asyncio.TimeoutError:
        print(f"[runner] killing [{name}] pid={process.pid} after timeout", flush=True)
        with suppress(ProcessLookupError):
            process.kill()
        await process.wait()


async def _shutdown_all(processes: dict[str, asyncio.subprocess.Process]) -> None:
    await asyncio.gather(
        *(_terminate_process(name, process) for name, process in processes.items()),
        return_exceptions=True,
    )


async def _run() -> int:
    processes: dict[str, asyncio.subprocess.Process] = {}
    stream_tasks: list[asyncio.Task[None]] = []
    wait_tasks: dict[asyncio.Task[int], str] = {}
    stop_event = asyncio.Event()

    loop = asyncio.get_running_loop()

    def _request_shutdown() -> None:
        if stop_event.is_set():
            return
        print("[runner] shutdown requested", flush=True)
        stop_event.set()

    for signum in (signal.SIGINT, signal.SIGTERM):
        with suppress(NotImplementedError):
            loop.add_signal_handler(signum, _request_shutdown)

    try:
        for spec in PROCESSES:
            process = await _start_process(spec)
            processes[spec.name] = process
            stream_tasks.append(asyncio.create_task(_stream_output(process.stdout, spec.name)))
            stream_tasks.append(asyncio.create_task(_stream_output(process.stderr, spec.name)))
            wait_task = asyncio.create_task(process.wait())
            wait_tasks[wait_task] = spec.name

        stop_task = asyncio.create_task(stop_event.wait())

        done, pending = await asyncio.wait(
            [*wait_tasks.keys(), stop_task],
            return_when=asyncio.FIRST_COMPLETED,
        )

        exit_code = 0
        if stop_task in done and stop_event.is_set():
            print("[runner] stopping all child processes", flush=True)
        else:
            finished_task = next(task for task in done if task in wait_tasks)
            finished_name = wait_tasks[finished_task]
            finished_code = finished_task.result()
            print(
                f"[runner] child [{finished_name}] exited unexpectedly with code={finished_code}",
                flush=True,
            )
            exit_code = finished_code if finished_code != 0 else 1

        stop_event.set()
        stop_task.cancel()
        with suppress(asyncio.CancelledError):
            await stop_task

        for task in pending:
            task.cancel()

        await _shutdown_all(processes)

        for task in wait_tasks:
            with suppress(asyncio.CancelledError):
                if task.cancelled():
                    continue
                await task

        await asyncio.gather(*stream_tasks, return_exceptions=True)
        return exit_code
    finally:
        await _shutdown_all(processes)
        for task in stream_tasks:
            if not task.done():
                task.cancel()
        await asyncio.gather(*stream_tasks, return_exceptions=True)


def main() -> None:
    try:
        raise SystemExit(asyncio.run(_run()))
    except KeyboardInterrupt:
        raise SystemExit(130)


if __name__ == "__main__":
    main()
