from __future__ import annotations

from collections.abc import Callable, Sequence
from concurrent.futures import Future, ThreadPoolExecutor, wait
from dataclasses import dataclass
from threading import Barrier

from django.db import close_old_connections, connection


@dataclass(frozen=True)
class ConcurrentOutcome[T]:
    """A value or exception returned by one separate-connection worker."""

    value: T | None = None
    error: BaseException | None = None


def run_concurrently[T](
    workers: Sequence[Callable[[], T]], *, timeout: float = 10
) -> list[ConcurrentOutcome[T]]:
    """Start workers together and give each a distinct Django connection.

    The start barrier is orchestration, not a substitute for a database race.
    Callers add a second barrier at a precise read/write boundary when they need
    to force a specific interleaving. Outcomes retain their input order.
    """

    if len(workers) < 2:
        raise ValueError("run_concurrently requires at least two workers")

    start = Barrier(len(workers), timeout=timeout)

    def invoke(worker: Callable[[], T]) -> ConcurrentOutcome[T]:
        close_old_connections()
        try:
            start.wait()
            return ConcurrentOutcome(value=worker())
        except BaseException as exc:  # noqa: BLE001 - race outcomes are assertions.
            return ConcurrentOutcome(error=exc)
        finally:
            connection.close()

    executor = ThreadPoolExecutor(
        max_workers=len(workers), thread_name_prefix="postgres-race"
    )
    futures: list[Future[ConcurrentOutcome[T]]] = [
        executor.submit(invoke, worker) for worker in workers
    ]
    incomplete: set[Future[ConcurrentOutcome[T]]] = set()
    try:
        _, incomplete = wait(futures, timeout=timeout)
        if incomplete:
            raise TimeoutError(
                f"concurrent workers exceeded the {timeout:g}-second deadline"
            )
        return [future.result() for future in futures]
    finally:
        for future in incomplete:
            future.cancel()
        # A context manager always waits for running workers during __exit__, which
        # defeats the caller-visible deadline. PostgreSQL statement/lock timeouts
        # bound database workers; cooperative non-database tests may release their
        # worker after receiving TimeoutError.
        executor.shutdown(wait=not incomplete, cancel_futures=bool(incomplete))
