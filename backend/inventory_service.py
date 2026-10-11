"""Inventory operations and transaction history for the NSTAR backend.

This module provides SQLite-backed stock updates and simple terminal reports.
The dictionaries returned by add_stock() and remove_stock() are also consumed
by the RFID service and FastAPI, so their response keys remain consistent.
"""

import sqlite3
from pathlib import Path


# Keep the database alongside this module, regardless of the working directory.
DB_PATH = Path(__file__).resolve().parent / "nstar.db"


def connect_db():
    """Open a database connection with foreign-key checks enabled."""
    conn = sqlite3.connect(DB_PATH)
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


# =========================================================
# INVENTORY REPORT
# =========================================================

def display_inventory():
    """Print each product's current quantity and minimum-stock threshold."""
    conn = connect_db()
    try:
        rows = conn.execute(
            """
            SELECT
                products.sku,
                products.product_name,
                inventory.quantity,
                inventory.minimum_stock
            FROM products
            JOIN inventory
                ON products.product_id = inventory.product_id
            ORDER BY products.product_id
            """
        ).fetchall()
    finally:
        conn.close()

    print("\nNSTAR CURRENT INVENTORY")
    print("------------------------------------------------------------")

    for sku, product_name, quantity, minimum_stock in rows:
        print(
            sku,
            "|", product_name,
            "| Qty:", quantity,
            "| Minimum:", minimum_stock,
        )


# =========================================================
# ADD STOCK
# =========================================================

def add_stock(sku, amount, source="MANUAL", device_id=None):
    """Increase stock and record a positive inventory transaction.

    Return a success dictionary or an error dictionary with an error_code.
    """
    if amount <= 0:
        print("ERROR: Amount must be greater than 0.")
        return {
            "success": False,
            "error_code": "INVALID_AMOUNT",
            "message": "Amount must be greater than 0.",
        }

    conn = connect_db()
    try:
        # Look up the product and its current quantity using a bound SKU.
        product = conn.execute(
            """
            SELECT
                products.product_id,
                products.product_name,
                inventory.quantity
            FROM products
            JOIN inventory
                ON products.product_id = inventory.product_id
            WHERE products.sku = ?
            """,
            (sku,),
        ).fetchone()

        if product is None:
            print("ERROR: Product not found.")
            return {
                "success": False,
                "error_code": "PRODUCT_NOT_FOUND",
                "message": "Product not found.",
            }

        product_id, product_name, previous_quantity = product
        new_quantity = previous_quantity + amount

        # Update the quantity and log the change in the same transaction.
        # If either SQL statement fails, neither change should be committed.
        with conn:
            conn.execute(
                """
                UPDATE inventory
                SET quantity = ?,
                    last_updated = CURRENT_TIMESTAMP
                WHERE product_id = ?
                """,
                (new_quantity, product_id),
            )
            conn.execute(
                """
                INSERT INTO transactions (
                    product_id,
                    action,
                    quantity_change,
                    previous_quantity,
                    new_quantity,
                    source,
                    device_id
                )
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    product_id,
                    "ADD",
                    amount,
                    previous_quantity,
                    new_quantity,
                    source,
                    device_id,
                ),
            )
    finally:
        conn.close()

    print("\nStock added successfully.")
    print("Product:", product_name)
    print("Previous quantity:", previous_quantity)
    print("Added:", amount)

    return {
        "success": True,
        "sku": sku,
        "product_name": product_name,
        "action": "ADD",
        "previous_quantity": previous_quantity,
        "new_quantity": new_quantity,
    }


# =========================================================
# REMOVE STOCK
# =========================================================

def remove_stock(sku, amount, source="MANUAL", device_id=None):
    """Decrease stock without allowing negative quantities; log the change."""
    if amount <= 0:
        print("ERROR: Amount must be greater than 0.")
        return {
            "success": False,
            "error_code": "INVALID_AMOUNT",
            "message": "Amount must be greater than 0.",
        }

    conn = connect_db()
    try:
        product = conn.execute(
            """
            SELECT
                products.product_id,
                products.product_name,
                inventory.quantity
            FROM products
            JOIN inventory
                ON products.product_id = inventory.product_id
            WHERE products.sku = ?
            """,
            (sku,),
        ).fetchone()

        if product is None:
            print("ERROR: Product not found.")
            return {
                "success": False,
                "error_code": "PRODUCT_NOT_FOUND",
                "message": "Product not found.",
            }

        product_id, product_name, previous_quantity = product

        # Reject the request before changing the database if stock is low.
        if amount > previous_quantity:
            print("\nERROR: Not enough stock.")
            print("Product:", product_name)
            print("Current quantity:", previous_quantity)
            print("Attempted to remove:", amount)
            return {
                "success": False,
                "error_code": "INSUFFICIENT_STOCK",
                "message": "Not enough stock.",
                "sku": sku,
                "product_name": product_name,
                "current_quantity": previous_quantity,
            }

        new_quantity = previous_quantity - amount

        # A removal is stored as a negative quantity_change in the audit log.
        with conn:
            conn.execute(
                """
                UPDATE inventory
                SET quantity = ?,
                    last_updated = CURRENT_TIMESTAMP
                WHERE product_id = ?
                """,
                (new_quantity, product_id),
            )
            conn.execute(
                """
                INSERT INTO transactions (
                    product_id,
                    action,
                    quantity_change,
                    previous_quantity,
                    new_quantity,
                    source,
                    device_id
                )
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    product_id,
                    "REMOVE",
                    -amount,
                    previous_quantity,
                    new_quantity,
                    source,
                    device_id,
                ),
            )
    finally:
        conn.close()

    print("\nStock removed successfully.")
    print("Product:", product_name)
    print("Previous quantity:", previous_quantity)
    print("Removed:", amount)

    return {
        "success": True,
        "sku": sku,
        "product_name": product_name,
        "action": "REMOVE",
        "previous_quantity": previous_quantity,
        "new_quantity": new_quantity,
    }


# =========================================================
# TRANSACTION HISTORY REPORT
# =========================================================

def display_transactions():
    """Print the transaction audit trail in transaction-ID order."""
    conn = connect_db()
    try:
        rows = conn.execute(
            """
            SELECT
                transactions.transaction_id,
                products.sku,
                transactions.action,
                transactions.quantity_change,
                transactions.previous_quantity,
                transactions.new_quantity,
                transactions.source,
                transactions.timestamp
            FROM transactions
            JOIN products
                ON transactions.product_id = products.product_id
            ORDER BY transactions.transaction_id
            """
        ).fetchall()
    finally:
        conn.close()

    print("\nNSTAR TRANSACTIONS")
    print("------------------------------------------------------------")

    for transaction_id, sku, action, change, previous, current, source, timestamp in rows:
        print(
            "ID:", transaction_id,
            "| SKU:", sku,
            "|", action,
            "| Change:", change,
            "|", previous, "->", current,
            "| Source:", source,
            "|", timestamp,
        )


# Only run terminal reports when this module is executed directly.
# Importing it from FastAPI or rfid_service.py does not print reports.
if __name__ == "__main__":
    display_inventory()
    display_transactions()
