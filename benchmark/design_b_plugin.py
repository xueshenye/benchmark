"""DesignBPlugin: injects InteractiveMultiStepTrial into Harbor's trial selection.

``harbor run --plugin benchmark.design_b_plugin:DesignBPlugin`` runs this
plugin's ``on_job_start`` AFTER ``Job.create`` and BEFORE ``job.run()`` (when
``Trial.create`` first selects the trial class). ``Trial.create`` imports
``MultiStepTrial`` with a function-body local import (trial.py:261), so
replacing the module attribute here is picked up on every trial.

Design B is purely additive: the A+ single-trial path is untouched. Roll back by
removing ``--plugin`` / the plugin and the multi-step task directory.
"""

from __future__ import annotations

from harbor.models.job.plugin import BaseJobPlugin


class DesignBPlugin(BaseJobPlugin):
    """Swaps Harbor's MultiStepTrial for the interactive multi-step trial."""

    async def on_job_start(self, job) -> None:
        import harbor.trial.multi_step as multi_step_module

        from benchmark.multi_step_trial import InteractiveMultiStepTrial

        multi_step_module.MultiStepTrial = InteractiveMultiStepTrial  # type: ignore[assignment]

    async def on_job_end(self, job_result) -> None:
        pass
