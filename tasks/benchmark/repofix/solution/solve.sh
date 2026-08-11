#!/bin/bash
# Reference solution for all three rounds: fix the seeded bugs, harden edge
# cases, then refactor into small functions + add a regression test file.
set -euo pipefail

mkdir -p /workspace/tests /workspace/data

cat > /workspace/pipeline.py <<'PY'
"""sales_pipeline: per-category sales totals from a CSV."""
import csv
import sys


def parse_rows(path):
    """Yield (category, amount) for each valid row; skip blank/non-numeric amounts."""
    with open(path, newline="") as f:
        reader = csv.reader(f)
        if next(reader, None) is None:  # empty file (no header either)
            return
        for row in reader:
            if not row or len(row) < 3:
                continue
            category = row[1].strip()
            amount = row[2].strip()
            if not amount:
                continue
            try:
                value = float(amount)
            except ValueError:
                continue
            yield category, value


def aggregate(rows):
    """Sum amounts per category."""
    totals = {}
    for category, amount in rows:
        totals[category] = totals.get(category, 0.0) + amount
    return totals


def main(argv=None):
    argv = sys.argv if argv is None else argv
    if len(argv) < 2:
        print("usage: python3 pipeline.py <input.csv>")
        return 1
    path = argv[1]
    totals = aggregate(parse_rows(path))
    for category in sorted(totals):
        print(f"{category}: {totals[category]:.2f}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
PY

cat > /workspace/tests/test_pipeline.py <<'PY'
"""Visible tests for the sales pipeline."""
import os
import subprocess
import sys
import tempfile

PIPE = [
    sys.executable,
    os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "pipeline.py"
    ),
]


def run_pipe(csv_text):
    with tempfile.TemporaryDirectory() as td:
        p = os.path.join(td, "in.csv")
        with open(p, "w") as f:
            f.write(csv_text)
        return subprocess.run(PIPE + [p], capture_output=True, text=True)


def test_grouping_by_category():
    r = run_pipe(
        "date,category,amount\n"
        "2024-01-01,electronics,10.5\n"
        "2024-01-02,books,5.0\n"
        "2024-01-03,electronics,15.0\n"
    )
    assert r.returncode == 0
    assert "electronics: 25.50" in r.stdout
    assert "books: 5.00" in r.stdout
    assert "2024-01-01" not in r.stdout  # grouped by category, not date


def test_blank_amount_is_skipped():
    r = run_pipe(
        "date,category,amount\n"
        "2024-01-01,electronics,\n"
        "2024-01-02,books,5.0\n"
    )
    assert r.returncode == 0
    assert "books: 5.00" in r.stdout
    assert "electronics" not in r.stdout  # blank-amount row is skipped
PY

cat > /workspace/tests/test_regression.py <<'PY'
"""Regression tests: lock in the two fixed bugs from the sales pipeline."""
import os
import subprocess
import sys
import tempfile

PIPE = [
    sys.executable,
    os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "pipeline.py"
    ),
]


def run_pipe(csv_text):
    with tempfile.TemporaryDirectory() as td:
        p = os.path.join(td, "in.csv")
        with open(p, "w") as f:
            f.write(csv_text)
        return subprocess.run(PIPE + [p], capture_output=True, text=True)


def test_regression_grouped_by_category():
    """Bug 1 regression: totals are per category, never per date."""
    r = run_pipe(
        "date,category,amount\n"
        "2024-01-01,electronics,10.5\n"
        "2024-01-02,books,5.0\n"
        "2024-01-03,electronics,15.0\n"
    )
    assert r.returncode == 0
    assert "electronics: 25.50" in r.stdout
    assert "books: 5.00" in r.stdout
    assert "2024-01-01" not in r.stdout


def test_regression_blank_amount_skipped():
    """Bug 2 regression: a blank amount never crashes the pipeline."""
    r = run_pipe(
        "date,category,amount\n"
        "2024-01-01,electronics,\n"
        "2024-01-02,books,5.0\n"
    )
    assert r.returncode == 0
    assert "books: 5.00" in r.stdout
PY

cat > /workspace/data/sample.csv <<'CSV'
date,category,amount
2024-01-01,electronics,10.5
2024-01-02,books,5.0
2024-01-03,electronics,15.0
2024-01-04,books,5.0
CSV
