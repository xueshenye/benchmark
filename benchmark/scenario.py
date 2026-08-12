"""Scenario model: the ground-truth design for a multi-turn benchmark task.

A scenario describes a sequence of cumulative requirement *milestones*. The real
"user" (an LLM) renders each milestone's ``user_intent`` into a natural message
based on the agent's actual output, and judges whether the previous output
satisfied the current milestone: satisfied → advance to the next milestone,
unsatisfied → give corrective feedback (repeat the current milestone), and once
corrective rounds exceed ``max_corrections`` the interaction force-advances
(the verifier then scores that milestone 0). ``max_rounds`` is a hard cap on the
total number of agent rounds. The verifier checks each milestone's
``requirement`` against the final workspace state (cumulative regression).
"""

from __future__ import annotations

import json
from pathlib import Path

from pydantic import BaseModel, Field, model_validator


class Milestone(BaseModel):
    """One graded requirement checkpoint of the multi-turn scenario."""

    index: int = Field(ge=1)
    requirement: str = Field(description="Ground-truth requirement (cumulative).")
    user_intent: str = Field(description="What the user-LLM should convey, in its own words.")
    test_id: str = Field(description="Key that maps to a check function in the task's scorer.")
    user_knowledge: str = Field(
        default="",
        description=(
            "Facts the simulated user knows and may REVEAL if the agent asks "
            "(clarification sub-loop), but must not volunteer. Ground truth for "
            "the user-LLM's answers to clarifying questions."
        ),
    )


class Scenario(BaseModel):
    """Full multi-milestone ground truth for one task."""

    user_persona: str = Field(description="Persona the simulated user plays.")
    milestones: list[Milestone] = Field(min_length=1)
    max_rounds: int = Field(ge=1, description="Hard cap on total agent rounds.")
    max_corrections: int = Field(
        default=1, ge=0, description="Corrective rounds allowed per milestone before force-advance."
    )
    max_clarifications: int = Field(
        default=2,
        ge=0,
        description=(
            "Answer rounds allowed per milestone in the clarification sub-loop: when the "
            "agent's output is primarily clarifying questions, the user answers them on the "
            "same milestone without consuming a correction. Once exhausted, further "
            "question-asking rounds are treated as corrections."
        ),
    )

    @model_validator(mode="after")
    def _validate(self) -> "Scenario":
        if self.max_rounds < len(self.milestones):
            raise ValueError(
                f"max_rounds={self.max_rounds} < len(milestones)={len(self.milestones)}"
            )
        indices = [m.index for m in self.milestones]
        if indices != list(range(1, len(indices) + 1)):
            raise ValueError(f"milestone indices must be 1..N consecutive, got {indices}")
        ids = [m.test_id for m in self.milestones]
        if len(set(ids)) != len(ids):
            raise ValueError(f"test_id must be unique, got {ids}")
        return self

    def milestone_by_index(self, index: int) -> Milestone:
        for m in self.milestones:
            if m.index == index:
                return m
        raise KeyError(f"no milestone with index {index}")

    @classmethod
    def load(cls, path: str | Path) -> "Scenario":
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        return cls.model_validate(data)

    @classmethod
    def parse(cls, text: str) -> "Scenario":
        """Parse a scenario from a JSON string (e.g. read from inside a container)."""
        return cls.model_validate(json.loads(text))
