"""Queue backend configuration shared by the web process and the worker.

The service supports two backends:

* ``inprocess`` (default) keeps Phase 1 semantics: tasks live in the web
  process and disappear when it restarts.
* ``redis`` enqueues serializable jobs into Redis so a separate worker
  process survives web restarts and restarts of the worker itself.

All values come from the environment so the web container and the worker
container can be configured from the same ``.env`` file.
"""

import os

DEFAULT_REDIS_URL = 'redis://localhost:6379/0'
DEFAULT_QUEUE_NAME = 'evalscope'
DEFAULT_CANCEL_TTL = 3600
DEFAULT_REDIS_FAILURE_TTL = 86400
DEFAULT_SUBMIT_LOCK_TTL = 30

BACKEND_INPROCESS = 'inprocess'
BACKEND_REDIS = 'redis'

TERMINAL_STATUSES = ('completed', 'stopped', 'error')


class QueueUnavailableError(RuntimeError):
    """Raised when the queue backend cannot accept or answer requests."""


def _env_int(name: str, default: int, minimum: int) -> int:
    try:
        return max(minimum, int(os.getenv(name, str(default))))
    except ValueError:
        return default


def queue_backend() -> str:
    """Return the configured backend name (``inprocess`` or ``redis``)."""
    return os.getenv('EVALSCOPE_QUEUE_BACKEND', BACKEND_INPROCESS).strip().lower() or BACKEND_INPROCESS


def redis_url() -> str:
    """Return the Redis connection URL used by the queue backend."""
    return os.getenv('EVALSCOPE_REDIS_URL', DEFAULT_REDIS_URL).strip() or DEFAULT_REDIS_URL


def queue_name() -> str:
    """Return the RQ queue name; web and workers must agree on it."""
    return os.getenv('EVALSCOPE_REDIS_QUEUE', DEFAULT_QUEUE_NAME).strip() or DEFAULT_QUEUE_NAME


def cancellation_key(task_id: str) -> str:
    """Return the Redis key a worker polls to stop a running task."""
    return f'evalscope:cancel:{task_id}'


def cancellation_ttl() -> int:
    """Return how long a cancellation request stays valid, in seconds."""
    return _env_int('EVALSCOPE_CANCEL_TTL', DEFAULT_CANCEL_TTL, minimum=60)


def redis_failure_ttl() -> int:
    """Return how long RQ keeps a failed job record, in seconds."""
    return _env_int('EVALSCOPE_REDIS_FAILURE_TTL', DEFAULT_REDIS_FAILURE_TTL, minimum=1)


def submit_lock_key(task_id: str) -> str:
    """Return the Redis key that serialises concurrent submissions of *task_id*."""
    return f'evalscope:submit:{task_id}'


def submit_lock_ttl() -> int:
    """Return how long a submission lock survives a crashed web process, in seconds."""
    return _env_int('EVALSCOPE_SUBMIT_LOCK_TTL', DEFAULT_SUBMIT_LOCK_TTL, minimum=5)
