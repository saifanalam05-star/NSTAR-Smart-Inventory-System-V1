"""RFID-to-product lookup for the NSTAR inventory system.

The RFID reader identifies a product; inventory_service performs the actual
stock adjustment and records the transaction.
"""

import sqlite3
from pathlib import Path

from backend.inventory_service import add_stock, remove_stock


# Use the same database file as the inventory service.
DB_PATH = Path(__file__).resolve().parent / "nstar.db"


def connect_db():
    """Open an SQLite connection with foreign-key checks enabled."""
    conn = sqlite3.connect(DB_PATH)
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def process_rfid(rfid_uid, action, device_id="NSTAR-ESP32-01"):
    """Resolve an RFID tag and apply a one-unit ADD or REMOVE operation.

    Return the inventory service's result, or an error dictionary if the
    requested action is invalid or the tag is not registered to an active product.
    """
    # The reader may include spaces in UIDs; the database stores compact UIDs.
    rfid_uid = rfid_uid.replace(" ", "").upper()
    action = action.upper()

    if action not in ("ADD", "REMOVE"):
        return {
            "success": False,
            "error_code": "INVALID_ACTION",
            "message": "Action must be ADD or REMOVE.",
        }

    # Look up only active products; inactive products cannot be scanned.
    conn = connect_db()
    try:
        cursor = conn.execute(
            """
            SELECT sku, product_name
            FROM products
            WHERE rfid_uid = ? AND active = 1
            """,
            (rfid_uid,),
        )
        product = cursor.fetchone()
    finally:
        conn.close()

    if product is None:
        return {
            "success": False,
            "error_code": "UNKNOWN_TAG",
            "message": "RFID tag is not registered.",
            "rfid_uid": rfid_uid,
        }

    sku, product_name = product
    print("\nRFID card recognized.")
    print("RFID UID:", rfid_uid)
    print("Product:", product_name)
    print("SKU:", sku)

    # Delegate inventory updates and transaction logging to inventory_service.
    if action == "ADD":
        return add_stock(sku, 1, source="RFID", device_id=device_id)

    return remove_stock(sku, 1, source="RFID", device_id=device_id)
