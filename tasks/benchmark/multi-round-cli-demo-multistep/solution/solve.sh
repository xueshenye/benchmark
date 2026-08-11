#!/bin/bash
# Reference solution implementing all three rounds of the scenario.
set -euo pipefail

mkdir -p /workspace/src
cat > /workspace/src/stats.py <<'PY'
#!/usr/bin/env python3
"""stats — summary statistics for CSV files (single or multiple)."""
import argparse
import csv
import json
import sys


def summarize(path):
    with open(path, newline="") as f:
        rows = list(csv.reader(f))
    data = rows[1:] if rows else []
    values = []
    for row in data:
        if row:
            try:
                values.append(float(row[0]))
            except (ValueError, IndexError):
                pass
    if not values:
        return {"file": path, "count": 0, "mean": 0.0, "min": 0.0, "max": 0.0}
    return {
        "file": path,
        "count": len(values),
        "mean": sum(values) / len(values),
        "min": min(values),
        "max": max(values),
    }


def fmt(s):
    return f"{s['file']}: count={s['count']} mean={s['mean']:.2f} min={s['min']:.2f} max={s['max']:.2f}"


def main(argv=None):
    parser = argparse.ArgumentParser(description="stats CLI")
    parser.add_argument("--output-json", action="store_true", help="emit JSON")
    parser.add_argument("files", nargs="+", help="input CSV files")
    args = parser.parse_args(argv)

    results = []
    for path in args.files:
        try:
            results.append(summarize(path))
        except OSError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 1

    if args.output_json:
        print(json.dumps(results))
    else:
        for s in results:
            print(fmt(s))
    return 0


if __name__ == "__main__":
    sys.exit(main())
PY

chmod +x /workspace/src/stats.py
ln -sf /workspace/src/stats.py /workspace/stats
