#!/bin/bash
# Verifier entry point.
#
# Harbor 0.20.0 reads its reward from /logs/verifier/reward.json (flat dict
# str->number) or reward.txt (scalar); it does NOT read rewards.txt. To satisfy
# BOTH the benchmark spec (req.txt: "test.sh 将最终得分写入
# /logs/verifier/rewards.txt") and Harbor's scoring, we write:
#   - /logs/verifier/reward.json   (what Harbor actually reads)
#   - /logs/verifier/rewards.txt   (spec-required output; same content)
set -u
python3 /tests/scorer.py || {
  # Fallback so the trial always has a reward file.
  echo '{"reward": 0.0}' > /logs/verifier/reward.json
  cp /logs/verifier/reward.json /logs/verifier/rewards.txt
  exit 0
}
# Spec requirement: also write the final score to /logs/verifier/rewards.txt.
cp /logs/verifier/reward.json /logs/verifier/rewards.txt
