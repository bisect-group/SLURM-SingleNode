#!/usr/bin/env python3
"""
validate_csv.py — sanity check a users.csv without touching the system.

Usage:
    validate_csv.py [--csv users.csv]

Exit code 0 = clean, 2 = validation errors.
"""
import argparse
import sys
import os

# Make sync_users.py importable from the same dir
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from sync_users import CSVFile, validate_rows  # type: ignore


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--csv", default="/etc/tesla-cluster/users.csv")
    args = ap.parse_args()

    with CSVFile(args.csv) as cf:
        rows = cf.read()

    errs = validate_rows(rows)
    if errs:
        print(f"{len(errs)} validation error(s):", file=sys.stderr)
        for e in errs:
            print(f"  {e}", file=sys.stderr)
        return 2

    print(f"OK — {len(rows)} row(s), no errors.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
