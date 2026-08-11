FROM ubuntu:24.04

# Task runtime only — claude-code itself is installed by the agent at setup.
# pytest is needed both for the repo's visible tests and the verifier.
RUN apt-get update \
    && apt-get install -y --no-install-recommends python3 python3-pytest \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /workspace
# Bake the seeded (broken) sales-pipeline repo into the workspace.
COPY seed/ /workspace/
COPY scenario.json /workspace/scenario.json
