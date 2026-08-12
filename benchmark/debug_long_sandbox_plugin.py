"""TEMPORARY debug plugin: raise the Novita sandbox lifetime for observation runs.

The installed Harbor Novita provider hardcodes ``NovitaEnvironment._SANDBOX_TIMEOUT_SEC
= 3600`` (1 h auto-kill). The devteam task's 4-milestone interactive run reaches
round ~6-7 in an hour and then loses the sandbox (and its transcript) mid-run.

This plugin bumps the cap to 2 h so a full observation run (all milestones +
verifier + transcript sync) completes. It only affects runs that pass
``--plugin benchmark.debug_long_sandbox_plugin:LongSandboxPlugin``; remove the
flag (and this file) once the task's wall-clock is reworked to fit 1 h or the
cap becomes per-task configurable.

Use: ``harbor run ... --plugin benchmark.debug_long_sandbox_plugin:LongSandboxPlugin``
"""

from __future__ import annotations

import logging
import os

from harbor.models.job.plugin import BaseJobPlugin

# Override with NOVITA_SANDBOX_TIMEOUT=<seconds>; default 7200 (2 h).
_DEFAULT_SANDBOX_TIMEOUT_SEC = 7200

logger = logging.getLogger(__name__)


class LongSandboxPlugin(BaseJobPlugin):
    """Raises the Novita sandbox auto-kill timeout for this job only.

    The installed Harbor Novita provider hardcodes
    ``NovitaEnvironment._SANDBOX_TIMEOUT_SEC = 3600`` (1 h auto-kill) with no
    task.toml / env lever. This plugin patches the class attribute before the
    sandbox is created; the value comes from ``NOVITA_SANDBOX_TIMEOUT`` (seconds)
    or defaults to 2 h.

    It also removes the novita_sandbox SDK's per-request timeout default (30 min)
    so a slow model's single agent round is not killed mid-stream — the runtime
    safety net for the venv patch applied to ``harbor/environments/novita.py``
    ``_run_command`` (``request_timeout=0``), which ``uv sync`` would wipe.

    Only affects runs that pass
    ``--plugin benchmark.debug_long_sandbox_plugin:LongSandboxPlugin``.
    """

    async def on_job_start(self, job) -> None:
        from harbor.environments.novita import NovitaEnvironment

        timeout = int(os.environ.get("NOVITA_SANDBOX_TIMEOUT", _DEFAULT_SANDBOX_TIMEOUT_SEC))
        old = NovitaEnvironment._SANDBOX_TIMEOUT_SEC
        NovitaEnvironment._SANDBOX_TIMEOUT_SEC = timeout

        # Remove the SDK's per-request timeout (30-min default) for this job's
        # sandbox connections: get_request_timeout(None) -> None == unlimited.
        from novita_sandbox.core import connection_config

        def _unlimited(self, request_timeout=None):  # noqa: ANN001
            return None

        connection_config.ConnectionConfig.get_request_timeout = _unlimited
        logger.info(
            "LongSandboxPlugin: raised Novita sandbox timeout %ss -> %ss and "
            "removed per-request timeout (debug observation run)",
            old,
            timeout,
        )

    async def on_job_end(self, job_result) -> None:
        pass
