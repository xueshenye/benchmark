FROM ubuntu:24.04

# Task runtime only — claude-code itself is installed by the agent at setup.
# pip + pytest are needed for the package build/install and the task's tests.
RUN apt-get update \
    && apt-get install -y --no-install-recommends python3 python3-pip python3-pytest \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /workspace
COPY scenario.json /workspace/scenario.json
