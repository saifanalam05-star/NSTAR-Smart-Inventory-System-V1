"""Initialize the NSTAR SQLite database with an empty product catalogue.

Safe to run repeatedly against the current schema: existing products,
inventory quantities, and transaction history are preserved. This script
does not migrate older database schemas.
"""

import sqlite3
from pathlib import Path

DB_PATH = Path(__file__).resolve().parent / "nstar.db"


def create_tables(conn: sqlite3.Connection) -> None:
    """Create catalogue, inventory, and transaction tables if absent."""
    conn.execute("""
        CREATE TABLE IF NOT EXISTS products (
            product_id INTEGER PRIMARY KEY AUTOINCREMENT,
            sku TEXT NOT NULL UNIQUE,
            product_name TEXT NOT NULL,
            category TEXT,
            brand TEXT,
            pack_size TEXT,
            rfid_uid TEXT UNIQUE,
            active INTEGER NOT NULL DEFAULT 1
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS inventory (
            product_id INTEGER PRIMARY KEY,
            quantity INTEGER NOT NULL DEFAULT 0,
            minimum_stock INTEGER NOT NULL DEFAULT 0,
            last_updated TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (product_id) REFERENCES products(product_id)
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS transactions (
            transaction_id INTEGER PRIMARY KEY AUTOINCREMENT,
            product_id INTEGER NOT NULL,
            action TEXT NOT NULL,
            quantity_change INTEGER NOT NULL,
            previous_quantity INTEGER NOT NULL,
            new_quantity INTEGER NOT NULL,
            source TEXT NOT NULL,
            device_id TEXT,
            timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (product_id) REFERENCES products(product_id)
        )
    """)


def verify_schema(conn: sqlite3.Connection) -> None:
    """Reject an old product schema rather than silently treating it as updated."""
    columns = {row[1] for row in conn.execute("PRAGMA table_info(products)")}
    missing = {"brand", "pack_size"} - columns
    if missing:
        raise RuntimeError(
            "Existing products table lacks columns: "
            + ", ".join(sorted(missing))
            + ". Replace or migrate the old nstar.db, then rerun this script."
        )


def display_database_summary(conn: sqlite3.Connection) -> None:
    """Print counts and a brief inventory overview."""
    for label, table in (
        ("Products", "products"),
        ("Inventory records", "inventory"),
        ("Transactions", "transactions"),
    ):
        count = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
        print(f"{label}: {count}")

    rows = conn.execute("""
        SELECT p.product_id, p.sku, p.product_name, p.brand,
               p.pack_size, i.quantity
        FROM products AS p
        JOIN inventory AS i ON p.product_id = i.product_id
        ORDER BY p.product_id
    """).fetchall()
    print("\nNSTAR INVENTORY")
    print("-" * 80)
    for product_id, sku, name, brand, pack_size, quantity in rows:
        print(
            f"{product_id} | {sku} | {name} | "
            f"Brand: {brand or '-'} | Pack: {pack_size or '-'} | Qty: {quantity}"
        )


def main() -> None:
    """Initialize tables without seeding or modifying existing records."""
    conn = sqlite3.connect(DB_PATH)
    try:
        conn.execute("PRAGMA foreign_keys = ON")
        with conn:
            create_tables(conn)
            verify_schema(conn)
        display_database_summary(conn)
    finally:
        conn.close()
    print("\nNSTAR database initialized successfully.")


if __name__ == "__main__":
    main()
