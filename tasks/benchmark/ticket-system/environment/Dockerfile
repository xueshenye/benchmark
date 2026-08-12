FROM ubuntu:24.04

# Task runtime only — claude-code itself is installed by the agent at setup.
# python3 + pytest: the HTTP service runtime + the M3 refactor/test check.
# sqlite3 is part of the Python standard library (no extra package needed).
RUN apt-get update \
    && apt-get install -y --no-install-recommends python3 python3-pytest \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /workspace
# User-visible materials: business docs + the HTTP contract + sample export.
COPY seed/ /workspace/
COPY scenario.json /workspace/scenario.json
# Verifier-only ground truth (independent copy of docs + hidden facts).
COPY ground_truth/ /var/ground_truth/
RUN chmod -R a-w /var/ground_truth
