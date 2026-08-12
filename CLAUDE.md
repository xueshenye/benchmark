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
- `controller.py` — `TurnController`, the transport-agnostic state machine: each turn the user-LLM returns `{"action": "judge"|"answer", "satisfied": bool, "message": str}`; `action="answer"` (clarification sub-loop) keeps the interaction on the SAME milestone (no correction consumed, no advance; capped by `max_clarifications`); satisfied → next milestone; unsatisfied → corrective round (up to `max_corrections`); exhausted → force-advance (verifier scores that milestone 0). `max_rounds` is the hard cap. Unit-testable with a fake simulator; zero Harbor imports (ready to lift into a Harbor hook for Design B).
- `user_simulator.py` — `UserSimulator`. Uses Harbor's `LiteLLM` (injectable `llm` callable for tests). `judge_and_speak()` parses the user-LLM's strict-JSON decision (`action`/`satisfied`/`message`); malformed output falls back to `satisfied=False` + raw text; `render_milestone()` re-requests a milestone after force-advance; `record_turn()` appends only delivered messages. Config via env: `USER_LLM_MODEL` / `USER_LLM_API_BASE` / `USER_LLM_API_KEY`. Accumulates input/output tokens + cost. **`USER_LLM_MODEL` must carry a LiteLLM-recognized provider prefix** — e.g. `openai/moonshotai/kimi-k3` for Novita's `/openai` endpoint; a bare `moonshotai/kimi-k3` crashes LiteLLM's provider resolution. Real LLM calls additionally require the provider account to have balance (Novita returns 403 NOT_ENOUGH_BALANCE otherwise).
- `scenario.py` — pydantic `Scenario`/`Milestone`: `user_persona`, `milestones[{index, requirement, user_intent, test_id, user_knowledge}]`, `max_rounds` (≥ len(milestones)), `max_corrections`, `max_clarifications`. Requirements are **cumulative**; `test_id` maps to a scorer check; `user_knowledge` is info the simulated user reveals **only if the agent asks** (clarification sub-loop).
- `prompt_templates.py` — `build_turn_decision_prompt` (judge + produce `{satisfied, message}`) and `build_user_message_prompt` (render a milestone's intent); both can render a `workspace_evidence` section (per-round file diff) so the user judges real code. The decision prompt includes a faithfulness guard (don't invent requirements beyond the milestone's ground truth).
- `last_only_agent.py` — `LastOnlyClaude`: deterministic "last-milestone-only" agent for end-to-end discriminator validation (writes only the multi-file impl, ignores user messages; expect `reward=0`).

**Design B (native multi-step, experimental, purely additive):**
- `interactive_step_agent.py` — `InteractiveStepClaude`: per-step agent = one `claude --print` run + dumps `agent_summary.txt` + `workspace_snapshot.json` to logs_dir (archived per step).
- `step_driver.py` — `StepDriver`: transport-agnostic between-step logic (snapshot diff → user-LLM judge → advance/correct/force-advance → next instruction). Zero Harbor imports.
- `multi_step_trial.py` — `InteractiveMultiStepTrial(MultiStepTrial)`: wraps `Task.step_instruction` to inject generated instructions; `_after_step` drives StepDriver; early-breaks on completion. Requires `multi_step_reward_strategy="final"`, no `min_reward`.
- `design_b_plugin.py` — `DesignBPlugin`: `on_job_start` monkeypatches `harbor.trial.multi_step.MultiStepTrial` (injected via `harbor run --plugin benchmark.design_b_plugin:DesignBPlugin`).
- Task: `tasks/benchmark/multi-round-cli-demo-multistep/` (6 pre-created steps, shared root `tests/`). Run: `-p <that task> -a benchmark.interactive_step_agent:InteractiveStepClaude --plugin benchmark.design_b_plugin:DesignBPlugin`. Rollback = delete the additive files + task dir.

The agent is registered via Harbor's import-path factory (`-a benchmark.interactive_agent:InteractiveUserClaude`); verified to instantiate.

## Sample task (`tasks/benchmark/multi-round-cli-demo/`)

3-round cumulative scenario (stats CLI): round 1 basic summary → round 2 `--output-json` → round 3 multiple files (keep 1–2 working). `environment/scenario.json` is baked into the container at `/workspace/scenario.json` and read by both the agent (via `exec cat`) and the verifier.

## Task suite (T1–T6, complexity ladder)

Design rationale + capability taxonomy in `docs/task-suite-design.md` (survey of SWE-Interact/SWE-Together/τ-bench/EvoCode-Bench etc.). Six new tasks sit on top of the stats baseline, all locally validated (full solution → reward=1; discriminator → reward=0; Harbor preflight OK; `tests/` has a per-task scorer test):

- **T1 `todo-tracker/`** (4 rounds, empty start): persistent todo CLI — `add/list/done`, then `--all`/`stats`/`--output-json`, then `--priority`/`--status` filters, then `report`/`search`. Probes **state persistence** (`todos.json` in cwd), data modeling, cross-process consistency, regression.
- **T2 `repofix/`** (3 rounds, **seeded-broken repo** in `seed/` baked into the image): fix a mis-grouped/crashing CSV pipeline → harden edge cases (empty/blank/non-numeric/unicode) → refactor into ≥3 functions + add `tests/test_regression.py`. Probes **debugging, edge cases, self-verification (pytest), refactoring, regression tests**. Hidden behavioral checks guard against "building to the visible test" (verified: rewriting the visible tests to pass still scores 0).
- **T3 `pkg-wordcount/`** (3 rounds, empty start): pip-installable `wordcount` package — `count(text)` API + `pyproject.toml` → `top_words` + pytest tests → console entry `wordcount <file>` verified via a real `pip install -e`. Probes **real ecosystem (pip/pytest), package structure, CLI entry, self-verification**. The scorer's `pip install` has a `--user` fallback and resolves the console script next to `sys.executable` (the uv venv needs `python -m ensurepip` for local validation).
- **T4 `support-bot/`** (4 rounds, seeded materials): build a customer-service bot — KB Q&A (round-1 prompt is deliberately terse; the agent must **ask clarifying questions**, answered via the clarification sub-loop) → order tracking via a baked mock HTTP API → package refactor + batch mode + pytest → multilingual (follow the customer's language, overriding M1's "always Chinese") + unknown-question escalation with logging. Probes **long-context real app, active clarification, requirement changes/large refactor, long-term memory + forgetting overridden rules**. Verifier uses hidden `ground_truth/facts.json` + synthetic orders generated at grading time; tampering with the user-provided KB fails the check. `benchmark/partial_support_bot.py:FirstTwoClaude` is the reward=0 discriminator.
- **T5 `ticket-system/`** (4 rounds, seeded materials) — the **product-development** task: zero-to-one build of an internal **support-ticket HTTP service** (REST API + one HTML page, stdlib `http.server` + SQLite). CRUD + restart-surviving persistence → workflow/search/filter + strict status machine → package refactor + SQLite + SLA/overdue → **delete-policy reversal** (v1 contract pins permanent delete; M4 flips to soft-delete + restore) + stats. Probes **long-context real product, active clarification, requirement change/large refactor, memory + forgetting the overridden delete rule**. The verifier **starts the built server itself** on an ephemeral port with its own temp DB and runs hidden end-to-end HTTP checks (restart persistence, SQLite magic bytes, stats math). `benchmark/partial_ticket_system.py:PartialTicketClaude` is the reward=0 discriminator.
- **T6 `devteam/`** (4 rounds, empty start) — converts the real product draft `task_1.txt` (a team **collaborative dev tool**) into a headless-verifiable **CLI + HTML dashboard** task: projects/members/roles (`DEVTEAM_USER`, owner/member/viewer) → mini-VCS over `projects/<proj>/code/` (commit/history/rollback/file-history, author attribution) → schedule + UI (`status` overview, `--output-json`, self-contained `dashboard-<proj>.html`) → `check` (syntax/undefined/TODO) + `autocomplete` + **permission reversal** (M1 pins "viewer read-only"; M4 flips to "viewer can commit"). Probes **long-context real app, active clarification** (round-1 brief is deliberately terse; agent must ask), **requirement change**, **memory + forgetting the overridden permission rule** (M1/M2 checks avoid asserting viewer-read-only so M4's reversal is gradable; M4 asserts viewer CAN commit). No seed/ground_truth — the scorer generates inputs at grading time (seeded name pool, dates relative to today, injected code issues). `benchmark/partial_devteam.py:PartialDevteamClaude` is the reward=0 discriminator (M1+M2 only). Acceptance doc + per-milestone eval criteria: `docs/task-devteam.md`. A **manual human-user mode** (`USER_SIMULATOR=manual` → `benchmark/manual_user.py`) lets the task author play the user by hand, reading each milestone's requirement/eval criteria and typing `s/c/a <message>` or strict JSON.

Authoring rules that carry across tasks (see `docs/task-suite-design.md` §2.3): every milestone requirement is an observable behavior; format/constraint details go in **both** `requirement` and `user_intent` (PROJECT_STATE.md §6.4); each check re-exercises earlier behavior (regression); the verifier checks the real artifact with hidden inputs, never the agent's self-report.

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

MVP + Design A+/Design B done and Novita-validated; **task suite T1–T6 implemented and locally validated** (todo-tracker / repofix / pkg-wordcount / support-bot / ticket-system / devteam; see "Task suite" above) plus the **clarification sub-loop** and **manual human-user mode** (`USER_SIMULATOR=manual` → `benchmark/manual_user.py`) framework capabilities. Next: run each new task 1–2× end-to-end on Novita (needs `.env` credentials — `NOVITA_API_KEY` + `USER_LLM_*` + `ANTHROPIC_*`), observing user-LLM judgement fidelity on harder tasks and watching for judge-vs-scorer divergence (especially T4's M4 "follow the customer's language", T5's M4 soft-delete-reversal, and T6's M4 viewer-permission-reversal overturns), then push RLVR (reward.json multi-key → `VerifierResult.rewards`). Design B refactor path stays available (native multi-step + step-wise runner via `--plugin benchmark.design_b_plugin:DesignBPlugin`).
