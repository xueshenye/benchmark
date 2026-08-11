#!/bin/bash
# Reference solution implementing all three rounds of the scenario.
set -euo pipefail

mkdir -p /workspace/wordcount /workspace/tests

cat > /workspace/pyproject.toml <<'TOML'
[build-system]
requires = ["setuptools>=61"]
build-backend = "setuptools.build_meta"

[project]
name = "wordcount"
version = "0.1.0"
description = "word frequency counter"
requires-python = ">=3.8"

[project.scripts]
wordcount = "wordcount.cli:main"

[tool.setuptools]
packages = ["wordcount"]
TOML

cat > /workspace/wordcount/core.py <<'PY'
"""Core word-counting logic."""
import re

_TOKEN_RE = re.compile(r"[a-zA-Z0-9]+")


def count(text):
    """Return {word: occurrences}, case-insensitive, ignoring punctuation."""
    out = {}
    for token in _TOKEN_RE.findall(text.lower()):
        out[token] = out.get(token, 0) + 1
    return out


def top_words(text, n):
    """Return the n most frequent words; ties broken lexicographically."""
    c = count(text)
    return sorted(c, key=lambda w: (-c[w], w))[:n]
PY

cat > /workspace/wordcount/__init__.py <<'PY'
"""wordcount: word frequency statistics for text."""
from .core import count, top_words

__all__ = ["count", "top_words"]
PY

cat > /workspace/wordcount/cli.py <<'PY'
"""Command-line entry point: wordcount <file>."""
import argparse
import sys

from .core import count


def main(argv=None):
    parser = argparse.ArgumentParser(prog="wordcount", description="word frequency counter")
    parser.add_argument("file", help="text file to count")
    args = parser.parse_args(argv)
    with open(args.file, encoding="utf-8") as f:
        text = f.read()
    c = count(text)
    for word in sorted(c, key=lambda w: (-c[w], w)):
        print(f"{word}: {c[word]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
PY

cat > /workspace/wordcount/__main__.py <<'PY'
"""Allow `python -m wordcount <file>` as well."""
import sys

from .cli import main

if __name__ == "__main__":
    sys.exit(main())
PY

cat > /workspace/tests/test_wordcount.py <<'PY'
"""Tests for the wordcount package."""
from wordcount import count, top_words


def test_count_case_and_punctuation():
    assert count("Hello, World! hello world") == {"hello": 2, "world": 2}


def test_count_empty():
    assert count("") == {}


def test_top_words_ordering_and_tie_break():
    assert top_words("a a a b b c", 2) == ["a", "b"]
    assert top_words("x x y y z", 2) == ["x", "y"]
PY
