#!/usr/bin/env bash
# Multi-agent-model comparison for the interactive multi-turn benchmark.
#
# Runs ONE task against several AGENT models (the model being benchmarked) with
# the user-LLM held FIXED (the USER_LLM_MODEL from .env), each model MTK times
# (default 2), and prints per-model MT@k + mean dense reward + per-round means
# (decay) + first-failed-round + divergence, plus reference/nop baselines.
#
# MT@k (adapted from EvoCode-Bench's MT@4): task-level K independent re-runs per
# model; MT@K = fraction of attempts reaching reward == 1.0 (full pass). Each
# attempt's metrics are cached in $CACHE_DIR (default /tmp/model-compare-cache),
# so a re-run skips already-completed attempts (incremental/resume).
#
# Agent backends (claude-code speaks the Anthropic Messages API). Each backend is
# (base_url | auth_token), resolved from .env (ZAI_BASE_URL etc. if set, else the
# hardcoded default below). Model ids must be the ones the provider's ANTHROPIC
# endpoint actually accepts — NOT the OpenAI-catalog ids, and no "[1m]" suffix.
# Verified working (1-token anthropic probe):
#   - deepseek : https://api.deepseek.com/anthropic        -> deepseek-v4-flash / deepseek-v4-pro
#                 (claude-sonnet-5 is silently aliased to flash — do not use)
#   - novita   : https://api.novita.ai/anthropic           -> ONLY deepseek-v4-pro/flash
#                 (claude-code's full request 400s for any other id — not useful)
#   - zai      : https://open.bigmodel.cn/api/anthropic    -> glm-5.2 / glm-4.7 / glm-4.6
#   - moonshot : https://api.moonshot.cn/anthropic         -> kimi-k3
#   - aliyun   : <ALIYUN_BASE_URL>/apps/anthropic (custom) -> qwen3.5-flash / qwen-plus
#
# Usage:
#   ./benchmark/run_model_compare.sh
#   REWARD_MODE=dense MTK=2 AGENT_MODELS="deepseek/deepseek-v4-flash zai/glm-5.2" ./benchmark/run_model_compare.sh
#
# Env overrides: TASK, AGENT, PLUGIN, ENV_FILE, SANDBOX_TIMEOUT, REWARD_MODE,
# MTK, AGENT_MODELS, CACHE_DIR. Each run is ~30-60 min and billed; run the grid
# overnight. Keys are read from $ENV_FILE.
set -u

cd "$(dirname "$0")/.." || exit 1

TASK="${TASK:-tasks/benchmark/devteam}"
AGENT="${AGENT:-benchmark.interactive_agent:InteractiveUserClaude}"
PLUGIN="${PLUGIN:-benchmark.debug_long_sandbox_plugin:LongSandboxPlugin}"
ENV_FILE="${ENV_FILE:-.env}"
SANDBOX_TIMEOUT="${SANDBOX_TIMEOUT:-10800}"
# Reward mode passed to the verifier: "dense" (default, continuous per-round) or
# "binary" (existing 0/1). e.g. REWARD_MODE=binary to reproduce old behaviour.
REWARD_MODE="${REWARD_MODE:-dense}"
# MT@k: independent re-runs per model (EvoCode-Bench MT@4 adaptation).
MTK="${MTK:-2}"
CACHE_DIR="${CACHE_DIR:-/tmp/model-compare-cache}"
mkdir -p "$CACHE_DIR"

# backend-prefixed agent models (backend = key into BACKENDS below). Model ids
# must be the ANTHROPIC-endpoint ids (no "[1m]" suffix, no OpenAI-catalog prefix).
AGENT_MODELS="${AGENT_MODELS:-deepseek/deepseek-v4-flash zai/glm-5.2 moonshot/kimi-k3 aliyun/qwen3.5-flash}"

deepseek_key=$(sed -n 's/^ANTHROPIC_AUTH_TOKEN=//p' "$ENV_FILE" | tail -1)
novita_key=$(sed -n 's/^NOVITA_API_KEY=//p' "$ENV_FILE" | tail -1)
zai_key=$(sed -n 's/^ZAI_AUTH_TOKEN=//p' "$ENV_FILE" | tail -1)
moonshot_key=$(sed -n 's/^MOONSHOT_AUTH_TOKEN=//p' "$ENV_FILE" | tail -1)
aliyun_key=$(sed -n 's/^ALIYUN_AUTH_TOKEN=//p' "$ENV_FILE" | tail -1)
# user-LLM stays fixed at the .env value (only the agent model varies).
user_llm_model=$(sed -n 's/^USER_LLM_MODEL=//p' "$ENV_FILE" | tail -1)

# Base URLs: prefer the per-provider *_BASE_URL from .env, else the known default.
zai_base="${ZAI_BASE_URL:-$(sed -n 's/^ZAI_BASE_URL=//p' "$ENV_FILE" | tail -1)}"
moonshot_base="${MOONSHOT_BASE_URL:-$(sed -n 's/^MOONSHOT_BASE_URL=//p' "$ENV_FILE" | tail -1)}"
aliyun_base="${ALIYUN_BASE_URL:-$(sed -n 's/^ALIYUN_BASE_URL=//p' "$ENV_FILE" | tail -1)}"
zai_base="${zai_base:-https://open.bigmodel.cn/api/anthropic}"
moonshot_base="${moonshot_base:-https://api.moonshot.cn/anthropic}"
aliyun_base="${aliyun_base:-https://dashscope.aliyuncs.com/apps/anthropic}"

declare -A BACKENDS=(
  [deepseek]="https://api.deepseek.com/anthropic|$deepseek_key"
  [novita]="https://api.novita.ai/anthropic|$novita_key"
  [zai]="$zai_base|$zai_key"
  [moonshot]="$moonshot_base|$moonshot_key"
  [aliyun]="$aliyun_base|$aliyun_key"
)

# ------------------------------------------------------------------ extract

# extract <jobdir> <label>: print a JSON line with the attempt's metrics.
extract() {
  .venv/bin/python - "$1" "$2" <<'PY'
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
reward = r.get("reward")
rounds = [r.get(f"round_{i}") for i in range(1, 5)]

res = {}
rj = os.path.join(jobdir, "result.json")
if os.path.exists(rj):
    res = json.load(open(rj))
runtime = ""
if res.get("started_at") and res.get("finished_at"):
    runtime = str(datetime.fromisoformat(res["finished_at"]) - datetime.fromisoformat(res["started_at"]))

n_rounds = n_clar = n_corr = n_forced = div = None
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

print(json.dumps({
    "label": label, "reward": reward, "rounds": rounds,
    "n_rounds": n_rounds, "clar": n_clar, "corr": n_corr, "forced": n_forced,
    "div": div, "runtime": runtime,
}, ensure_ascii=False))
PY
}

# ----------------------------------------------------------------- aggregate

# aggregate <entry> <json-file...>: print one summary row from K attempt files.
aggregate() {
  .venv/bin/python - "$@" <<'PY'
import json
import sys

entry = sys.argv[1]
rows = []
for f in sys.argv[2:]:
    try:
        rows.append(json.load(open(f)))
    except Exception:
        pass
n = len(rows)


def mean(vals):
    nums = [v for v in vals if isinstance(v, (int, float))]
    return sum(nums) / len(nums) if nums else None


def fmt(x):
    if isinstance(x, float):
        return f"{x:.3f}"
    return "-" if x is None else x


rewards = [r["reward"] for r in rows if isinstance(r.get("reward"), (int, float))]
mtk = sum(1 for v in rewards if v is not None and v >= 0.999) / n if n else None
mean_rew = mean(rewards)
R = [
    mean([r["rounds"][i] for r in rows
          if isinstance(r.get("rounds"), list) and len(r["rounds"]) > i
          and isinstance(r["rounds"][i], (int, float))])
    for i in range(4)
]
ffr = None
for i, v in enumerate(R):
    if v is not None and v < 0.999:
        ffr = i + 1
        break
if ffr is None:
    ffr = "all" if R and all(v is not None and v >= 0.999 for v in R) else "-"
rstr = " ".join(f"R{i+1}={fmt(R[i])}" for i in range(4))
row = (
    f"{entry}|MT@{n}={fmt(mtk)}|mean={fmt(mean_rew)}|{rstr}|ffr={ffr}"
    f"|clar={fmt(mean([r['clar'] for r in rows if isinstance(r.get('clar'), int)]))}"
    f"|corr={fmt(mean([r['corr'] for r in rows if isinstance(r.get('corr'), int)]))}"
    f"|div={fmt(mean([r['div'] for r in rows if isinstance(r.get('div'), int)]))}"
    f"|runs={n}"
)
print(row)
PY
}

# ---------------------------------------------------------------- baselines

# local reference (oracle-like) and nop baselines, computed with the scorer.
baselines() {
  .venv/bin/python - <<'PY'
import json
import os
import shutil
import subprocess
import tempfile

SCORER = "tasks/benchmark/devteam/tests/scorer.py"
SCEN = "tasks/benchmark/devteam/environment/scenario.json"


def run_ws(ws):
    p = subprocess.run(
        [".venv/bin/python", SCORER, "--base-dir", ws, "--scenario", SCEN,
         "--reward-out", os.path.join(ws, "r.json")],
        capture_output=True, text=True, env=dict(os.environ),
    )
    rew = {}
    if os.path.exists(os.path.join(ws, "r.json")):
        rew = json.load(open(os.path.join(ws, "r.json")))
    return rew


def row(label, rew):
    r = [rew.get(f"round_{i}") for i in range(1, 5)]
    fr = "-" if all(v == 1.0 for v in r if v is not None) else "1"
    return f"{label}|MT@{1}={1.0 if rew.get('reward') == 1.0 else 0.0}|mean={rew.get('reward')}|" \
           f"{' '.join(f'R{i+1}={r[i]}' for i in range(4))}|ffr={fr}|clar=-|corr=-|div=-|runs=1"


# reference: apply solve.sh into a temp workspace, score it.
ws = tempfile.mkdtemp()
subprocess.run(
    ["bash", "-c", f"sed 's|/workspace|{ws}|g' tasks/benchmark/devteam/solution/solve.sh | bash"],
    capture_output=True, text=True,
)
print(row("reference", run_ws(ws)))
shutil.rmtree(ws, ignore_errors=True)

# nop: empty workspace, score it (every command fails -> all rounds 0).
ws = tempfile.mkdtemp()
print(row("nop", run_ws(ws)))
shutil.rmtree(ws, ignore_errors=True)
PY
}

# ------------------------------------------------------------------- main

ROWS=()
for entry in $AGENT_MODELS; do
  backend=${entry%%/*}
  model=${entry#*/}
  spec="${BACKENDS[$backend]:-}"
  if [ -z "$spec" ]; then
    echo "!! unknown backend '$backend' (expected deepseek|novita|zai|moonshot|aliyun); skipping $entry"
    continue
  fi
  base="${spec%%|*}"
  key="${spec#*|}"
  slug=$(printf '%s' "$entry" | tr '/:' '__')
  echo ">>> [$entry] MTK=$MTK reward=$REWARD_MODE (user-LLM fixed = $user_llm_model) ..."
  attempt_files=()
  for a in $(seq 1 "$MTK"); do
    cache="$CACHE_DIR/${slug}__a${a}.json"
    if [ -f "$cache" ]; then
      echo "    [attempt $a/$MTK] cached"
    else
      log="/tmp/model-compare__${slug}__a${a}.log"
      # Harbor loads --env-file with override=True, so a shell-level
      # ANTHROPIC_BASE_URL is clobbered by .env's. Build a per-backend env file.
      runenv=$(mktemp)
      grep -v '^ANTHROPIC_' "$ENV_FILE" > "$runenv" || true
      printf 'ANTHROPIC_BASE_URL=%s\nANTHROPIC_AUTH_TOKEN=%s\n' "$base" "$key" >> "$runenv"
      PYTHONPATH="$PWD" NOVITA_SANDBOX_TIMEOUT="$SANDBOX_TIMEOUT" \
        .venv/bin/harbor run -e novita --env-file "$runenv" -p "$TASK" -a "$AGENT" -m "$model" \
          --plugin "$PLUGIN" --ve "REWARD_MODE=$REWARD_MODE" > "$log" 2>&1
      rm -f "$runenv"
      jobdir=$(grep -o 'Results written to [^ ]*' "$log" | head -1 | sed 's#^Results written to ##; s#/result.json$##')
      if [ -z "$jobdir" ]; then
        echo "    [attempt $a/$MTK] !! no job dir — run failed? see $log"
        echo '{"label":"'"$entry"'","reward":null,"rounds":[null,null,null,null],"n_rounds":null,"clar":null,"corr":null,"forced":null,"div":null,"runtime":""}' > "$cache"
      else
        extract "$jobdir" "$entry" > "$cache"
      fi
      reward_hint=$(.venv/bin/python -c "import json;d=json.load(open('$cache'));print('reward=%s rounds=%s'%(d.get('reward'), d.get('rounds')))" 2>/dev/null || echo "?")
      echo "    [attempt $a/$MTK] done -> $reward_hint"
    fi
    attempt_files+=("$cache")
  done
  row=$(aggregate "$entry" "${attempt_files[@]}")
  ROWS+=("$row")
  echo "    $row"
done

echo
echo ">>> local baselines (reference = oracle-like, nop = empty workspace) ..."
BASELINES=$(baselines)

echo
echo "=== SUMMARY ==="
echo "columns: agent_model | MT@k | mean_dense_reward | per-round_means | first_failed_round | clar/corr/div_means | runs"
printf '%s\n' "${ROWS[@]}"
echo "$BASELINES"
