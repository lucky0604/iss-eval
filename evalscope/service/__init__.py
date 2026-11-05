# Copyright (c) Alibaba, Inc. and its affiliates.
"""EvalScope Flask Service."""

from .app import create_app, run_service
from .worker import run_worker

__all__ = ['create_app', 'run_service', 'run_worker']
