# Copyright (c) Alibaba, Inc. and its affiliates.
"""Blueprint modules for EvalScope service."""

from .eval import bp_eval
from .perf import bp_perf
from .perf_reports import bp_perf_reports
from .reports import bp_reports
from .tasks import bp_tasks

__all__ = ['bp_eval', 'bp_perf', 'bp_perf_reports', 'bp_reports', 'bp_tasks']
