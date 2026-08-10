# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project status

**Goal:** build a **Multi-turn Agent Benchmark for the programming domain** on top of the [Harbor](https://github.com/laude-institute/harbor) framework. Full requirements will be described in later conversations — this file is project charter + framework reference.

**Current state (done):** Harbor **0.20.0** installed and CLI verified; project scaffolded with `git`, `pyproject.toml`, `tasks/` (with a `hello-world` example task); initial commit `a62a3f0`.

**Key decisions:**
- **Environment: `uv`-managed venv at `.venv`** (not conda). Harbor CLI: `.venv/bin/harbor`.
- **No Docker** on this machine (daemon socket not accessible to this user). Harbor's default execution provider is local Docker, so **actual job runs (`harbor run`) are deferred** until an execution strategy is chosen (cloud provider like Daytona/Modal/E2B, or Docker elsewhere). Task authoring does not require Docker.

## Environment & install

- Python 3.12.13 (reuses `/ssd/xueshenye/env/claude/bin/python3.12`).
- Dependencies managed by uv via `pyproject.toml` + `uv.lock` (`[tool.uv] package = false` — application project, not a package).
- Install / refresh deps: `uv sync` (creates/updates `.venv`).
- **Network gotcha:** direct PyPI access is slow/flaky and the configured mirrors are stale/broken (`~/.pip/pip.conf` → USTC which times out and has no `harbor`; `/etc/pip.conf` → Tsinghua which only has old `harbor-0.9.0`). `uv sync` works because uv's parallel downloader tolerates the flaky link. Do **not** fall back to `pip install harbor` — it has failed repeatedly. If uv fails, retry rather than switching index/mirror.

## Harbor CLI (0.20.0) — verified commands

All via `.venv/bin/harbor` (alias `H`).

```bash
H task init <org>/<name> [--steps N] [--org X] [--author "Name <email>"] [--tasks-dir PATH]
H init <org>/<name> --task | --dataset        # init task or dataset (no /name → interactive, aborts)
H run -p <task> -a <agent> -m <model>         # run a job (needs execution provider)
H task start-env -p <task> -e docker -a -i     # interactive env shell
H view ./jobs                                  # trajectory viewer
```

Note (0.20.0 CLI differs from older docs): there is **no `harbor datasets list`** — registry/task/dataset management is via `harbor dataset`, `harbor hub`, `harbor task`, `harbor add`. `harbor task init` requires `org/name` (else it interactively prompts and aborts without a TTY). `--steps N` scaffolds a **multi-step** task (see below).

## Task structure

Single-step task (from `harbor task init org/name`):
```
tasks/<name>/
├── instruction.md              # task description shown to the agent
├── task.toml                   # schema_version "1.3"; [task] [verifier] [agent] [environment] [solution]
├── environment/Dockerfile      # agent's container
├── solution/solve.sh           # reference solution (used by oracle)
└── tests/
    ├── test.sh                 # MUST end by writing reward to /logs/verifier/reward.txt (1/0)
    └── test_outputs.py         # pytest unit tests
```

The generated `tests/test.sh` installs uv + pytest (`uvx --with pytest ... pytest --ctrf /logs/verifier/ctrf.json`), then writes the reward based on pytest exit code. For multi-criterion/weighted scoring or LLM-as-judge, use Harbor's **Reward Kit** (`docs/content/docs/rewardkit/`).

## Multi-turn (multi-step) tasks — the core mechanism

Scaffold with `harbor task init org/name --steps N`, producing:
```
tasks/<name>/
├── task.toml        # [[steps]] array-of-tables, execution order; per-step [steps.verifier] etc.
├── environment/Dockerfile      # built once, shared across steps
└── steps/
    ├── step-1/  (instruction.md, tests/, solution/)
    └── step-2/  (...)
```

- Each step has its own `instruction.md`, `tests/test.sh`, optional `solution/solve.sh` and `workdir/setup.sh`; steps share one environment and run sequentially.
- Per-step config: `min_reward` gates early-abort (e.g. `min_reward = 1.0` aborts the trial if that step's reward is below threshold — use for dependent steps). Full field reference (per-step `agent.timeout_sec`, `verifier.timeout_sec`, `healthcheck.*`, `artifacts`, …): `docs/content/docs/tasks/multi-step.mdx`.
- Reference examples in Harbor repo: `examples/tasks/hello-multi-step-{simple,advanced}`.

## Authoring workflow

1. `H task init <org>/<name> [--steps N]` (run from `tasks/`)
2. Write `instruction.md`; configure `task.toml` (timeouts, `os`, explicit `cpus`/`memory_mb`/`gpus` if needed)
3. Implement `environment/Dockerfile`
4. Interactively verify: `H task start-env -p <task> -e docker -a -i`
5. Write `solution/solve.sh` (executable) and `tests/test.sh` (+ pytest files)
6. Validate solvability: `H run -p <task> -a oracle` (expect reward 1)
7. Real agent: `H run -p <task> -a <agent> -m <model>`
8. Inspect: `H view ./jobs`

Docs source of truth: repo `laude-institute/harbor` (its `AGENTS.md` = full codebase layout); guides `docs/content/docs/tasks/task-tutorial.mdx` and `docs/content/docs/tasks/multi-step.mdx`; site `harborframework.com/docs`.

## Status / next steps

Project scaffolded. Execution provider undecided (no Docker). Awaiting the full benchmark requirements description to author real tasks.
