from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from collections import deque
from dataclasses import asdict, dataclass, field, is_dataclass
from enum import Enum
from pathlib import Path
from typing import Any

from pymongo.errors import AutoReconnect, NetworkTimeout, OperationFailure, PyMongoError, ServerSelectionTimeoutError

try:
    from pymongo.errors import WaitQueueTimeoutError
except ImportError:  # pragma: no cover
    WaitQueueTimeoutError = PyMongoError  # type: ignore[misc,assignment]


log = logging.getLogger("meowbot")

TRANSIENT_MONGO_ERRORS = (
    NetworkTimeout,
    AutoReconnect,
    ServerSelectionTimeoutError,
    WaitQueueTimeoutError,
)

PRIORITY_CRITICAL = 0
PRIORITY_NORMAL = 1
PRIORITY_LOW = 2

RETRY_DELAYS_SECONDS = (1.0, 3.0, 10.0, 30.0)
DEFAULT_FAILED_WRITES_PATH = Path("data/emergency_mongo_failed_writes.jsonl")
QUOTA_ERROR_MARKERS = (
    "over your space quota",
    "space quota",
    "quota exceeded",
    "storage quota",
)


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "y", "on"}


def _json_safe(value: Any, *, max_string_length: int = 2000, max_list_items: int = 200) -> Any:
    if is_dataclass(value):
        return _json_safe(asdict(value), max_string_length=max_string_length, max_list_items=max_list_items)
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, dict):
        return {
            str(key): _json_safe(item, max_string_length=max_string_length, max_list_items=max_list_items)
            for key, item in value.items()
            if key != "_id"
        }
    if isinstance(value, (list, tuple, set)):
        items = list(value)
        compacted = [
            _json_safe(item, max_string_length=max_string_length, max_list_items=max_list_items)
            for item in items[:max_list_items]
        ]
        if len(items) > max_list_items:
            compacted.append({"truncated_items": len(items) - max_list_items})
        return compacted
    if isinstance(value, bytes):
        return f"<bytes:{len(value)}>"
    if isinstance(value, str) and len(value) > max_string_length:
        return f"{value[:max_string_length]}...<truncated:{len(value) - max_string_length}>"
    return value


def _is_mongo_quota_error(exc: Exception) -> bool:
    text = str(exc).lower()
    if any(marker in text for marker in QUOTA_ERROR_MARKERS):
        return True
    if isinstance(exc, OperationFailure):
        details = getattr(exc, "details", None)
        if isinstance(details, dict):
            details_text = json.dumps(_json_safe(details), ensure_ascii=False).lower()
            return any(marker in details_text for marker in QUOTA_ERROR_MARKERS)
    return False


@dataclass(slots=True)
class MongoWriteJob:
    operation_type: str
    priority: int
    payload: Any
    created_at: float = field(default_factory=time.time)
    attempts: int = 0
    max_attempts: int = 1
    dedupe_key: str | None = None
    next_attempt_at: float = 0.0


@dataclass(slots=True)
class MongoWriterStats:
    writes_success: int = 0
    writes_failed: int = 0
    retries: int = 0
    dropped_low_priority: int = 0
    last_success_at: float = 0.0
    last_failure_at: float = 0.0
    consecutive_failures: int = 0


class MongoWriteQueue:
    def __init__(
        self,
        *,
        bars_repo,
        bot_state_repo,
        trades_repo,
        trade_events_repo,
        admin_alert_service=None,
        max_queue_size: int = 5000,
        high_watermark: int = 4000,
        flush_interval_seconds: float = 2.0,
        health_interval_seconds: float = 30.0,
        worker_count: int = 3,
        critical_batch_size: int = 100,
        normal_batch_size: int = 250,
        low_batch_size: int = 500,
        mongo_unavailable_alert_seconds: float = 60.0,
        critical_oldest_age_alert_seconds: float = 60.0,
        critical_queue_alert_threshold: int = 100,
        persist_bars: bool | None = None,
        emergency_failed_writes_path: str | Path | None = None,
    ) -> None:
        self.bars_repo = bars_repo
        self.bot_state_repo = bot_state_repo
        self.trades_repo = trades_repo
        self.trade_events_repo = trade_events_repo
        self.admin_alert_service = admin_alert_service
        self.max_queue_size = int(max_queue_size)
        self.high_watermark = int(high_watermark)
        self.flush_interval_seconds = float(flush_interval_seconds)
        self.health_interval_seconds = float(health_interval_seconds)
        self.worker_count = max(1, int(worker_count))
        self.critical_batch_size = int(critical_batch_size)
        self.normal_batch_size = int(normal_batch_size)
        self.low_batch_size = int(low_batch_size)
        self.mongo_unavailable_alert_seconds = float(mongo_unavailable_alert_seconds)
        self.critical_oldest_age_alert_seconds = float(critical_oldest_age_alert_seconds)
        self.critical_queue_alert_threshold = int(critical_queue_alert_threshold)
        self.persist_bars = _env_bool("MONGO_PERSIST_BARS", False) if persist_bars is None else bool(persist_bars)
        self.emergency_failed_writes_path = Path(
            emergency_failed_writes_path
            or os.getenv("MONGO_FAILED_WRITES_PATH")
            or DEFAULT_FAILED_WRITES_PATH
        )

        self._critical_queue: deque[MongoWriteJob] = deque()
        self._normal_queue: deque[MongoWriteJob] = deque()
        self._low_queue: deque[MongoWriteJob] = deque()
        self._pending_trade_upserts: dict[str, MongoWriteJob] = {}
        self._pending_bot_state: dict[str, MongoWriteJob] = {}
        self._pending_bars: dict[str, MongoWriteJob] = {}
        self._stats = MongoWriterStats()
        self._has_jobs = asyncio.Event()
        self._accepting = True
        self._worker_tasks: list[asyncio.Task] = []
        self._health_task: asyncio.Task | None = None
        self._last_low_flush_at = time.monotonic()
        self._high_watermark_since: float | None = None
        self._critical_queue_over_threshold_intervals = 0
        self._idle_health_intervals = 0
        self._idle_health_info_every = max(1, int(os.getenv("MONGO_WRITE_QUEUE_IDLE_HEALTH_INFO_EVERY", "10")))

    async def start(self) -> None:
        if self._worker_tasks:
            return
        self._worker_tasks = [
            asyncio.create_task(self._worker_loop(worker_id=index + 1), name=f"mongo-write-worker-{index + 1}")
            for index in range(self.worker_count)
        ]
        self._health_task = asyncio.create_task(self._health_loop(), name="mongo-write-health")
        log.info(
            "[mongo-writer] started max_queue_size=%s high_watermark=%s flush_interval=%ss workers=%s persist_bars=%s failed_writes_path=%s",
            self.max_queue_size,
            self.high_watermark,
            self.flush_interval_seconds,
            self.worker_count,
            self.persist_bars,
            self.emergency_failed_writes_path,
        )

    async def stop(self, *, flush_timeout_seconds: float = 15.0) -> None:
        self._accepting = False
        self._has_jobs.set()
        started = time.monotonic()
        while self.pending_jobs and (time.monotonic() - started) < float(flush_timeout_seconds):
            await asyncio.sleep(0.2)
            self._has_jobs.set()

        for worker_task in self._worker_tasks:
            if not worker_task.done():
                worker_task.cancel()
        for worker_task in self._worker_tasks:
            try:
                await worker_task
            except asyncio.CancelledError:
                pass
        self._worker_tasks = []
        if self._health_task is not None:
            self._health_task.cancel()
            try:
                await self._health_task
            except asyncio.CancelledError:
                pass

        log.info("[mongo-writer] stopped remaining_jobs=%s", self.pending_jobs)

    @property
    def pending_jobs(self) -> int:
        return (
            len(self._critical_queue)
            + len(self._normal_queue)
            + len(self._low_queue)
            + len(self._pending_trade_upserts)
            + len(self._pending_bot_state)
            + len(self._pending_bars)
        )

    def enqueue_trade_upsert(self, trade, *, critical: bool = True) -> bool:
        return self._enqueue_job(
            MongoWriteJob(
                operation_type="trade_upsert",
                priority=PRIORITY_CRITICAL if critical else PRIORITY_NORMAL,
                payload=trade,
                max_attempts=10 if critical else 5,
                dedupe_key=getattr(trade, "trade_id", None),
            )
        )

    def enqueue_trade_event(
        self,
        *,
        trade_id: str,
        event_type: str,
        ts: int,
        symbol: str,
        user_id: str,
        mode: str,
        payload: dict[str, Any] | None = None,
        idempotency_key: str | None = None,
        priority: str = "normal",
    ) -> bool:
        resolved_priority = {
            "critical": PRIORITY_CRITICAL,
            "normal": PRIORITY_NORMAL,
            "low": PRIORITY_LOW,
        }.get(str(priority), PRIORITY_NORMAL)
        max_attempts = 10 if resolved_priority == PRIORITY_CRITICAL else (5 if resolved_priority == PRIORITY_NORMAL else 2)
        return self._enqueue_job(
            MongoWriteJob(
                operation_type="trade_event_insert",
                priority=resolved_priority,
                max_attempts=max_attempts,
                dedupe_key=idempotency_key,
                payload={
                    "trade_id": trade_id,
                    "event_type": event_type,
                    "ts": int(ts),
                    "symbol": symbol,
                    "user_id": user_id,
                    "mode": mode,
                    "payload": payload or {},
                    "idempotency_key": idempotency_key,
                },
            )
        )

    def enqueue_bot_state_set(self, key: str, value: int) -> bool:
        return self._enqueue_job(
            MongoWriteJob(
                operation_type="bot_state_set",
                priority=PRIORITY_LOW,
                payload={"key": str(key), "value": int(value)},
                max_attempts=2,
                dedupe_key=str(key),
            )
        )

    def enqueue_bars_upsert_many(self, bars: list[Any]) -> bool:
        if not bars:
            return True
        if not self.persist_bars:
            log.debug(
                "[mongo-writer] bars persistence disabled by MONGO_PERSIST_BARS=false count=%s",
                len(bars),
            )
            return True
        sample = bars[0]
        dedupe_key = f"{str(getattr(sample, 'symbol', '-')).upper()}:{str(getattr(sample, 'tf', '-'))}"
        return self._enqueue_job(
            MongoWriteJob(
                operation_type="bars_upsert_many",
                priority=PRIORITY_LOW,
                payload=list(bars),
                max_attempts=2,
                dedupe_key=dedupe_key,
            )
        )

    def _enqueue_job(self, job: MongoWriteJob) -> bool:
        if not self._accepting:
            return False

        if job.priority == PRIORITY_LOW and self.pending_jobs >= self.high_watermark:
            if job.operation_type == "bars_upsert_many":
                self._stats.dropped_low_priority += 1
                log.warning(
                    "[mongo-writer] dropped low-priority bars job queue=%s high_watermark=%s",
                    self.pending_jobs,
                    self.high_watermark,
                )
                return False

        self._ensure_capacity(job.priority)
        if self.pending_jobs >= self.max_queue_size and job.priority != PRIORITY_CRITICAL:
            self._stats.dropped_low_priority += 1
            log.warning(
                "[mongo-writer] dropped non-critical job operation=%s queue=%s max=%s",
                job.operation_type,
                self.pending_jobs,
                self.max_queue_size,
            )
            return False

        if job.operation_type == "trade_upsert" and job.dedupe_key:
            current = self._pending_trade_upserts.get(job.dedupe_key)
            if current is None or current.created_at <= job.created_at:
                self._pending_trade_upserts[job.dedupe_key] = job
        elif job.operation_type == "bot_state_set" and job.dedupe_key:
            self._pending_bot_state[job.dedupe_key] = job
        elif job.operation_type == "bars_upsert_many" and job.dedupe_key:
            self._pending_bars[job.dedupe_key] = self._merge_bars_job(self._pending_bars.get(job.dedupe_key), job)
        elif job.priority == PRIORITY_CRITICAL:
            self._critical_queue.append(job)
        elif job.priority == PRIORITY_NORMAL:
            self._normal_queue.append(job)
        else:
            self._low_queue.append(job)

        self._has_jobs.set()
        return True

    def _merge_bars_job(self, current: MongoWriteJob | None, incoming: MongoWriteJob) -> MongoWriteJob:
        if current is None:
            return incoming
        by_close = {
            int(getattr(bar, "close_time", 0)): bar
            for bar in list(current.payload) + list(incoming.payload)
        }
        merged = sorted(by_close.values(), key=lambda item: int(getattr(item, "close_time", 0)))
        current.payload = merged
        current.created_at = min(current.created_at, incoming.created_at)
        return current

    def _ensure_capacity(self, priority: int) -> None:
        if self.pending_jobs < self.max_queue_size:
            return
        while self.pending_jobs >= self.max_queue_size:
            if self._pending_bars:
                self._pending_bars.pop(next(iter(self._pending_bars)))
                self._stats.dropped_low_priority += 1
                continue
            if self._pending_bot_state:
                self._pending_bot_state.pop(next(iter(self._pending_bot_state)))
                self._stats.dropped_low_priority += 1
                continue
            if self._pending_trade_upserts and priority == PRIORITY_CRITICAL:
                break
            if self._low_queue:
                self._low_queue.popleft()
                self._stats.dropped_low_priority += 1
                continue
            if priority == PRIORITY_CRITICAL and self._normal_queue:
                self._normal_queue.popleft()
                self._stats.dropped_low_priority += 1
                continue
            break

    async def _worker_loop(self, *, worker_id: int) -> None:
        while self._accepting or self.pending_jobs:
            try:
                await self._flush_low_priority_buffers_if_due()
                job = self._next_due_job()
                if job is None:
                    self._has_jobs.clear()
                    try:
                        await asyncio.wait_for(self._has_jobs.wait(), timeout=0.2)
                    except asyncio.TimeoutError:
                        pass
                    continue
                await self._process_job(job)
            except Exception as exc:
                log.exception("[mongo-writer] worker loop error worker=%s error=%s:%s", worker_id, type(exc).__name__, exc)

    async def _health_loop(self) -> None:
        try:
            while self._accepting or self.pending_jobs:
                await asyncio.sleep(self.health_interval_seconds)
                self._log_health()
                await self._emit_health_alerts_if_needed()
        except asyncio.CancelledError:
            raise

    def _log_health(self) -> None:
        oldest_age_sec = 0
        oldest = self._oldest_created_at()
        if oldest:
            oldest_age_sec = int(max(time.time() - oldest, 0))
        job_counts = self._job_type_counts()
        is_idle_healthy = (
            self.pending_jobs == 0
            and self._critical_queue_size() == 0
            and self._stats.writes_failed == 0
            and self._stats.retries == 0
            and self._stats.consecutive_failures == 0
        )
        if is_idle_healthy:
            self._idle_health_intervals += 1
        else:
            self._idle_health_intervals = 0
        log_fn = log.debug if is_idle_healthy and (self._idle_health_intervals % self._idle_health_info_every) != 1 else log.info
        log_fn(
            "[mongo-writer] health queue=%s critical=%s pending_low=%s success=%s failed=%s retries=%s dropped_low=%s oldest_age=%ss trade_upsert=%s trade_event_insert=%s bot_state_set=%s bars_upsert_many=%s",
            self.pending_jobs,
            self._critical_queue_size(),
            len(self._low_queue) + len(self._pending_bot_state) + len(self._pending_bars),
            self._stats.writes_success,
            self._stats.writes_failed,
            self._stats.retries,
            self._stats.dropped_low_priority,
            oldest_age_sec,
            job_counts["trade_upsert"],
            job_counts["trade_event_insert"],
            job_counts["bot_state_set"],
            job_counts["bars_upsert_many"],
        )

    async def _emit_health_alerts_if_needed(self) -> None:
        now = time.time()
        if self.pending_jobs >= self.high_watermark:
            if self._high_watermark_since is None:
                self._high_watermark_since = now
            elif (now - self._high_watermark_since) >= self.health_interval_seconds and self.admin_alert_service is not None:
                await self.admin_alert_service.send_alert(
                    alert_key="mongo_writer_high_watermark",
                    component="mongo-writer",
                    severity="WARNING",
                    error="queue_above_high_watermark",
                    action_taken="kept critical jobs and compacted low-priority writes",
                    details={
                        "queue_size": self.pending_jobs,
                        "high_watermark": self.high_watermark,
                        "dropped_low_priority": self._stats.dropped_low_priority,
                    },
                )
        else:
            self._high_watermark_since = None

        critical_queue_size = self._critical_queue_size()
        if critical_queue_size > self.critical_queue_alert_threshold:
            self._critical_queue_over_threshold_intervals += 1
        else:
            self._critical_queue_over_threshold_intervals = 0

        oldest = self._oldest_created_at()
        oldest_age_sec = int(max(now - oldest, 0)) if oldest else 0
        if oldest_age_sec > self.critical_oldest_age_alert_seconds and self.admin_alert_service is not None:
            await self.admin_alert_service.send_alert(
                alert_key="mongo_writer_oldest_job_age",
                component="mongo-writer",
                severity="WARNING",
                error="oldest_job_age_exceeded",
                action_taken="workers continue draining queue",
                details={
                    "oldest_age_sec": oldest_age_sec,
                    "queue_size": self.pending_jobs,
                    "critical_queue_size": critical_queue_size,
                },
            )

        if self._critical_queue_over_threshold_intervals >= 2 and self.admin_alert_service is not None:
            await self.admin_alert_service.send_alert(
                alert_key="mongo_writer_critical_queue_stuck",
                component="mongo-writer",
                severity="CRITICAL",
                error="critical_queue_over_threshold",
                action_taken="kept processing critical jobs with worker concurrency",
                details={
                    "critical_queue_size": critical_queue_size,
                    "threshold": self.critical_queue_alert_threshold,
                    "intervals": self._critical_queue_over_threshold_intervals,
                    "oldest_age_sec": oldest_age_sec,
                },
            )

        last_success_at = self._stats.last_success_at
        if last_success_at and (now - last_success_at) >= self.mongo_unavailable_alert_seconds and self._stats.consecutive_failures > 0:
            if self.admin_alert_service is not None:
                await self.admin_alert_service.send_alert(
                    alert_key="mongo_writer_unavailable",
                    component="mongo-writer",
                    severity="CRITICAL",
                    error="mongo_unavailable_for_extended_period",
                    action_taken="runtime kept operating with queued retries",
                    details={
                        "queue_size": self.pending_jobs,
                        "consecutive_failures": self._stats.consecutive_failures,
                        "seconds_since_last_success": int(now - last_success_at),
                    },
                )

    async def _flush_low_priority_buffers_if_due(self) -> None:
        now = time.monotonic()
        if self._accepting and (now - self._last_low_flush_at) < self.flush_interval_seconds:
            return
        self._last_low_flush_at = now

        for job in list(self._pending_bot_state.values()):
            self._low_queue.append(job)
        self._pending_bot_state.clear()

        for job in list(self._pending_bars.values()):
            self._low_queue.append(job)
        self._pending_bars.clear()

    def _next_due_job(self) -> MongoWriteJob | None:
        trade_upsert_job = self._pop_oldest_due_from_mapping(self._pending_trade_upserts)
        if trade_upsert_job is not None:
            return trade_upsert_job
        for queue in (self._critical_queue, self._normal_queue, self._low_queue):
            job = self._pop_first_due(queue)
            if job is not None:
                return job
        return None

    def _pop_oldest_due_from_mapping(self, mapping: dict[str, MongoWriteJob]) -> MongoWriteJob | None:
        if not mapping:
            return None
        now = time.monotonic()
        due_items = [
            (key, job)
            for key, job in mapping.items()
            if not job.next_attempt_at or job.next_attempt_at <= now
        ]
        if not due_items:
            return None
        due_items.sort(key=lambda item: item[1].created_at)
        key, job = due_items[0]
        mapping.pop(key, None)
        return job

    def _pop_first_due(self, queue: deque[MongoWriteJob]) -> MongoWriteJob | None:
        if not queue:
            return None
        now = time.monotonic()
        for _ in range(len(queue)):
            job = queue.popleft()
            if not job.next_attempt_at or job.next_attempt_at <= now:
                return job
            queue.append(job)
        return None

    async def _process_job(self, job: MongoWriteJob) -> None:
        try:
            if job.operation_type == "trade_upsert":
                processed = await self._process_trade_upsert_batch(job)
                if not processed:
                    return
            elif job.operation_type == "trade_event_insert":
                processed = await self._process_trade_event_batch(job)
                if not processed:
                    return
            elif job.operation_type == "bot_state_set":
                await self._process_bot_state_batch(job)
            elif job.operation_type == "bars_upsert_many":
                await self._process_bars_batch(job)
            else:
                raise RuntimeError(f"unknown_mongo_write_operation:{job.operation_type}")
            self._record_success()
        except Exception as exc:
            await self._handle_job_failure(job, exc)

    async def _process_trade_upsert_batch(self, first_job: MongoWriteJob) -> bool:
        jobs = [first_job]
        while len(jobs) < self.critical_batch_size:
            next_job = self._pop_oldest_due_from_mapping(self._pending_trade_upserts)
            if next_job is not None:
                jobs.append(next_job)
                continue
            next_job = self._pop_first_due_matching(self._critical_queue, "trade_upsert")
            if next_job is None:
                break
            jobs.append(next_job)
        trades = [job.payload for job in jobs]
        try:
            await self.trades_repo.bulk_upsert_trades(trades)
        except Exception as exc:
            for job in jobs:
                await self._handle_job_failure(job, exc)
            return False
        return True

    async def _process_trade_event_batch(self, first_job: MongoWriteJob) -> bool:
        jobs = [first_job]
        source_queue = self._normal_queue if first_job.priority != PRIORITY_CRITICAL else self._critical_queue
        while len(jobs) < self.normal_batch_size and source_queue:
            next_job = self._pop_first_due_matching(source_queue, "trade_event_insert")
            if next_job is None:
                break
            jobs.append(next_job)
        docs = [
            self.trade_events_repo.build_event_doc(
                trade_id=job.payload["trade_id"],
                event_type=job.payload["event_type"],
                ts=job.payload["ts"],
                symbol=job.payload["symbol"],
                user_id=job.payload["user_id"],
                mode=job.payload["mode"],
                payload=job.payload.get("payload") or {},
                idempotency_key=job.payload.get("idempotency_key"),
            )
            for job in jobs
        ]
        try:
            await self.trade_events_repo.insert_event_docs(docs, ignore_duplicate_idempotency=True)
        except Exception as exc:
            for job in jobs:
                await self._handle_job_failure(job, exc)
            return False
        return True

    async def _process_bot_state_batch(self, first_job: MongoWriteJob) -> None:
        jobs = [first_job]
        while len(jobs) < self.low_batch_size and self._low_queue:
            next_job = self._pop_first_due_matching(self._low_queue, "bot_state_set")
            if next_job is None:
                break
            jobs.append(next_job)
        values = {job.payload["key"]: int(job.payload["value"]) for job in jobs}
        await self.bot_state_repo.set_many_ints(values)

    async def _process_bars_batch(self, first_job: MongoWriteJob) -> None:
        jobs = [first_job]
        while len(jobs) < self.low_batch_size and self._low_queue:
            next_job = self._pop_first_due_matching(self._low_queue, "bars_upsert_many")
            if next_job is None:
                break
            jobs.append(next_job)
        by_tf: dict[str, list[Any]] = {}
        for job in jobs:
            for bar in job.payload:
                by_tf.setdefault(str(getattr(bar, "tf", "")), []).append(bar)
        for bars in by_tf.values():
            await self.bars_repo.upsert_many(bars)

    def _record_success(self) -> None:
        self._stats.writes_success += 1
        self._stats.last_success_at = time.time()
        self._stats.consecutive_failures = 0

    async def _handle_job_failure(self, job: MongoWriteJob, exc: Exception) -> None:
        self._stats.writes_failed += 1
        self._stats.last_failure_at = time.time()
        self._stats.consecutive_failures += 1
        if _is_mongo_quota_error(exc):
            if job.priority == PRIORITY_CRITICAL:
                await self._write_failed_job_to_jsonl(job, exc)
            log.error(
                "[mongo-writer] mongo quota full operation=%s priority=%s dedupe_key=%s fallback_path=%s error=%s:%s",
                job.operation_type,
                job.priority,
                job.dedupe_key,
                self.emergency_failed_writes_path,
                type(exc).__name__,
                exc,
            )
            await self._emit_quota_alert(job, exc)
            return

        is_transient = isinstance(exc, TRANSIENT_MONGO_ERRORS)
        if is_transient and job.attempts < job.max_attempts:
            delay = RETRY_DELAYS_SECONDS[min(job.attempts, len(RETRY_DELAYS_SECONDS) - 1)]
            job.attempts += 1
            job.next_attempt_at = time.monotonic() + delay
            self._stats.retries += 1
            self._requeue_job(job)
            log.warning(
                "[mongo-writer] retrying operation=%s attempts=%s/%s delay=%ss error=%s:%s",
                job.operation_type,
                job.attempts,
                job.max_attempts,
                delay,
                type(exc).__name__,
                exc,
            )
            self._has_jobs.set()
            return

        log.warning(
            "[mongo-writer] failed operation=%s priority=%s attempts=%s/%s error=%s:%s",
            job.operation_type,
            job.priority,
            job.attempts,
            job.max_attempts,
            type(exc).__name__,
            exc,
        )
        if job.priority == PRIORITY_CRITICAL:
            await self._write_failed_job_to_jsonl(job, exc)
        await self._emit_failure_alert(job, exc)

    def _requeue_job(self, job: MongoWriteJob) -> None:
        if job.operation_type == "trade_upsert" and job.dedupe_key:
            current = self._pending_trade_upserts.get(job.dedupe_key)
            if current is None or current.created_at <= job.created_at:
                self._pending_trade_upserts[job.dedupe_key] = job
        elif job.priority == PRIORITY_CRITICAL:
            self._critical_queue.append(job)
        elif job.priority == PRIORITY_NORMAL:
            self._normal_queue.append(job)
        else:
            self._low_queue.append(job)

    async def _emit_failure_alert(self, job: MongoWriteJob, exc: Exception) -> None:
        if self.admin_alert_service is None:
            return
        severity = "CRITICAL" if job.priority == PRIORITY_CRITICAL else ("WARNING" if job.priority == PRIORITY_NORMAL else "INFO")
        payload = job.payload if isinstance(job.payload, dict) else {}
        await self.admin_alert_service.send_alert(
            alert_key=f"mongo_writer_failure:{job.operation_type}:{job.dedupe_key or '-'}",
            component="mongo-writer",
            severity=severity,
            error=f"{type(exc).__name__}: {exc}",
            action_taken="job dropped after retries" if job.priority != PRIORITY_CRITICAL else "critical write failed after retries",
            symbol=str(payload.get("symbol")) if payload.get("symbol") else None,
            user_id=str(payload.get("user_id")) if payload.get("user_id") else None,
            details={
                "operation_type": job.operation_type,
                "attempts": job.attempts,
                "max_attempts": job.max_attempts,
                "queue_size": self.pending_jobs,
                "dedupe_key": job.dedupe_key,
            },
        )

    async def _emit_quota_alert(self, job: MongoWriteJob, exc: Exception) -> None:
        if self.admin_alert_service is None:
            return
        payload = job.payload if isinstance(job.payload, dict) else {}
        await self.admin_alert_service.send_alert(
            alert_key="mongo_quota_full",
            component="mongo-writer",
            severity="CRITICAL",
            error=f"{type(exc).__name__}: {exc}",
            action_taken=(
                "critical write saved to local JSONL fallback; Mongo quota full. "
                "Disable bars persistence and run cleanup."
            ),
            symbol=str(payload.get("symbol")) if payload.get("symbol") else None,
            user_id=str(payload.get("user_id")) if payload.get("user_id") else None,
            details={
                "operation_type": job.operation_type,
                "attempts": job.attempts,
                "max_attempts": job.max_attempts,
                "queue_size": self.pending_jobs,
                "dedupe_key": job.dedupe_key,
                "fallback_path": str(self.emergency_failed_writes_path),
                "recommended_action": "Mongo quota full. Disable bars persistence and run cleanup.",
            },
        )

    async def _write_failed_job_to_jsonl(self, job: MongoWriteJob, exc: Exception) -> None:
        line = {
            "operation_type": job.operation_type,
            "dedupe_key": job.dedupe_key,
            "payload": _json_safe(job.payload),
            "created_at": job.created_at,
            "error": f"{type(exc).__name__}: {exc}",
            "attempts": job.attempts,
            "max_attempts": job.max_attempts,
        }
        await asyncio.to_thread(self._append_failed_job_line, line)

    def _append_failed_job_line(self, line: dict[str, Any]) -> None:
        self.emergency_failed_writes_path.parent.mkdir(parents=True, exist_ok=True)
        with self.emergency_failed_writes_path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(line, ensure_ascii=False, default=str))
            fh.write("\n")

    def _oldest_created_at(self) -> float | None:
        candidates: list[float] = []
        for queue in (self._critical_queue, self._normal_queue, self._low_queue):
            if queue:
                candidates.append(queue[0].created_at)
        for job in self._pending_trade_upserts.values():
            candidates.append(job.created_at)
        for job in self._pending_bot_state.values():
            candidates.append(job.created_at)
        for job in self._pending_bars.values():
            candidates.append(job.created_at)
        return min(candidates) if candidates else None

    def _pop_first_due_matching(self, queue: deque[MongoWriteJob], operation_type: str) -> MongoWriteJob | None:
        if not queue:
            return None
        now = time.monotonic()
        for _ in range(len(queue)):
            job = queue.popleft()
            if job.operation_type == operation_type and (not job.next_attempt_at or job.next_attempt_at <= now):
                return job
            queue.append(job)
        return None

    def _critical_queue_size(self) -> int:
        return len(self._critical_queue) + len(self._pending_trade_upserts)

    def _job_type_counts(self) -> dict[str, int]:
        counts = {
            "trade_upsert": 0,
            "trade_event_insert": 0,
            "bot_state_set": 0,
            "bars_upsert_many": 0,
        }
        for job in self._critical_queue:
            counts[job.operation_type] = counts.get(job.operation_type, 0) + 1
        for job in self._normal_queue:
            counts[job.operation_type] = counts.get(job.operation_type, 0) + 1
        for job in self._low_queue:
            counts[job.operation_type] = counts.get(job.operation_type, 0) + 1
        for job in self._pending_trade_upserts.values():
            counts[job.operation_type] = counts.get(job.operation_type, 0) + 1
        for job in self._pending_bot_state.values():
            counts[job.operation_type] = counts.get(job.operation_type, 0) + 1
        for job in self._pending_bars.values():
            counts[job.operation_type] = counts.get(job.operation_type, 0) + 1
        return counts


class QueueBackedBarsPersistenceAsync:
    supports_nonblocking_enqueue = True

    def __init__(self, *, queue: MongoWriteQueue) -> None:
        self.queue = queue

    async def upsert_many(self, bars: list[Any]) -> None:
        self.queue.enqueue_bars_upsert_many(bars)


class QueueBackedBotStatePersistenceAsync:
    def __init__(self, *, cache, persistence_repo, queue: MongoWriteQueue) -> None:
        self.cache = cache
        self.persistence_repo = persistence_repo
        self.queue = queue

    async def preload_from_persistence(self) -> None:
        if self.persistence_repo is None:
            return
        try:
            rows = await self.persistence_repo.list_ints()
        except Exception as exc:
            log.warning("[cache] bot_state preload skipped error=%s:%s", type(exc).__name__, exc)
            return
        self.cache.preload(rows)

    async def get_int(self, key: str, default: int = 0) -> int:
        return self.cache.get_int(key, default=default)

    async def set_int(self, key: str, value: int) -> None:
        self.cache.set_int(key, value)
        self.queue.enqueue_bot_state_set(key, int(value))

    async def acquire_lock(self, *, key: str, owner: str, ttl_ms: int = 120_000) -> bool:
        acquired = self.cache.acquire_lock(key=key, owner=owner, ttl_ms=ttl_ms)
        log.debug("[cache] bot_state lock key=%s owner=%s acquired=%s", key, owner, acquired)
        return acquired

    async def release_lock(self, *, key: str, owner: str) -> None:
        self.cache.release_lock(key=key, owner=owner)


class QueueBackedTradesRepositoryAsync:
    def __init__(self, *, persistence_repo, queue: MongoWriteQueue) -> None:
        self.persistence_repo = persistence_repo
        self.queue = queue

    async def create_trade(self, trade) -> None:
        self.queue.enqueue_trade_upsert(trade, critical=True)

    async def update_trade(self, trade) -> None:
        self.queue.enqueue_trade_upsert(trade, critical=True)

    def __getattr__(self, item: str) -> Any:
        return getattr(self.persistence_repo, item)


class QueueBackedTradeEventsRepositoryAsync:
    def __init__(self, *, persistence_repo, queue: MongoWriteQueue) -> None:
        self.persistence_repo = persistence_repo
        self.queue = queue

    async def ensure_indexes(self) -> None:
        await self.persistence_repo.ensure_indexes()

    async def add_event(
        self,
        *,
        trade_id: str,
        event_type: str,
        ts: int,
        symbol: str,
        user_id: str,
        mode: str,
        payload: dict[str, Any] | None = None,
    ) -> str:
        priority = "critical" if event_type in {"OPENED", "CLOSED", "STOP", "TP_HIT"} else "normal"
        self.queue.enqueue_trade_event(
            trade_id=trade_id,
            event_type=event_type,
            ts=ts,
            symbol=symbol,
            user_id=user_id,
            mode=mode,
            payload=payload,
            priority=priority,
        )
        return ""

    async def add_event_once(
        self,
        *,
        idempotency_key: str,
        trade_id: str,
        event_type: str,
        ts: int,
        symbol: str,
        user_id: str,
        mode: str,
        payload: dict[str, Any] | None = None,
    ) -> tuple[str | None, bool]:
        priority = "critical" if event_type in {"OPENED", "CLOSED", "STOP", "TP_HIT"} else "normal"
        enqueued = self.queue.enqueue_trade_event(
            trade_id=trade_id,
            event_type=event_type,
            ts=ts,
            symbol=symbol,
            user_id=user_id,
            mode=mode,
            payload=payload,
            idempotency_key=idempotency_key,
            priority=priority,
        )
        return None, enqueued

    def __getattr__(self, item: str) -> Any:
        return getattr(self.persistence_repo, item)
