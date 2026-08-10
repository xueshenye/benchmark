# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project status

**Goal:** build a **Multi-turn Agent Benchmark for the programming domain** on top of the [Harbor](https://github.com/laude-institute/harbor) framework. A simulated **user (another LLM)** intervenes mid-task based on the agent's actual output; the benchmark evaluates whether the agent keeps context, updates requirements, avoids forgetting old constraints, and finishes the task.

**Current state:** MVP (Design A) implemented and unit-tested. Harbor 0.20.0 in a `uv` venv. Reference solution + scorer validated end-to-end locally (no container): full solution → `reward=1`; only-later-rounds implementation → `reward=0`.

**Key decisions:**
- **Execution container (MVP) = Design A**: one Harbor trial whose whole multi-turn interaction runs inside a custom agent's `run()`. Later iteration = Design B (native multi-step + dynamic instructions via a runner/hook).
- **Per-round context = user-message only**: each round's instruction is just the simulated user's natural message (no history replay); the agent must reconstruct context from the environment + the message.
- **Environment: `uv` venv at `.venv`** (not conda). Harbor CLI `.venv/bin/harbor`.
- **No Docker** on this machine → end-to-end `harbor run` deferred until an execution provider is chosen (Docker elsewhere, or a cloud provider like Daytona/Modal/E2B). Task authoring + unit tests don't need Docker.

## Benchmark harness (`benchmark/` — importable, needs to be on `PYTHONPATH`)

- `interactive_agent.py` — `InteractiveUserClaude(ClaudeCode)`. Overrides `run()` to drive the rounds: round 1 = `instruction.md`; rounds ≥ 2 = simulated-user message; each round runs a fresh `claude --print` (via `super().run()`) against the shared environment, then reads `/logs/agent/claude-code.txt` and extracts the assistant text from stream-json (`_extract_agent_summary`). Writes `interactive_transcript.json` artifact; populates `AgentContext`.
- `user_simulator.py` — `UserSimulator`. Uses Harbor's `LiteLLM` (injectable `llm` callable for tests). Turns each round's `user_intent` into a natural user message conditioned on the previous round's agent output. Config via env: `USER_LLM_MODEL` / `USER_LLM_API_BASE` / `USER_LLM_API_KEY`. Accumulates input/output tokens + cost.
- `scenario.py` — pydantic `Scenario`/`Round`: `num_rounds`, `user_persona`, `rounds[{index, requirement, user_intent, test_id}]`. Requirements are **cumulative**; `test_id` maps to a scorer check.
- `prompt_templates.py` — user-LLM prompt builder.

The agent is registered via Harbor's import-path factory (`-a benchmark.interactive_agent:InteractiveUserClaude`); verified to instantiate.

## Sample task (`tasks/benchmark/multi-round-cli-demo/`)

3-round cumulative scenario (stats CLI): round 1 basic summary → round 2 `--output-json` → round 3 multiple files (keep 1–2 working). `environment/scenario.json` is baked into the container at `/workspace/scenario.json` and read by both the agent (via `exec cat`) and the verifier.

## Verifier & reward protocol (IMPORTANT deviation)

- The task's `tests/scorer.py` reads `scenario.json`, runs each round's ground-truth check against the **final** workspace, and writes **`/logs/verifier/reward.json`** as a flat dict: `{"round_1": 0|1, ..., "reward": <product>}`.
- **`reward` = product of per-round scores** → sparse 0/1 that distinguishes "only completed the last round" (early rounds = 0 → reward 0) from "truly completed the full multi-turn task" (all 1 → reward 1). Per-round keys give dense RLVR diagnostics; all keys land in `VerifierResult.rewards`.
- **Harbor only reads `reward.txt` (scalar) or `reward.json` (flat dict str→number).** The requirement's stated `rewards.txt` / the template comment's `rewards.json` do **not** exist in the verifier code — do not use them.
- Multi-key reward allows custom per-round keys; `min_reward` gating is per-step only (Design B), not used in Design A's single trial.

## Harbor CLI (0.20.0) — verified

```bash
H= .venv/bin/harbor
$H task init <org>/<name> [--steps N]        # --steps N scaffolds a multi-step task (steps/step-N/)
$H init <org>/<name> --task | --dataset      # needs org/name (else interactive, aborts without TTY)
$H run -p <task> -a <agent> -m <model>       # run a job (needs execution provider)
$H task start-env -p <task> -e docker -a -i  # interactive env shell
$H view ./jobs                               # trajectory viewer
```

No `harbor datasets list` in 0.20.0 — registry/dataset/task management is via `harbor dataset`, `harbor hub`, `harbor task`, `harbor add`. The task Dockerfile does **not** need to install claude-code: `ClaudeCode.install()` installs node/npm/claude-code at agent setup (`agents/installed/claude_code.py:150`).

## Environment & install

- `uv sync` to install/refresh (from `pyproject.toml` + `uv.lock`; `package = false`). Dev group: pytest.
- **Network gotcha:** pip's configured mirrors are stale/broken (USTC times out and lacks `harbor`; Tsinghua only has old `harbor-0.9.0`). Use `uv sync` only — `pip install harbor` has failed repeatedly.
- Tests: `.venv/bin/python -m pytest tests/` (all host-side, no Docker).

## How to add a new multi-turn task

1. `cd tasks && ../.venv/bin/harbor task init <org>/<name>` (single-step skeleton).
2. Write `instruction.md` (round-1 task). Add `environment/scenario.json` describing all rounds (`requirement`/`user_intent`/`test_id`).
3. Dockerfile: base image + task runtime; `COPY scenario.json /workspace/scenario.json`; set `workdir=/workspace` in `task.toml`.
4. `tests/scorer.py`: implement a check function per `test_id`; reuse the pattern in the demo task (subprocess against the workspace; tolerant entry-point discovery).
5. `tests/test.sh`: run the scorer and ensure it writes `reward.json` (with a fallback on failure).
6. Validate locally (no Docker): run `solve.sh` (path-adjusted) then `scorer.py --base-dir <ws> --scenario <scenario> --reward-out <out>`, expecting all `round_*` = 1.

## Docs source of truth

Repo `laude-institute/harbor` (its `AGENTS.md` = codebase layout; `LiteLLM` at `llms/lite_llm.py:61`; `ClaudeCode` at `agents/installed/claude_code.py`; `exec_as_agent` at `agents/installed/base.py:583`; reward paths at `models/trial/paths.py:42-43`). Guides `docs/content/docs/tasks/task-tutorial.mdx` and `docs/content/docs/tasks/multi-step.mdx`; site `harborframework.com/docs`.

## Status / next steps

MVP done. Planned iteration (Design B): refactor rounds to native multi-step — one step per round, a runner/hook generating each step's instruction from the previous step's agent trajectory via the user-LLM, gaining per-step rewards + `min_reward` gating. Execution provider still undecided (no Docker).
