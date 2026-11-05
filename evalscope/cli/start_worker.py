# Copyright (c) Alibaba, Inc. and its affiliates.
"""CLI command for running an EvalScope task-queue worker."""

from evalscope.cli.base import ArgumentParserWithSubParsers, CLICommand


def subparser_func(args):
    """Function which will be called for a specific sub parser."""
    return WorkerCMD(args)


class WorkerCMD(CLICommand):
    name = 'worker'

    def __init__(self, args):
        self.args = args

    @staticmethod
    def define_args(parsers: ArgumentParserWithSubParsers) -> None:
        """Define args for the worker command."""
        parser = parsers.add_parser(
            WorkerCMD.name, help='Run a task-queue worker for the Redis backend started by `evalscope service`'
        )
        parser.add_argument(
            '--redis-url',
            type=str,
            default=None,
            help='Redis connection URL (default: EVALSCOPE_REDIS_URL or redis://localhost:6379/0)',
        )
        parser.add_argument(
            '--queue',
            type=str,
            default=None,
            help='RQ queue name (default: EVALSCOPE_REDIS_QUEUE or "evalscope")',
        )
        parser.add_argument('--burst', action='store_true', default=False, help='Exit once the queue is empty')
        parser.add_argument('--max-jobs', type=int, default=None, help='Exit after executing this many jobs')
        parser.add_argument(
            '--log-level',
            type=str.upper,
            default='INFO',
            choices=['DEBUG', 'INFO', 'WARNING', 'ERROR', 'CRITICAL'],
            help='Worker logging level (default: INFO)',
        )
        parser.set_defaults(func=subparser_func)

    def execute(self):
        """Execute the worker command."""
        from evalscope.service.worker import run_worker

        run_worker(
            redis_url=self.args.redis_url,
            queue_name=self.args.queue,
            burst=self.args.burst,
            max_jobs=self.args.max_jobs,
            log_level=self.args.log_level,
        )
