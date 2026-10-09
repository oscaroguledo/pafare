"""In-process TTL cache for read-only display data. Never use for cart/checkout stock
reservation - those must always read the live, transactionally-locked DB row."""
from cachetools import TTLCache

# Per-process cache: fine even with multiple app instances, since clients already
# tolerate a few seconds of staleness (polling every ~20s) for a display count.
product_read_cache = TTLCache(maxsize=5000, ttl=5)

# This process's copy of the home page sections, in front of Redis. Five minutes keeps nearly every
# request in memory while the servers pick up the daily 20:00 rebuild (or any refill) within 5 minutes.
home_page_cache = TTLCache(maxsize=1, ttl=300)


def invalidate_variant(variant_id, product_id=None) -> None:
    """Drop cached reads for a variant (and its parent product) after a stock or data change."""
    product_read_cache.pop(("variant", variant_id), None)
    if product_id is not None:
        invalidate_product(product_id)


def invalidate_product(product_id) -> None:
    """Drop cached reads for a product and its variant list after an edit or delete."""
    product_read_cache.pop(("product", product_id), None)
    product_read_cache.pop(("variants", product_id), None)


def invalidate_all() -> None:
    """Drop every cached read - used for rare, bulk admin writes where per-key invalidation isn't worth it."""
    product_read_cache.clear()
