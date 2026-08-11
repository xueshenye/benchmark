# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project status

> **最新进展/踩坑/计划先看 [`PROJECT_STATE.md`](PROJECT_STATE.md)** —— 它是交接文档(当前进展、执行 provider、凭证、任务 prompt、未完成工作、未来计划)。下面是章程级摘要。

**Goal:** build a **Multi-turn Agent Benchmark for the programming domain** on top of the [Harbor](https://github.com/laude-institute/harbor) framework. A simulated **user (another LLM)** intervenes mid-task based on the agent's actual output; the benchmark evaluates whether the agent keeps context, updates requirements, avoids forgetting old constraints, and finishes the task.

**Current state:** MVP (Design A) implemented and unit-tested. Harbor 0.20.0 in a `uv` venv. Reference solution + scorer validated end-to-end locally (no container): full solution → `reward=1`; only-later-rounds implementation → `reward=0`.

**Key decisions:**
- **Execution container (MVP) = Design A**: one Harbor trial whose whole multi-turn interaction runs inside a custom agent's `run()`. Later iteration = Design B (native multi-step + dynamic instructions via a runner/hook).
- **Per-round context = user-message only**: each round's instruction is just the simulated user's natural message (no history replay); the agent must reconstruct context from the environment + the message.
- **Environment: `uv` venv at `.venv`** (not conda). Harbor CLI `.venv/bin/harbor`.
- **Execution provider = Novita cloud sandbox.** Docker daemon IS installed/running on this machine, but the user has no root/socket permission and no sudo → end-to-end runs use **`harbor run -e novita`** (Novita builds the task Dockerfile into a cloud template). `novita-sandbox` SDK is in the venv. Requires `NOVITA_API_KEY` + user-LLM credentials (`.env.example`). Task authoring + unit tests need none of this.

## Benchmark harness (`benchmark/` — importable, needs to be on `PYTHONPATH`)

- `interactive_agent.py` — `InteractiveUserClaude(ClaudeCode)`. Overrides `run()` to drive the rounds via `TurnController`: round 1 = `instruction.md`; rounds ≥ 2 = simulated-user message; each round runs a fresh `claude --print` (via `super().run()`) against the shared environment, then reads `/logs/agent/claude-code.txt` and extracts the assistant text from stream-json (`_extract_agent_summary`). After each round it snapshots `/workspace` and diffs it (`_gather_workspace_snapshot`/`_workspace_diff`), so the user-LLM judges real file changes, not just the agent's self-report. Writes `interactive_transcript.json` artifact (incl. `decisions` log); populates `AgentContext`.
- `controller.py` — `TurnController`, the transport-agnostic state machine: each turn the user-LLM returns `{"satisfied": bool, "message": str}`; satisfied → next milestone, unsatisfied → corrective round (up to `max_corrections`), exhausted → force-advance (verifier scores that milestone 0). `max_rounds` is the hard cap. Unit-testable with a fake simulator; zero Harbor imports (ready to lift into a Harbor hook for Design B).
- `user_simulator.py` — `UserSimulator`. Uses Harbor's `LiteLLM` (injectable `llm` callable for tests). `judge_and_speak()` parses the user-LLM's strict-JSON decision (`satisfied`/`message`); malformed output falls back to `satisfied=False` + raw text; `render_milestone()` re-requests a milestone after force-advance; `record_turn()` appends only delivered messages. Config via env: `USER_LLM_MODEL` / `USER_LLM_API_BASE` / `USER_LLM_API_KEY`. Accumulates input/output tokens + cost. **`USER_LLM_MODEL` must carry a LiteLLM-recognized provider prefix** — e.g. `openai/moonshotai/kimi-k3` for Novita's `/openai` endpoint; a bare `moonshotai/kimi-k3` crashes LiteLLM's provider resolution. Real LLM calls additionally require the provider account to have balance (Novita returns 403 NOT_ENOUGH_BALANCE otherwise).
- `scenario.py` — pydantic `Scenario`/`Milestone`: `user_persona`, `milestones[{index, requirement, user_intent, test_id}]`, `max_rounds` (≥ len(milestones)), `max_corrections`. Requirements are **cumulative**; `test_id` maps to a scorer check.
- `prompt_templates.py` — `build_turn_decision_prompt` (judge + produce `{satisfied, message}`) and `build_user_message_prompt` (render a milestone's intent); both can render a `workspace_evidence` section (per-round file diff) so the user judges real code.

The agent is registered via Harbor's import-path factory (`-a benchmark.interactive_agent:InteractiveUserClaude`); verified to instantiate.

## Sample task (`tasks/benchmark/multi-round-cli-demo/`)

3-round cumulative scenario (stats CLI): round 1 basic summary → round 2 `--output-json` → round 3 multiple files (keep 1–2 working). `environment/scenario.json` is baked into the container at `/workspace/scenario.json` and read by both the agent (via `exec cat`) and the verifier.

## Verifier & reward protocol (IMPORTANT deviation)

- The task's `tests/scorer.py` reads `scenario.json`, runs each milestone's ground-truth check against the **final** workspace, and writes **`/logs/verifier/reward.json`** as a flat dict: `{"round_1": 0|1, ..., "reward": <product>}`.
- **`reward` = product of per-round scores** → sparse 0/1 that distinguishes "only completed the last round" (early rounds = 0 → reward 0) from "truly completed the full multi-turn task" (all 1 → reward 1). Per-round keys give dense RLVR diagnostics; all keys land in `VerifierResult.rewards`.
- **Harbor only reads `reward.txt` (scalar) or `reward.json` (flat dict str→number).** The requirement's stated `rewards.txt` / the template comment's `rewards.json` do **not** exist in the verifier code — do not use them.
- Multi-key reward allows custom per-round keys; `min_reward` gating is per-step only (Design B), not used in Design A's single trial.

## Harbor CLI (0.20.0) — verified

```bash
H= .venv/bin/harbor
$H task init <org>/<name> [--steps N]        # --steps N scaffolds a multi-step task (steps/step-N/)
$H init <org>/<name> --task | --dataset      # needs org/name (else interactive, aborts without TTY)
$H run -e novita --env-file .env -p <task> -a <agent> -m <model>  # end-to-end via Novita sandbox
$H run -p <task> -e novita --print-config    # free preflight: resolves JobConfig, no API call
$H task start-env -p <task> -e docker -a -i  # interactive env shell (local Docker only)
$H view ./jobs                               # trajectory viewer
```

No `harbor datasets list` in 0.20.0 — registry/dataset/task management is via `harbor dataset`, `harbor hub`, `harbor task`, `harbor add`. The task Dockerfile does **not** need to install claude-code: `ClaudeCode.install()` installs node/npm/claude-code at agent setup (`agents/installed/claude_code.py:150`).

**Agent auth (important):** the agent (claude-code) runs inside the sandbox and needs its own LLM backend — host OAuth does NOT carry over. Set `ANTHROPIC_BASE_URL` + `ANTHROPIC_AUTH_TOKEN` in `.env` (loaded by `--env-file` into the harbor process env, which `claude_code.py` reads). We use DeepSeek's `/anthropic` proxy (mirrors the host Claude setup), so the agent model must be a DeepSeek model (`-m deepseek-v4-flash`), not a `claude-*` name. Without these the CLI fails with `Not logged in` → agent errors → verifier scores all rounds 0.

**Novita provider notes** (`harbor/environments/novita.py`): needs `novita-sandbox` SDK (installed). Task `environment/Dockerfile` instructions supported: FROM/RUN/COPY/ADD/WORKDIR/USER/ENV/ARG/CMD/ENTRYPOINT — our demo Dockerfile (ubuntu:24.04 + python3, WORKDIR /workspace, COPY scenario.json) is fully supported. Template is cached by `environment_name__<hash>_<key-suffix>`; sandbox auto-kills after 1 h (`_SANDBOX_TIMEOUT_SEC=3600`), billed per runtime. Default domain `us-phx-1.sandbox.novita.ai`, override via `NOVITA_DOMAIN`/`NOVITA_API_URL`.

## Environment & install

- `uv sync` to install/refresh (from `pyproject.toml` + `uv.lock`; `package = false`). Dev group: pytest. Novita provider SDK `novita-sandbox` is a main dependency (lazy-loaded by Harbor only when `-e novita`).
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

MVP done. Execution provider chosen: **Novita** (`harbor run -e novita`); end-to-end run is unblocked except for `NOVITA_API_KEY` + `USER_LLM_*` credentials (see `.env.example`). Planned iteration (Design B): refactor rounds to native multi-step — one step per round, a runner/hook generating each step's instruction from the previous step's agent trajectory via the user-LLM, gaining per-step rewards + `min_reward` gating.
