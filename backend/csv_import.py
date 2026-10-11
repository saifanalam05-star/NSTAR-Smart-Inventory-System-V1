"""Safely import WooCommerce products into NSTAR SQLite.

Default mode is read-only. Use --commit --limit 10 for a small trial import.
Existing products, stock counts and transactions are never updated.
"""
import argparse
import csv
import io
import shutil
import sqlite3
from collections import Counter
from datetime import datetime
from pathlib import Path

from backend.category_rules import clean_category

DEFAULT_DB = Path(__file__).resolve().parent / 'nstar.db'


def load_candidates(data: bytes):
    try:
        reader = csv.DictReader(io.StringIO(data.decode('utf-8-sig'), newline=''))
    except UnicodeDecodeError as exc:
        raise ValueError('CSV must use UTF-8 encoding') from exc
    required = {'ID', 'Type', 'SKU', 'Name', 'Parent', 'Categories'}
    if not reader.fieldnames or not required.issubset(reader.fieldnames):
        raise ValueError('Missing WooCommerce columns: ' + ', '.join(sorted(required - set(reader.fieldnames or []))))
    rows = list(reader)
    parents_id = {r['ID'].strip(): r for r in rows if r['Type'].strip().lower() == 'variable'}
    parents_sku = {r['SKU'].strip().upper(): r for r in parents_id.values() if r['SKU'].strip()}
    def attrs(row):
        if not row:
            return {}
        return {row.get(f'Attribute {n} name', '').strip().lower(): row.get(f'Attribute {n} value(s)', '').strip()
                for n in range(1, 4) if row.get(f'Attribute {n} name', '').strip()}
    seen = set()
    candidates = []
    counts = Counter()
    for row in rows:
        counts['total_rows'] += 1
        kind = row['Type'].strip().lower()
        if kind not in ('variable', 'variation'):
            counts['unsupported_type'] += 1
            continue
        sku = row['SKU'].strip().upper()
        name = row['Name'].strip()
        if not sku:
            counts['missing_sku'] += 1
            continue
        if not name:
            counts['missing_name'] += 1
            continue
        if sku in seen:
            counts['duplicate_sku_in_csv'] += 1
            continue
        seen.add(sku)
        parent = None
        if kind == 'variation':
            key = row['Parent'].strip()
            parent = parents_id.get(key.removeprefix('id:')) or parents_sku.get(key.upper())
            if parent is None:
                counts['unresolved_parent'] += 1
                # Do not import variations without a resolved parent: category and attributes may be incomplete.
                continue
        own, inherited = attrs(row), attrs(parent)
        raw_category = row['Categories'].strip() or (parent['Categories'].strip() if parent else '')
        category = clean_category(raw_category)
        candidates.append((sku, name, category,
                           own.get('brand') or inherited.get('brand') or None,
                           own.get('pack size') or inherited.get('pack size') or None))
    counts['valid_candidates'] = len(candidates)
    return candidates, counts


def run(csv_path: Path, db_path: Path, limit: int, commit: bool):
    if limit < 1:
        raise ValueError('--limit must be at least 1')
    if not csv_path.is_file() or not db_path.is_file():
        raise FileNotFoundError('CSV or database file does not exist')
    candidates, counts = load_candidates(csv_path.read_bytes())
    # Open in read-only mode until the user explicitly requests a commit.
    if not commit:
        with sqlite3.connect(f'file:{db_path.resolve().as_posix()}?mode=ro', uri=True) as conn:
            existing = {r[0].strip().upper() for r in conn.execute('SELECT sku FROM products')}
        pending = [c for c in candidates if c[0] not in existing]
        print('DRY RUN — database unchanged')
        print('CSV counts:', dict(counts))
        print('Already in database:', len(candidates) - len(pending))
        print('Would import:', min(limit, len(pending)))
        for c in pending[:limit]:
            print(' ', c[0], '|', c[1], '|', c[2] or '-')
        return
    # Back up using SQLite backup API, capturing a consistent database snapshot.
    backup = db_path.with_name(db_path.stem + '_backup_' + datetime.now().strftime('%Y%m%d_%H%M%S_%f') + db_path.suffix)
    with sqlite3.connect(db_path) as source, sqlite3.connect(backup) as target:
        source.backup(target)
    print('Backup created:', backup)
    inserted = 0
    skipped = 0
    with sqlite3.connect(db_path) as conn:
        conn.execute('PRAGMA foreign_keys=ON')
        with conn:
            existing = {r[0].strip().upper() for r in conn.execute('SELECT sku FROM products')}
            for sku, name, category, brand, pack_size in candidates:
                if sku in existing:
                    skipped += 1
                    continue
                if inserted >= limit:
                    break
                cursor = conn.execute(
                    'INSERT INTO products (sku, product_name, category, brand, pack_size) VALUES (?, ?, ?, ?, ?)',
                    (sku, name, category, brand, pack_size))
                conn.execute('INSERT INTO inventory (product_id, quantity, minimum_stock) VALUES (?, 0, 0)',
                             (cursor.lastrowid,))
                existing.add(sku)
                inserted += 1
    print('Imported:', inserted, '| Skipped existing:', skipped)
    print('All imported quantities and minimum_stock set to 0. Existing records unchanged.')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='Preview or import NSTAR WooCommerce products safely')
    parser.add_argument('csv_file', type=Path)
    parser.add_argument('--db', type=Path, default=DEFAULT_DB)
    parser.add_argument('--limit', type=int, default=10)
    parser.add_argument('--commit', action='store_true', help='Back up database and insert products')
    args = parser.parse_args()
    try:
        run(args.csv_file, args.db, args.limit, args.commit)
    except (ValueError, FileNotFoundError, sqlite3.Error) as exc:
        parser.error(str(exc))
