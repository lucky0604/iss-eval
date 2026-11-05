# Copyright (c) Alibaba, Inc. and its affiliates.
"""Run an RQ worker for the Redis queue backend.

The worker imports :func:`evalscope.service.utils.task_jobs.execute_job` by
name, so it must run from an environment where EvalScope is installed. It
shares the output volume with the web process: progress and results are read
from disk, not from Redis.
"""

from __future__ import annotations

import os
from typing import Optional

from evalscope.utils.logger import get_logger

logger = get_logger()


def run_worker(
    redis_url: Optional[str] = None,
    queue_name: Optional[str] = None,
    burst: bool = False,
    max_jobs: Optional[int] = None,
    log_level: str = 'INFO',
) -> None:
    """Start a blocking worker that executes queued tasks.

    Args:
        redis_url: Redis connection URL; defaults to ``EVALSCOPE_REDIS_URL``.
        queue_name: RQ queue name; defaults to ``EVALSCOPE_REDIS_QUEUE``.
        burst: Exit as soon as the queue is empty instead of blocking.
        max_jobs: Exit after executing this many jobs.
        log_level: Worker logging level.
    """
    try:
        import redis
        from rq import Queue, Worker
    except ImportError as error:  # pragma: no cover - dependency is declared with the service extra
        raise SystemExit('The worker requires the redis and rq packages: pip install "evalscope[service]"') from error

    from .utils import queue_config

    url = redis_url or queue_config.redis_url()
    name = queue_name or queue_config.queue_name()

    # The worker executes jobs that poll for cancellation through Redis, so the
    # Redis backend must be selected regardless of the environment default.
    os.environ['EVALSCOPE_QUEUE_BACKEND'] = queue_config.BACKEND_REDIS
    os.environ['EVALSCOPE_REDIS_URL'] = url
    os.environ['EVALSCOPE_REDIS_QUEUE'] = name

    connection = redis.Redis.from_url(url, socket_timeout=5.0, socket_connect_timeout=5.0)
    try:
        connection.ping()
    except Exception as error:  # noqa: BLE001 - a worker without Redis can do nothing
        raise SystemExit(f'Cannot connect to Redis at {url}: {error}') from error

    worker = Worker([Queue(name, connection=connection)], connection=connection)
    logger.info(f'EvalScope worker listening on queue "{name}" (burst={burst}, max_jobs={max_jobs})')
    worker.work(burst=burst, max_jobs=max_jobs, logging_level=log_level)
    logger.info('EvalScope worker stopped')
