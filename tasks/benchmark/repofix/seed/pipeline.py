"""sales_pipeline: per-category sales totals from a CSV.

Seeded with two bugs the agent must fix:
  - BUG 1: grouping uses the date column (index 0) instead of category (index 1).
  - BUG 2: a blank ``amount`` raises ValueError and crashes the process.
"""
import csv
import sys


def main(argv=None):
    argv = sys.argv if argv is None else argv
    if len(argv) < 2:
        print("usage: python3 pipeline.py <input.csv>")
        return 1
    path = argv[1]
    totals = {}
    with open(path, newline="") as f:
        reader = csv.reader(f)
        next(reader)  # skip header
        for row in reader:
            category = row[0]       # BUG 1: should be row[1]
            amount = float(row[2])  # BUG 2: crashes on a blank amount
            totals[category] = totals.get(category, 0.0) + amount
    for category in sorted(totals):
        print(f"{category}: {totals[category]:.2f}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
