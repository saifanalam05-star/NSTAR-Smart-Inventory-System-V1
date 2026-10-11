"""Preview or safely clean categories of already-imported WooCommerce products.

Only updates rows whose current category still equals the original CSV value.
No other product fields, inventory counts or transactions are touched.
"""
import argparse
import csv
import sqlite3
from pathlib import Path
from datetime import datetime

from backend.category_rules import clean_category

DEFAULT_DB = Path(__file__).resolve().parent / 'nstar.db'


def run(csv_path: Path, db_path: Path, commit: bool = False):
    if not csv_path.is_file() or not db_path.is_file():
        raise FileNotFoundError('CSV or database file does not exist')
    with csv_path.open(encoding='utf-8-sig', newline='') as f:
        rows = list(csv.DictReader(f))
    parent_id = {r['ID'].strip(): r for r in rows if r['Type'].strip().lower() == 'variable'}
    parent_sku = {r['SKU'].strip().upper(): r for r in parent_id.values() if r['SKU'].strip()}
    proposals = {}
    conflicts = set()
    for row in rows:
        sku = row['SKU'].strip().upper()
        if not sku or row['Type'].strip().lower() not in ('variable', 'variation'):
            continue
        parent = None
        if row['Type'].strip().lower() == 'variation':
            key = row['Parent'].strip()
            parent = parent_id.get(key.removeprefix('id:')) or parent_sku.get(key.upper())
            if parent is None:
                continue
        raw = row['Categories'].strip() or (parent['Categories'].strip() if parent else '')
        cleaned = clean_category(raw)
        if sku in proposals:
            conflicts.add(sku)
        else:
            proposals[sku] = (raw, cleaned)
    with sqlite3.connect(db_path) as conn:
        current = conn.execute('SELECT sku, category FROM products').fetchall()
    updates = []
    skipped_ambiguous = 0
    skipped_modified = 0
    for sku, old in current:
        key = sku.strip().upper()
        if key in conflicts or key not in proposals:
            continue
        raw, new = proposals[key]
        if not new:
            skipped_ambiguous += 1
        elif (old or '') != raw:
            # Respect any manual category edits or prior cleanup.
            skipped_modified += 1
        elif old != new:
            updates.append((new, sku, old))
    print('CATEGORY CLEANUP —', 'COMMIT' if commit else 'DRY RUN (database unchanged)')
    print('Proposed changes:', len(updates))
    print('Ambiguous categories skipped:', skipped_ambiguous)
    print('Previously edited categories skipped:', skipped_modified)
    for new, sku, old in updates[:15]:
        print(f'  {sku}: {old!r} -> {new!r}')
    if len(updates) > 15:
        print(f'  ... plus {len(updates) - 15} more')
    if not commit or not updates:
        return
    backup = db_path.with_name(db_path.stem + '_backup_categories_' + datetime.now().strftime('%Y%m%d_%H%M%S_%f') + db_path.suffix)
    with sqlite3.connect(db_path) as source, sqlite3.connect(backup) as target:
        source.backup(target)
    print('Backup created:', backup)
    with sqlite3.connect(db_path) as conn:
        with conn:
            changed = 0
            for new, sku, old in updates:
                cur = conn.execute('UPDATE products SET category = ? WHERE sku = ? AND category = ?', (new, sku, old))
                changed += cur.rowcount
    print('Categories updated:', changed)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='Preview or clean NSTAR WooCommerce categories')
    parser.add_argument('csv_file', type=Path)
    parser.add_argument('--db', type=Path, default=DEFAULT_DB)
    parser.add_argument('--commit', action='store_true')
    args = parser.parse_args()
    try:
        run(args.csv_file, args.db, args.commit)
    except (OSError, ValueError, sqlite3.Error) as exc:
        parser.error(str(exc))
