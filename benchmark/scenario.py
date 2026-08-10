"""Scenario model: the ground-truth multi-round design for a benchmark task.

A scenario describes a sequence of cumulative requirement changes. The real
"user" (an LLM) renders each round's ``user_intent`` into a natural message
based on the agent's actual output; the verifier checks each round's
``requirement`` against the final workspace state (cumulative regression).
"""

from __future__ import annotations

import json
from pathlib import Path

from pydantic import BaseModel, Field, model_validator


class Round(BaseModel):
    """One user-intervention round of the multi-turn scenario."""

    index: int = Field(ge=1)
    requirement: str = Field(description="Ground-truth requirement for this round (cumulative).")
    user_intent: str = Field(description="What the user-LLM should convey, in its own words.")
    test_id: str = Field(description="Key that maps to a check function in the task's scorer.")


class Scenario(BaseModel):
    """Full multi-round ground truth for one task."""

    num_rounds: int = Field(ge=1)
    user_persona: str = Field(description="Persona the simulated user plays.")
    rounds: list[Round] = Field(min_length=1)

    @model_validator(mode="after")
    def _validate_rounds(self) -> "Scenario":
        if self.num_rounds != len(self.rounds):
            raise ValueError(
                f"num_rounds={self.num_rounds} != len(rounds)={len(self.rounds)}"
            )
        indices = [r.index for r in self.rounds]
        if indices != list(range(1, len(indices) + 1)):
            raise ValueError(f"round indices must be 1..N consecutive, got {indices}")
        ids = [r.test_id for r in self.rounds]
        if len(set(ids)) != len(ids):
            raise ValueError(f"test_id must be unique, got {ids}")
        return self

    def round_by_index(self, index: int) -> Round:
        for r in self.rounds:
            if r.index == index:
                return r
        raise KeyError(f"no round with index {index}")

    @classmethod
    def load(cls, path: str | Path) -> "Scenario":
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        return cls.model_validate(data)

    @classmethod
    def parse(cls, text: str) -> "Scenario":
        """Parse a scenario from a JSON string (e.g. read from inside a container)."""
        return cls.model_validate(json.loads(text))
