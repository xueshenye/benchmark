"""Visible tests for the sales pipeline (fail against the seeded bugs)."""
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
