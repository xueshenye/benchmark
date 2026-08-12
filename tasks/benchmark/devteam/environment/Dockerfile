FROM ubuntu:24.04

# Task runtime only — claude-code itself is installed by the agent at setup.
RUN apt-get update \
    && apt-get install -y --no-install-recommends python3 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /workspace
# scenario.json is the benchmark's ground truth (harness + verifier read it);
# keep it OUT of /workspace so the agent can't peek at future milestones.
COPY scenario.json /scenario.json
