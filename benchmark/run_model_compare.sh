#!/usr/bin/env bash
# Multi-agent-model comparison for the interactive multi-turn benchmark.
#
# Runs ONE task against several AGENT models (the model being benchmarked) with
# the user-LLM held FIXED (the USER_LLM_MODEL from .env), then prints a per-model
# comparison: reward / per-round / agent rounds / clarifications / corrections /
# force-advances / judge-vs-scorer divergence / runtime / cost.
#
# Agent backends (claude-code speaks the Anthropic Messages API):
#   - deepseek : ANTHROPIC_BASE_URL=https://api.deepseek.com/anthropic (2 distinct
#                models: deepseek-v4-flash / deepseek-v4-pro). NOTE claude-sonnet-5
#                is silently aliased to flash there — do not use it.
#   - novita   : ANTHROPIC_BASE_URL=https://api.novita.ai/anthropic (146 models,
#                e.g. zai-org/glm-5.2, moonshotai/kimi-k3, qwen/qwen3.7-max,
#                deepseek/deepseek-v4-pro, ...). Use the id as it appears in
#                `GET https://api.novita.ai/openai/v1/models`.
#
# Usage:
#   ./benchmark/run_model_compare.sh
#   AGENT_MODELS="deepseek/deepseek-v4-flash deepseek/deepseek-v4-pro novita/zai-org/glm-5.2 novita/moonshotai/kimi-k3" ./benchmark/run_model_compare.sh
#
# Env overrides: TASK, AGENT, PLUGIN, ENV_FILE, SANDBOX_TIMEOUT, AGENT_MODELS.
# Each run is ~30-60 min and billed; run the grid overnight. Logs land in
# /tmp/model-compare__<backend>__<model>.log. Keys are read from $ENV_FILE
# (ANTHROPIC_AUTH_TOKEN for deepseek, NOVITA_API_KEY for novita).
set -u

cd "$(dirname "$0")/.." || exit 1

TASK="${TASK:-tasks/benchmark/devteam}"
AGENT="${AGENT:-benchmark.interactive_agent:InteractiveUserClaude}"
PLUGIN="${PLUGIN:-benchmark.debug_long_sandbox_plugin:LongSandboxPlugin}"
ENV_FILE="${ENV_FILE:-.env}"
SANDBOX_TIMEOUT="${SANDBOX_TIMEOUT:-10800}"

# backend-prefixed agent models (backend = key into BACKENDS below).
# kimi-k2.5 is an older-generation model included to probe the difficulty floor
# (weak-model calibration). All ids are verified to support the anthropic endpoint.
AGENT_MODELS="${AGENT_MODELS:-deepseek/deepseek-v4-flash novita/zai-org/glm-5.2 novita/moonshotai/kimi-k3 novita/moonshotai/kimi-k2.5}"

deepseek_key=$(sed -n 's/^ANTHROPIC_AUTH_TOKEN=//p' "$ENV_FILE" | tail -1)
novita_key=$(sed -n 's/^NOVITA_API_KEY=//p' "$ENV_FILE" | tail -1)
# user-LLM stays fixed at the .env value (only the agent model varies).
user_llm_model=$(sed -n 's/^USER_LLM_MODEL=//p' "$ENV_FILE" | tail -1)

declare -A BACKENDS=(
  [deepseek]="https://api.deepseek.com/anthropic|$deepseek_key"
  [novita]="https://api.novita.ai/anthropic|$novita_key"
)

ROWS=()
for entry in $AGENT_MODELS; do
  backend=${entry%%/*}
  model=${entry#*/}
  spec="${BACKENDS[$backend]:-}"
  if [ -z "$spec" ]; then
    echo "!! unknown backend '$backend' (expected deepseek|novita); skipping $entry"
    continue
  fi
  base="${spec%%|*}"
  key="${spec#*|}"
  log="/tmp/model-compare__${backend}__${model}.log"
  echo ">>> [$backend/$model] running harbor (user-LLM fixed = $user_llm_model) ..."
  # Pre-setting the agent backend overrides .env (dotenv does not clobber env).
  ANTHROPIC_BASE_URL="$base" ANTHROPIC_AUTH_TOKEN="$key" \
    USER_LLM_MODEL="$user_llm_model" PYTHONPATH="$PWD" NOVITA_SANDBOX_TIMEOUT="$SANDBOX_TIMEOUT" \
    .venv/bin/harbor run -e novita --env-file "$ENV_FILE" -p "$TASK" -a "$AGENT" -m "$model" \
      --plugin "$PLUGIN" > "$log" 2>&1
  jobdir=$(grep -oP 'Results written to \Kjobs/[0-9_]+' "$log" | head -1)
  if [ -z "$jobdir" ]; then
    echo "    !! no job dir found — run failed? see $log"
    ROWS+=("$entry|FAILED")
    continue
  fi
  row=$(.venv/bin/python - "$jobdir" "$entry" <<'PY'
import json
import os
import sys
from datetime import datetime

jobdir, label = sys.argv[1], sys.argv[2]

reward_file = transcript_file = None
for root, _, files in os.walk(jobdir):
    for f in files:
        if f == "reward.json":
            reward_file = os.path.join(root, f)
        if f == "interactive_transcript.json":
            transcript_file = os.path.join(root, f)

r = json.load(open(reward_file)) if reward_file else {}
reward = r.get("reward", "?")
rounds = " ".join(f"R{i}={r.get(f'round_{i}', '?')}" for i in range(1, 5))

res = {}
rj = os.path.join(jobdir, "result.json")
if os.path.exists(rj):
    res = json.load(open(rj))
runtime = ""
if res.get("started_at") and res.get("finished_at"):
    runtime = str(datetime.fromisoformat(res["finished_at"]) - datetime.fromisoformat(res["started_at"]))
cost = res.get("cost_usd")
cost_s = f"{cost:.4f}" if isinstance(cost, (int, float)) else "n/a"

n_rounds = n_clar = n_corr = n_forced = div = "?"
if transcript_file:
    t = json.load(open(transcript_file))
    n_rounds = t.get("num_rounds")
    decs = t.get("decisions", [])
    n_clar = sum(1 for d in decs if d.get("action") == "answer")
    n_corr = sum(1 for d in decs if d.get("action") == "judge" and not d.get("satisfied") and not d.get("forced_advance"))
    n_forced = sum(1 for d in decs if d.get("forced_advance"))
    div = 0
    for d in decs:
        if d.get("action") != "judge":
            continue
        ms = d.get("milestone_index")
        score = r.get(f"round_{ms}")
        if score is None:
            continue
        sat = d.get("satisfied")
        if (sat and score == 0) or (not sat and score == 1):
            div += 1

print(f"{label}|{reward}|{rounds}|{n_rounds}|{n_clar}|{n_corr}|{n_forced}|{div}|{runtime}|{cost_s}")
PY
)
  ROWS+=("$row")
  echo "    $row"
done

echo
echo "=== SUMMARY ==="
echo "columns: agent_model | reward | per-round | agent_rounds | clarifications | corrections | force_advances | judge_vs_scorer_divergence | runtime | cost"
printf '%s\n' "${ROWS[@]}"
