#!/bin/bash
# Verifier entry point. Runs the scorer, which writes the per-round and final
# rewards to /logs/verifier/reward.json (flat dict str→number).
set -u
python3 /tests/scorer.py || {
  # Fallback so the trial always has a reward file.
  echo '{"reward": 0.0}' > /logs/verifier/reward.json
  exit 0
}
