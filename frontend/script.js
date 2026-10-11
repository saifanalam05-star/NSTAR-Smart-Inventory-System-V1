/**
 * NSTAR inventory dashboard.
 * Reads live inventory and transaction data from FastAPI and allows staff
 * to make manual stock adjustments. The backend remains the source of truth.
 */

const API_URL = ""; // Same-origin requests: frontend and API share port 8000.
const REFRESH_INTERVAL_MS = 5000;

// Dashboard elements.
const inventoryTable = document.getElementById("inventory-table");
const activityTable = document.getElementById("activity-table");
const searchInput = document.getElementById("inventory-search");
const stockFilter = document.getElementById("stock-filter");
const manualForm = document.getElementById("manual-stock-form");
const manualSku = document.getElementById("manual-sku");
const manualAction = document.getElementById("manual-action");
const manualQuantity = document.getElementById("manual-quantity");
const manualMessage = document.getElementById("manual-message");
const manualSubmit = document.getElementById("manual-submit");

let currentPage = 1;
let totalPages = 1;
let pageSize = 25;
let searchDebounce;
let inventoryRequestSequence = 0;
const pageInfo = document.getElementById("inventory-page-info");
const pageNumber = document.getElementById("inventory-page-number");
const prevButton = document.getElementById("inventory-prev");
const nextButton = document.getElementById("inventory-next");
const pageSizeSelect = document.getElementById("inventory-page-size");

// -----------------------------------------------------------------------------
// INVENTORY STATUS AND DISPLAY
// -----------------------------------------------------------------------------

function getStockStatus(item) {
    if (item.quantity === 0) {
        return { label: "OUT OF STOCK", className: "out-of-stock" };
    }
    if (item.quantity <= item.minimum_stock) {
        return { label: "LOW STOCK", className: "low-stock" };
    }
    return { label: "IN STOCK", className: "in-stock" };
}

/** Append a text-only cell, avoiding HTML injection from database values. */
function appendTextCell(row, value) {
    const cell = document.createElement("td");
    cell.textContent = value ?? "";
    row.appendChild(cell);
}

/** Show a message in an otherwise empty table. */
function showTableMessage(table, message) {
    table.replaceChildren();
    const row = document.createElement("tr");
    const cell = document.createElement("td");
    cell.colSpan = 6;
    cell.textContent = message;
    row.appendChild(cell);
    table.appendChild(row);
}

function displayInventory(items) {
    inventoryTable.replaceChildren();

    items.forEach(item => {
        const row = document.createElement("tr");
        [item.sku, item.product_name, item.category, item.quantity, item.minimum_stock]
            .forEach(value => appendTextCell(row, value));

        const status = getStockStatus(item);
        const statusCell = document.createElement("td");
        const badge = document.createElement("span");
        badge.className = `status-badge ${status.className}`;
        badge.textContent = status.label;
        statusCell.appendChild(badge);
        row.appendChild(statusCell);
        inventoryTable.appendChild(row);
    });
}

function updateSummary(summary) {
    document.getElementById("total-products").textContent = summary.total_products;
    document.getElementById("total-units").textContent = summary.total_units;
    document.getElementById("low-stock").textContent = summary.low_stock;
}

function updatePagination(result) {
    currentPage = result.page;
    totalPages = result.total_pages;
    const start = result.total ? (currentPage - 1) * pageSize + 1 : 0;
    const end = result.total ? start + result.items.length - 1 : 0;
    pageInfo.textContent = `Showing ${start}–${end} of ${result.total} matching products`;
    pageNumber.textContent = `Page ${currentPage} of ${totalPages}`;
    prevButton.disabled = currentPage <= 1;
    nextButton.disabled = currentPage >= totalPages;
}

// Search and filter on the server, not just within the current page.
searchInput.addEventListener("input", () => {
    currentPage = 1;
    clearTimeout(searchDebounce);
    searchDebounce = setTimeout(loadInventory, 250);
});
stockFilter.addEventListener("change", () => { currentPage = 1; loadInventory(); });
pageSizeSelect.addEventListener("change", () => {
    pageSize = Number(pageSizeSelect.value);
    currentPage = 1;
    loadInventory();
});
prevButton.addEventListener("click", () => {
    if (currentPage > 1) { currentPage--; loadInventory(); }
});
nextButton.addEventListener("click", () => {
    if (currentPage < totalPages) { currentPage++; loadInventory(); }
});

// The original /api/inventory endpoint remains available for other consumers.
async function loadInventory() {
    const requestId = ++inventoryRequestSequence;
    const params = new URLSearchParams({
        page: String(currentPage), page_size: String(pageSize),
        search: searchInput.value, stock: stockFilter.value
    });
    try {
        const response = await fetch(`${API_URL}/api/inventory/paged?${params}`);
        if (!response.ok) throw new Error(`Server returned ${response.status}`);
        const result = await response.json();
        // Ignore stale responses when a search or page change is still in flight.
        if (requestId !== inventoryRequestSequence) return;
        updateSummary(result.summary);
        if (result.items.length) displayInventory(result.items);
        else showTableMessage(inventoryTable, "No matching products found.");
        updatePagination(result);
    } catch (error) {
        if (requestId !== inventoryRequestSequence) return;
        console.error("Failed to load inventory:", error);
        showTableMessage(inventoryTable, "Unable to load inventory.");
        pageInfo.textContent = "Unable to load page.";
    }
}

// -----------------------------------------------------------------------------
// RECENT ACTIVITY
// -----------------------------------------------------------------------------

function displayTransactions(transactions) {
    activityTable.replaceChildren();

    if (transactions.length === 0) {
        showTableMessage(activityTable, "No transactions recorded yet.");
        return;
    }

    transactions.forEach(transaction => {
        const row = document.createElement("tr");
        const quantityChange = transaction.quantity_change > 0
            ? `+${transaction.quantity_change}`
            : String(transaction.quantity_change);

        [transaction.timestamp, transaction.sku, transaction.product_name,
            transaction.action, quantityChange, transaction.new_quantity]
            .forEach(value => appendTextCell(row, value));

        activityTable.appendChild(row);
    });
}

async function loadTransactions() {
    try {
        const response = await fetch(`${API_URL}/api/transactions`);
        if (!response.ok) throw new Error(`Server returned ${response.status}`);

        const transactions = await response.json();
        // The backend limits this response to the 10 latest transactions.
        displayTransactions(transactions);
    } catch (error) {
        console.error("Failed to load transactions:", error);
        showTableMessage(activityTable, "Unable to load transaction history.");
    }
}

// -----------------------------------------------------------------------------
// MANUAL STOCK ADJUSTMENTS
// -----------------------------------------------------------------------------

async function loadManualProducts() {
    try {
        const response = await fetch(`${API_URL}/api/inventory`);
        if (!response.ok) throw new Error("Unable to retrieve products.");

        const products = await response.json();
        // Retain the placeholder option and avoid duplicate entries if reloaded.
        const selectedSku = manualSku.value;
        manualSku.replaceChildren(new Option("Select a product", ""));
        products.forEach(product => {
            manualSku.appendChild(new Option(`${product.sku} - ${product.product_name}`, product.sku));
        });
        manualSku.value = selectedSku;
    } catch (error) {
        console.error("Failed to load product dropdown:", error);
        manualMessage.textContent = "Unable to load products. Please refresh the page.";
    }
}

manualForm.addEventListener("submit", async event => {
    event.preventDefault();

    const sku = manualSku.value;
    const action = manualAction.value;
    const quantity = Number(manualQuantity.value);

    if (!sku || !Number.isInteger(quantity) || quantity <= 0) {
        manualMessage.textContent = "Select a product and enter a valid quantity.";
        return;
    }

    manualSubmit.disabled = true;
    manualMessage.textContent = "Updating inventory...";

    try {
        const response = await fetch(`${API_URL}/api/inventory/manual`, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ sku, action, quantity })
        });
        if (!response.ok) throw new Error(`Server returned ${response.status}`);

        const result = await response.json();
        if (!result.success) {
            manualMessage.textContent = `Update failed: ${result.message || result.error}`;
            return;
        }

        manualMessage.textContent = `Success! ${result.product_name}: ` +
            `${result.previous_quantity} → ${result.new_quantity}`;
        manualQuantity.value = "1";

        // Reflect successful changes immediately, without waiting for polling.
        await Promise.all([loadInventory(), loadTransactions()]);
    } catch (error) {
        console.error("Manual inventory update failed:", error);
        manualMessage.textContent = "Unable to update inventory. Check the backend connection.";
    } finally {
        manualSubmit.disabled = false;
    }
});

// -----------------------------------------------------------------------------
// PRODUCT MANAGEMENT — CREATE PRODUCT
// -----------------------------------------------------------------------------

const productForm = document.getElementById("add-product-form");
const productSubmit = document.getElementById("product-submit");
const productMessage = document.getElementById("product-message");

productForm.addEventListener("submit", async event => {
    event.preventDefault();

    const sku = document.getElementById("product-sku").value.trim().toUpperCase();
    const product_name = document.getElementById("product-name").value.trim();
    const category = document.getElementById("product-category").value.trim();
    const brand = document.getElementById("product-brand").value.trim();
    const pack_size = document.getElementById("product-pack-size").value.trim();
    const quantity = Number(document.getElementById("product-quantity").value);
    const minimum_stock = Number(document.getElementById("product-minimum-stock").value);

    if (!sku || !product_name || !Number.isSafeInteger(quantity) || quantity < 0 ||
        !Number.isSafeInteger(minimum_stock) || minimum_stock < 0) {
        productMessage.textContent = "Enter a SKU, product name, and valid nonnegative whole-number stock values.";
        return;
    }

    productSubmit.disabled = true;
    productMessage.textContent = "Creating product...";

    try {
        const response = await fetch(`${API_URL}/api/products`, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ sku, product_name, category, brand, pack_size, quantity, minimum_stock })
        });
        const result = await response.json();
        if (!response.ok || result.success === false) {
            const detail = result.detail;
            const message = typeof detail === "string" ? detail :
                (result.message || `Server returned ${response.status}`);
            productMessage.textContent = `Could not create product: ${message}`;
            return;
        }

        productMessage.textContent = `Product ${result.sku || sku} created successfully.`;
        productForm.reset();
        await Promise.all([loadInventory(), loadManualProducts()]);
    } catch (error) {
        console.error("Create product failed:", error);
        productMessage.textContent = "Unable to create product. Check the backend connection.";
    } finally {
        productSubmit.disabled = false;
    }
});

// -----------------------------------------------------------------------------
// DASHBOARD STARTUP AND AUTO-REFRESH
// -----------------------------------------------------------------------------

loadInventory();
loadTransactions();
loadManualProducts();

setInterval(() => {
    loadInventory();
    loadTransactions();
}, REFRESH_INTERVAL_MS);
