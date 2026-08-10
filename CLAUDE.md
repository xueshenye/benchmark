# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project status

This is a **brand-new, empty project** (only `.claude/settings.local.json` exists). Nothing has been scaffolded yet.

**Goal:** build a **Multi-turn Agent Benchmark for the programming domain** on top of the [Harbor](https://github.com/laude-institute/harbor) framework. Requirements will be fully specified in later conversations — treat this file as project charter + framework reference, not as spec.

Sources of truth for Harbor (network access to `github.com`/docs is restricted here; `raw.githubusercontent.com` and the GitHub API work via `curl`):
- Repo: `laude-institute/harbor` (monorepo: `src/harbor/`, `adapters/`, `docs/`, `examples/`, `rfcs/`)
- Docs: `harborframework.com/docs`; task-authoring guide is `docs/content/docs/tasks/task-tutorial.mdx`
- Cookbook: `harbor-framework/harbor-cookbook` (end-to-end examples)
- The Harbor repo's own `AGENTS.md` (= its CLAUDE.md) documents the full codebase layout

## What Harbor is

Harbor (by the creators of [Terminal-Bench](https://www.tbench.ai), the official harness for Terminal-Bench-2.0) is a framework for **evaluating and optimizing AI agents in containerized environments**. It can:
- Run arbitrary agents (Claude Code, Codex CLI, OpenHands, Aider, Terminus-2, …) against benchmark tasks
- Build and share **custom benchmarks** (this is what this project does)
- Parallelize across providers: Docker (local, default), Daytona, Modal, E2B, GKE, etc.
- Generate rollouts for RL optimization

## Installation

```bash
uv tool install harbor      # or: pip install harbor
```

Requires Docker for local runs. Verify with `harbor --help`, `harbor run --help`, `harbor datasets list`. Harbor is **not yet installed** in any of the envs under `/ssd/xueshenye/env/` — do not assume it's importable; use the CLI (`harbor ...`) or a fresh env.

## Core concepts

- **Task** — one unit of evaluation. A task is a directory (see structure below).
- **Agent** — implements `BaseAgent` (`src/harbor/agents/base.py`). Built-ins include `claude-code`, `codex`, `openhands`, `aider`, and internal `terminus-2`; `oracle` runs a provided solution script, `nop` is a no-op.
- **Environment** — implements `BaseEnvironment`; the container the agent works in. `docker` is default.
- **Trial** — one execution of one agent on one task.
- **Job** — a collection of trials (multiple agents × tasks × attempts).

## Task structure (single-step)

A task directory contains:
```
task/
├── instruction.md      # natural-language task description shown to the agent
├── task.toml           # config/metadata (version, [agent], [verifier], [environment])
├── environment/
│   └── Dockerfile      # the agent's container
├── solution/
│   └── solve.sh        # reference solution (used by the oracle agent)
└── tests/
    ├── test.sh         # verifier: MUST write reward to /logs/verifier/reward.txt
    └── test_outputs.py
```

Scaffold with `harbor task init <name>`. For multi-criterion/weighted scoring or LLM-as-judge, Harbor's **Reward Kit** (`docs/content/docs/rewardkit/`) replaces plain `test.sh`.

## Multi-turn tasks (this project's core mechanism)

Harbor models multi-turn via **multi-step tasks**: a sequence of ordered steps sharing one environment. Each step has its own `instruction.md`, `tests/`, optional `solution/` and `workdir/setup.sh`, plus an optional **`min_reward` gate** that aborts the trial if a step's reward is below threshold (used for early stopping / dependency between steps). Layout:

```
task/
├── task.toml              # declare [[steps]] in execution order (schema_version = "1.4")
├── environment/Dockerfile # built once, shared across all steps
└── steps/
    ├── step-one/
    │   ├── instruction.md
    │   ├── workdir/setup.sh
    │   ├── tests/test.sh
    │   └── solution/solve.sh
    └── step-two/
        └── instruction.md
```

Reference docs: `docs/content/docs/tasks/multi-step.mdx` (full `[[steps]]` field reference: `agent.timeout_sec`, `verifier.timeout_sec`, `min_reward`, `healthcheck.*`, `artifacts`, …). `examples/tasks/hello-multi-step-{simple,advanced}` show working multi-step tasks.

## Authoring workflow (from Harbor's task tutorial)

1. `harbor task init <name>` → generates the task skeleton
2. Write `instruction.md`; configure `task.toml` (timeouts, category/tags, explicit `cpus`/`memory_mb`/`gpus` if needed; `os = "windows"` for Windows targets)
3. Define `environment/Dockerfile` with the dependencies the agent needs
4. Interactively verify the approach: `harbor task start-env -p <task> -e docker -a -i`
5. Write `solution/solve.sh` (must be executable)
6. Write `tests/test.sh` + pytest files; the script must end by writing `1` or `0` to `/logs/verifier/reward.txt`
7. Validate solvability with the oracle: `harbor run -p <task> -a oracle` (expect reward 1)
8. Test with a real agent: `harbor run -p <task> -a terminus-2 -m anthropic/claude-...` (or `claude-code`, etc.)
9. Inspect trajectories/verifier logs: `harbor view ./jobs`

A `create-task` skill exists for interactive authoring: `npx skills add harbor-framework/harbor --skill create-task`.

## Running a benchmark

```bash
harbor run --dataset <dataset@version> --agent <agent> --model <model> --n-concurrent N
# pass agent env vars with --ae KEY=VAL; use --env daytona etc. for cloud providers
```

## Status / next steps

Project is empty. Awaiting the full requirements description. Likely first steps once scoped: install Harbor, scaffold the benchmark repo layout, and author a first multi-step programming task using the structure above.
