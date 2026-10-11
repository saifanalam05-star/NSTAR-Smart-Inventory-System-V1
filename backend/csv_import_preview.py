"""Read-only validation of a WooCommerce product export for NSTAR.

No inserts, updates, or deletes are performed by this module.
"""

import csv
import io
from collections import Counter


def preview_csv(csv_bytes: bytes, existing_skus=(), issue_limit: int = 50) -> dict:
    """Validate WooCommerce rows and return a compact import preview."""
    try:
        content = csv_bytes.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise ValueError("CSV must use UTF-8 encoding.") from exc

    reader = csv.DictReader(io.StringIO(content, newline=""))
    required = {"ID", "Type", "SKU", "Name", "Parent", "Categories"}
    if not reader.fieldnames or not required.issubset(reader.fieldnames):
        raise ValueError("Not a supported WooCommerce export. Missing: " +
                         ", ".join(sorted(required - set(reader.fieldnames or []))))

    rows = list(reader)
    parents_by_id = {r["ID"].strip(): r for r in rows if r["Type"].strip().lower() == "variable"}
    parents_by_sku = {r["SKU"].strip().upper(): r for r in parents_by_id.values() if r["SKU"].strip()}
    existing = {sku.strip().upper() for sku in existing_skus}
    counts = Counter()
    issues = []
    valid_examples = []
    seen = set()

    def attributes(row):
        return {row.get(f"Attribute {n} name", "").strip().lower():
                row.get(f"Attribute {n} value(s)", "").strip()
                for n in (1, 2, 3) if row.get(f"Attribute {n} name", "").strip()}

    for line_number, row in enumerate(rows, start=2):
        kind = row["Type"].strip().lower()
        counts["total_rows"] += 1
        counts[f"type_{kind}"] += 1
        sku = row["SKU"].strip().upper()
        name = row["Name"].strip()
        parent = None
        if kind == "variation":
            parent_key = row["Parent"].strip()
            parent = (parents_by_id.get(parent_key.removeprefix("id:"))
                      or parents_by_sku.get(parent_key.upper()))
            if parent is None:
                counts["unresolved_parent"] += 1
        elif kind != "variable":
            counts["unsupported_type"] += 1
            if len(issues) < issue_limit:
                issues.append({"row": line_number, "sku": sku, "reason": "Unsupported product type"})
            continue

        if not sku:
            counts["missing_sku"] += 1
            if len(issues) < issue_limit:
                issues.append({"row": line_number, "sku": "", "reason": "Missing SKU"})
            continue
        if not name:
            counts["missing_name"] += 1
            if len(issues) < issue_limit:
                issues.append({"row": line_number, "sku": sku, "reason": "Missing name"})
            continue
        if sku in seen:
            counts["duplicate_sku_in_csv"] += 1
            if len(issues) < issue_limit:
                issues.append({"row": line_number, "sku": sku, "reason": "Duplicate SKU in CSV"})
            continue
        seen.add(sku)
        if sku in existing:
            counts["already_in_database"] += 1
            if len(issues) < issue_limit:
                issues.append({"row": line_number, "sku": sku, "reason": "Already in NSTAR database"})
            continue

        own_attrs = attributes(row)
        parent_attrs = attributes(parent) if parent else {}
        brand = own_attrs.get("brand") or parent_attrs.get("brand") or None
        pack_size = own_attrs.get("pack size") or parent_attrs.get("pack size") or None
        category = row["Categories"].strip() or (parent["Categories"].strip() if parent else "")
        counts["eligible_for_import"] += 1
        if len(valid_examples) < 20:
            valid_examples.append({"sku": sku, "product_name": name,
                                   "category": category or None, "brand": brand,
                                   "pack_size": pack_size, "quantity": 0,
                                   "minimum_stock": 0, "type": kind})

    return {"preview_only": True, "counts": dict(counts),
            "sample_products": valid_examples, "issues": issues,
            "issues_truncated": sum(counts[k] for k in (
                "missing_sku", "missing_name", "duplicate_sku_in_csv",
                "already_in_database", "unsupported_type")) > len(issues),
            "notes": ["No database changes were made.",
                      "Both parent products and variations are previewed when they have distinct SKUs.",
                      "Parent/variation stock relationships require review before enabling import.",
                      "All preview quantities default to zero; CSV prices are ignored."]}

if __name__ == "__main__":
    import argparse
    import json
    from pathlib import Path
    import sqlite3

    parser = argparse.ArgumentParser(description="Preview WooCommerce CSV without modifying NSTAR")
    parser.add_argument("csv_file", type=Path, help="Path to WooCommerce CSV export")
    parser.add_argument("--db", type=Path, default=Path(__file__).resolve().parent / "nstar.db")
    parser.add_argument("--output", type=Path, default=Path("import_preview.json"))
    args = parser.parse_args()

    if not args.csv_file.is_file():
        parser.error(f"CSV file not found: {args.csv_file}")
    if not args.db.is_file():
        parser.error(f"NSTAR database not found: {args.db}")

    with sqlite3.connect(f"file:{args.db.resolve().as_posix()}?mode=ro", uri=True) as db:
        current = [row[0] for row in db.execute("SELECT sku FROM products")]
    result = preview_csv(args.csv_file.read_bytes(), current)
    args.output.write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8")
    print("NSTAR CSV PREVIEW (NO DATABASE CHANGES)")
    for key, value in result["counts"].items():
        print(f"  {key}: {value}")
    print(f"Full preview saved to: {args.output.resolve()}")
