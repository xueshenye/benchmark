# Sales pipeline

Computes **per-category** sales totals from a CSV with columns `date,category,amount`.

Usage:

```
python3 pipeline.py <input.csv>
```

Output (one line per category, sorted by name):

```
electronics: 25.50
books: 10.00
```

Expected behavior:

- The header line is skipped.
- Amounts are summed per `category` (the second column), not per date.
- Rows with a blank or non-numeric `amount` are skipped — never a crash.
- Empty input (no data rows) prints nothing and exits 0.

Tests live in `tests/`; run them with `python3 -m pytest`.
