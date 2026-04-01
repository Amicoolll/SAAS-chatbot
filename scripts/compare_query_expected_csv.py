#!/usr/bin/env python3
"""Compare analyze_query() to expected columns in a CSV or Excel (.xlsx) file."""

from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path
from typing import Any, Iterable

_root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_root))

from app.core.config import Settings
from app.services.query_understanding import analyze_query

KEYS = (
    "domain",
    "intent",
    "complexity",
    "risk_level",
    "needs_exact_match",
    "needs_multi_hop",
    "needs_live_data",
    "requires_citations",
)


def _settings() -> Settings:
    return Settings.model_construct(
        DATABASE_URL="postgresql://localhost/test",
        QUERY_UNDERSTANDING_DOMAINS=[
            "medical",
            "logistics",
            "support",
            "hr",
            "finance",
            "legal",
            "general",
            "multi_domain",
        ],
        QUERY_UNDERSTANDING_INTENTS=[
            "faq",
            "search",
            "summarization",
            "troubleshooting",
            "status_lookup",
            "policy_lookup",
            "comparison",
            "analysis",
            "workflow_help",
        ],
    )


def _parse_bool(raw: str, *, row_no: int, col: str) -> bool:
    s = raw.strip().lower()
    if s in ("true", "1", "yes", "y"):
        return True
    if s in ("false", "0", "no", "n"):
        return False
    raise ValueError(f"Row {row_no}: column {col!r} must be a boolean (got {raw!r})")


def _coerce_bool(val: Any, *, row_no: int, col: str) -> bool:
    if isinstance(val, bool):
        return val
    if val is None or (isinstance(val, str) and not val.strip()):
        raise ValueError(f"Row {row_no}: empty value for {col!r}")
    if isinstance(val, (int, float)) and not isinstance(val, bool):
        if val == 1:
            return True
        if val == 0:
            return False
    return _parse_bool(str(val), row_no=row_no, col=col)


def _expect_row(row: dict[str, Any], row_no: int) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for k in KEYS:
        if k not in row:
            raise KeyError(f"Row {row_no}: missing column {k!r}")
        val = row[k]
        if k.startswith("needs_") or k == "requires_citations":
            out[k] = _coerce_bool(val, row_no=row_no, col=k)
        else:
            out[k] = "" if val is None else str(val).strip()
    return out


def _header_check(header_set: set[str]) -> list[str]:
    if "query" not in header_set:
        return ["<missing: query>"]
    return [k for k in KEYS if k not in header_set]


def _iter_csv_rows(path: Path) -> Iterable[tuple[int, dict[str, Any]]]:
    with path.open(newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        if reader.fieldnames is None:
            raise ValueError("CSV has no header row.")
        header_set = {(h or "").strip() for h in reader.fieldnames}
        missing = _header_check(header_set)
        if missing:
            raise ValueError(f"CSV header missing columns: {missing}")

        for row_no, raw_row in enumerate(reader, start=2):
            row = {
                (k or "").strip(): v
                for k, v in raw_row.items()
                if (k or "").strip()
            }
            if not any(
                v is not None and (not isinstance(v, str) or v.strip())
                for v in row.values()
            ):
                continue
            q = row.get("query")
            if q is None or (isinstance(q, str) and not q.strip()):
                continue
            yield row_no, row


def _iter_xlsx_rows(path: Path) -> Iterable[tuple[int, dict[str, Any]]]:
    import openpyxl

    wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
    try:
        ws = wb.active
        it = ws.iter_rows(values_only=True)
        header_raw = next(it)
        header = [
            (str(h).strip() if h is not None else "") for h in header_raw
        ]
        header_set = {h for h in header if h}
        missing = _header_check(header_set)
        if missing:
            raise ValueError(f"Worksheet header missing columns: {missing}")

        for row_no, values in enumerate(it, start=2):
            if not values:
                continue
            row = {
                header[i]: values[i] if i < len(values) else None
                for i in range(len(header))
                if header[i]
            }
            if not any(
                v is not None and (not isinstance(v, str) or str(v).strip())
                for v in row.values()
            ):
                continue
            yield row_no, row
    finally:
        wb.close()


def main() -> int:
    p = argparse.ArgumentParser(
        description="Diff query understanding vs tabular expected outputs (CSV or .xlsx).",
    )
    p.add_argument(
        "data_path",
        type=Path,
        help="Path to .csv or .xlsx (columns: query + " + ", ".join(KEYS) + "; extra columns OK).",
    )
    p.add_argument(
        "--failures-csv",
        type=Path,
        default=None,
        help="Write failing rows (expected vs actual) to this CSV.",
    )
    p.add_argument(
        "--verbose",
        action="store_true",
        help="Print each row (default: only failures + summary).",
    )
    p.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Process at most this many non-empty rows (smoke test).",
    )
    args = p.parse_args()

    path: Path = args.data_path
    if not path.is_file():
        print(f"Not a file: {path}", file=sys.stderr)
        return 2

    suffix = path.suffix.lower()
    if suffix == ".csv":
        row_iter_factory = lambda: _iter_csv_rows(path)
    elif suffix in (".xlsx", ".xlsm"):
        row_iter_factory = lambda: _iter_xlsx_rows(path)
    else:
        print("Use a .csv or .xlsx file (or export Excel to CSV).", file=sys.stderr)
        return 2

    settings = _settings()
    failed = 0
    total = 0
    failure_records: list[dict[str, object]] = []

    try:
        row_iter = row_iter_factory()
    except ValueError as e:
        print(e, file=sys.stderr)
        return 2

    for row_no, row in row_iter:
        q_raw = row.get("query")
        q = "" if q_raw is None else str(q_raw).strip()
        if not q:
            continue
        if args.limit is not None and total >= args.limit:
            break
        total += 1
        try:
            exp = _expect_row(row, row_no)
        except (KeyError, ValueError) as e:
            print(f"Row {row_no}: {e}", file=sys.stderr)
            return 2

        got = analyze_query(q, settings=settings).model_dump()
        mismatches: list[str] = []
        for k in KEYS:
            if got[k] != exp[k]:
                mismatches.append(f"  {k}: expected {exp[k]!r} got {got[k]!r}")

        if args.verbose or mismatches:
            print(f"\n--- {total}. row {row_no} {q!r} ---")
        if mismatches:
            print("FAIL:")
            print("\n".join(mismatches))
            failed += 1
            rec: dict[str, object] = {
                "row": row_no,
                "query": q,
                "mismatches": "; ".join(m.replace("  ", "") for m in mismatches),
            }
            for k in KEYS:
                rec[f"expected_{k}"] = exp[k]
                rec[f"actual_{k}"] = got[k]
            failure_records.append(rec)
        elif args.verbose:
            print("OK")
            print(json.dumps({k: got[k] for k in KEYS}, indent=2))

    print(f"\nTotal rows tested: {total}; failed: {failed}")

    if args.failures_csv and failure_records:
        out_path: Path = args.failures_csv
        fieldnames = list(failure_records[0].keys())
        with out_path.open("w", newline="", encoding="utf-8") as outf:
            w = csv.DictWriter(outf, fieldnames=fieldnames)
            w.writeheader()
            for rec in failure_records:
                w.writerow(rec)
        print(f"Wrote {len(failure_records)} failures to {out_path}")

    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
