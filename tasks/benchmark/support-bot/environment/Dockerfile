FROM ubuntu:24.04

# Task runtime only — claude-code itself is installed by the agent at setup.
# python3 + pytest: bot runtime + the M3 batch/test check; mock API is stdlib-only.
RUN apt-get update \
    && apt-get install -y --no-install-recommends python3 python3-pytest \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /workspace
# User-visible materials: docs, knowledge base, dev mock API.
COPY seed/ /workspace/
COPY scenario.json /workspace/scenario.json
# Verifier-only ground truth (independent copy of KB + api spec + mock data + facts).
COPY ground_truth/ /var/ground_truth/
RUN chmod -R a-w /var/ground_truth
