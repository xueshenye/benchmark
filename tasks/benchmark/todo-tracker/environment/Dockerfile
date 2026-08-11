FROM ubuntu:24.04

# Task runtime only — claude-code itself is installed by the agent at setup.
RUN apt-get update \
    && apt-get install -y --no-install-recommends python3 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /workspace
COPY scenario.json /workspace/scenario.json
