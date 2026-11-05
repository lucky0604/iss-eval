# Copyright (c) Alibaba, Inc. and its affiliates.
"""Utility modules for EvalScope service."""

from .benchmarks import (
    DEFAULT_MULTIMODAL_BENCHMARKS,
    DEFAULT_TEXT_BENCHMARKS,
    build_benchmark_entry,
    discover_all_benchmarks,
    parse_benchmark_description,
)
from .log import (
    OUTPUT_DIR,
    build_task_info,
    create_log_file,
    finish_task,
    get_log_content,
    read_task_manifest,
    read_task_result,
    task_file_path,
    update_task_manifest,
    validate_task_id,
    write_task_manifest,
    write_task_result,
)
from .process import (
    ActiveTask,
    TaskStoppedError,
    active_task_ids,
    list_active_tasks,
    run_eval_wrapper,
    run_in_subprocess,
    run_perf_wrapper,
    serialize_result,
    stop_process,
)
from .queue_config import QueueUnavailableError
from .redis_queue import RedisTaskQueue
from .task_jobs import TaskJob
from .task_queue import TaskQueue, TaskRunner, get_task_queue, queue_health, reset_task_queue

__all__ = [
    'OUTPUT_DIR',
    'build_task_info',
    'finish_task',
    'create_log_file',
    'get_log_content',
    'validate_task_id',
    'read_task_manifest',
    'read_task_result',
    'task_file_path',
    'update_task_manifest',
    'write_task_manifest',
    'write_task_result',
    'active_task_ids',
    'list_active_tasks',
    'ActiveTask',
    'TaskQueue',
    'TaskRunner',
    'TaskJob',
    'QueueUnavailableError',
    'RedisTaskQueue',
    'get_task_queue',
    'queue_health',
    'reset_task_queue',
    'run_eval_wrapper',
    'run_perf_wrapper',
    'serialize_result',
    'stop_process',
    'run_in_subprocess',
    'TaskStoppedError',
    'DEFAULT_TEXT_BENCHMARKS',
    'DEFAULT_MULTIMODAL_BENCHMARKS',
    'build_benchmark_entry',
    'discover_all_benchmarks',
    'parse_benchmark_description',
]
