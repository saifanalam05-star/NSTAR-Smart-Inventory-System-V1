"""HTTP API for the NSTAR Smart Inventory System.

Receives RFID scan requests from the ESP32 and provides inventory data
and product management endpoints to the web dashboard.
"""

import sqlite3

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pathlib import Path
from pydantic import BaseModel, Field

from backend.csv_import_preview import preview_csv
from backend.rfid_service import process_rfid
from backend.inventory_service import connect_db, add_stock, remove_stock


# -----------------------------------------------------------------------------
# API APPLICATION
# -----------------------------------------------------------------------------

app = FastAPI(
    title="NSTAR Inventory API",
    description="Backend API for the NSTAR Smart Inventory System",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://127.0.0.1:5500", "http://localhost:5500"],
    allow_credentials=False,
    allow_methods=["GET", "POST"],
    allow_headers=["Content-Type"],
)


# -----------------------------------------------------------------------------
# REQUEST MODELS
# -----------------------------------------------------------------------------

class RFIDScanRequest(BaseModel):
    """Data sent by an ESP32 when an RFID tag is scanned."""
    rfid_uid: str
    action: str
    device_id: str = "NSTAR-ESP32-01"


class ManualStockRequest(BaseModel):
    """Data submitted when manually adjusting inventory."""
    sku: str
    action: str
    quantity: int


class CreateProductRequest(BaseModel):
    """Information required to register a new inventory product."""
    sku: str = Field(min_length=1)
    product_name: str = Field(min_length=1)
    category: str | None = None
    brand: str | None = None
    pack_size: str | None = None
    quantity: int = Field(default=0, ge=0)
    minimum_stock: int = Field(default=0, ge=0)


# -----------------------------------------------------------------------------
# API ENDPOINTS
# -----------------------------------------------------------------------------

@app.get("/api/health")
def home():
    """Return a simple response to confirm that the API is running."""
    return {"message": "NSTAR API is running"}


@app.post("/api/inventory/scan")
def scan_rfid(request: RFIDScanRequest):
    """Process a scanned tag and return the inventory service result."""
    return process_rfid(request.rfid_uid, request.action, request.device_id)


@app.get("/api/inventory")
def get_inventory():
    """Return all active products and their current stock levels."""
    conn = connect_db()
    try:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT
                p.product_id, p.sku, p.product_name, p.category,
                i.quantity, i.minimum_stock
            FROM products p
            JOIN inventory i ON p.product_id = i.product_id
            WHERE p.active = 1
            ORDER BY p.product_id
        """)
        columns = [column[0] for column in cursor.description]
        return [dict(zip(columns, row)) for row in cursor.fetchall()]
    finally:
        conn.close()


# -----------------------------------------------------------------------------
# PAGINATED INVENTORY (dashboard only; legacy /api/inventory stays unchanged)
# -----------------------------------------------------------------------------

@app.get("/api/inventory/paged")
def get_inventory_paged(page: int = 1, page_size: int = 25,
                        search: str = "", stock: str = "all"):
    """Return a filtered inventory page plus unfiltered dashboard totals."""
    if page < 1 or page_size not in (25, 50, 100):
        raise HTTPException(status_code=422, detail="Invalid page or page_size.")
    if stock not in ("all", "in-stock", "low-stock", "out-of-stock"):
        raise HTTPException(status_code=422, detail="Invalid stock filter.")
    if len(search) > 200:
        raise HTTPException(status_code=422, detail="Search is too long.")

    conditions = ["p.active = 1"]
    params = []
    if search.strip():
        # Escape LIKE wildcards so user input is searched literally.
        term = search.strip().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        conditions.append("(p.sku LIKE ? ESCAPE '\\' OR p.product_name LIKE ? ESCAPE '\\')")
        params.extend([f"%{term}%", f"%{term}%"])
    if stock == "out-of-stock":
        conditions.append("i.quantity = 0")
    elif stock == "low-stock":
        conditions.append("i.quantity > 0 AND i.quantity <= i.minimum_stock")
    elif stock == "in-stock":
        conditions.append("i.quantity > 0 AND i.quantity > i.minimum_stock")
    where = " AND ".join(conditions)
    joined = "FROM products p JOIN inventory i ON p.product_id = i.product_id"

    conn = connect_db()
    try:
        totals = conn.execute("""
            SELECT COUNT(*), COALESCE(SUM(i.quantity), 0),
                   COALESCE(SUM(CASE WHEN i.quantity <= i.minimum_stock THEN 1 ELSE 0 END), 0)
            FROM products p JOIN inventory i ON p.product_id = i.product_id
            WHERE p.active = 1
        """).fetchone()
        count = conn.execute(f"SELECT COUNT(*) {joined} WHERE {where}", params).fetchone()[0]
        total_pages = max(1, (count + page_size - 1) // page_size)
        page = min(page, total_pages)
        cursor = conn.execute(f"""
            SELECT p.product_id, p.sku, p.product_name, p.category,
                   i.quantity, i.minimum_stock
            {joined} WHERE {where}
            ORDER BY p.product_id LIMIT ? OFFSET ?
        """, [*params, page_size, (page - 1) * page_size])
        columns = [col[0] for col in cursor.description]
        items = [dict(zip(columns, row)) for row in cursor.fetchall()]
        return {"items": items, "page": page, "page_size": page_size,
                "total": count, "total_pages": total_pages,
                "summary": {"total_products": totals[0], "total_units": totals[1],
                            "low_stock": totals[2]}}
    finally:
        conn.close()


@app.get("/api/transactions")
def get_transactions():
    """Return the 10 most recent inventory transactions."""
    conn = connect_db()
    try:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT
                t.transaction_id, t.timestamp, p.sku, p.product_name,
                t.action, t.quantity_change, t.previous_quantity,
                t.new_quantity, t.source, t.device_id
            FROM transactions t
            JOIN products p ON t.product_id = p.product_id
            ORDER BY t.timestamp DESC, t.transaction_id DESC
            LIMIT 10
        """)
        columns = [column[0] for column in cursor.description]
        return [dict(zip(columns, row)) for row in cursor.fetchall()]
    finally:
        conn.close()


@app.post("/api/inventory/manual")
def manual_inventory_update(request: ManualStockRequest):
    """Manually add or remove stock and record the transaction."""
    sku = request.sku.strip().upper()
    action = request.action.strip().upper()
    quantity = request.quantity

    if quantity <= 0:
        return {
            "success": False,
            "error": "INVALID_AMOUNT",
            "message": "Quantity must be greater than zero.",
        }

    if action == "ADD":
        return add_stock(sku, quantity, source="MANUAL")

    if action == "REMOVE":
        return remove_stock(sku, quantity, source="MANUAL")

    return {
        "success": False,
        "error": "INVALID_ACTION",
        "message": "Action must be ADD or REMOVE.",
    }


# -----------------------------------------------------------------------------
# PRODUCT MANAGEMENT
# -----------------------------------------------------------------------------

@app.post("/api/products", status_code=201)
def create_product(request: CreateProductRequest):
    """Create a product and its inventory row in one database transaction."""
    sku = request.sku.strip().upper()
    name = request.product_name.strip()
    category = (request.category or "").strip() or None
    brand = (request.brand or "").strip() or None
    pack_size = (request.pack_size or "").strip() or None

    if not sku or not name:
        raise HTTPException(status_code=400, detail="SKU and product name cannot be empty.")

    conn = connect_db()
    try:
        with conn:
            cursor = conn.execute(
                """
                INSERT INTO products (sku, product_name, category, brand, pack_size)
                VALUES (?, ?, ?, ?, ?)
                """,
                (sku, name, category, brand, pack_size),
            )
            product_id = cursor.lastrowid
            conn.execute(
                """
                INSERT INTO inventory (product_id, quantity, minimum_stock)
                VALUES (?, ?, ?)
                """,
                (product_id, request.quantity, request.minimum_stock),
            )
    except sqlite3.IntegrityError as exc:
        if "products.sku" in str(exc):
            raise HTTPException(status_code=409, detail=f"SKU {sku} already exists.") from exc
        raise HTTPException(status_code=409, detail="Database constraint prevented product creation.") from exc
    finally:
        conn.close()

    return {
        "success": True,
        "message": "Product created successfully.",
        "product_id": product_id,
        "sku": sku,
        "product_name": name,
        "category": category,
        "brand": brand,
        "pack_size": pack_size,
        "quantity": request.quantity,
        "minimum_stock": request.minimum_stock,
    }


# -----------------------------------------------------------------------------
# CSV IMPORT PREVIEW (READ-ONLY)
# -----------------------------------------------------------------------------

@app.post("/api/products/import-preview")
async def import_preview(request: Request):
    """Preview a WooCommerce CSV sent as the raw request body.

    Content-Type: text/csv. Does not change the database.
    """
    if request.headers.get("content-type", "").split(";")[0].strip() not in (
        "text/csv", "application/octet-stream"
    ):
        raise HTTPException(status_code=415, detail="Send raw CSV with Content-Type: text/csv.")

    # Reject oversized uploads while streaming, rather than buffering indefinitely.
    chunks = []
    total = 0
    async for chunk in request.stream():
        total += len(chunk)
        if total > 10 * 1024 * 1024:
            raise HTTPException(status_code=413, detail="CSV exceeds 10 MB limit.")
        chunks.append(chunk)

    conn = connect_db()
    try:
        existing_skus = [row[0] for row in conn.execute("SELECT sku FROM products")]
    finally:
        conn.close()

    try:
        return preview_csv(b"".join(chunks), existing_skus)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


# -----------------------------------------------------------------------------
# FRONTEND HOSTING
# -----------------------------------------------------------------------------

# Register this LAST so the API endpoints and /docs remain accessible.
# Resolving relative to this file works regardless of the working directory.
FRONTEND_DIR = Path(__file__).resolve().parent.parent / "frontend"
app.mount("/", StaticFiles(directory=FRONTEND_DIR, html=True), name="frontend")
