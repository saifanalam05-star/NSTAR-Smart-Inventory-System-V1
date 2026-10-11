"""Conservative NSTAR category selection for WooCommerce exports.

Keep the deepest hierarchy under a recognized merchandise department. Never
infer categories from product names or customer/industry taxonomy labels.
"""
import re

PRODUCT_DEPARTMENTS = {
    'Grocery', 'Packaging', 'Cleaning & Janitorial', 'Disposable',
    'Bakery & Deli', 'Beverages', 'Office & Warehouse',
}


def clean_category(raw: str) -> str | None:
    """Return the most specific product taxonomy path, or None if ambiguous."""
    # WooCommerce escapes commas inside category names with a backslash.
    entries = re.split(r'(?<!\\),', raw or '')
    choices = []
    for entry in entries:
        entry = entry.replace(r'\,', ',').strip().replace('\u200b', '')
        parts = [p.strip() for p in entry.split(' > ') if p.strip()]
        if parts and parts[0] in PRODUCT_DEPARTMENTS:
            choices.append(' > '.join(parts))
    if not choices:
        return None
    # In case of two equally deep competing hierarchies, do not guess.
    depth = max(len(s.split(' > ')) for s in choices)
    deepest = sorted(set(s for s in choices if len(s.split(' > ')) == depth))
    return deepest[0] if len(deepest) == 1 else None
