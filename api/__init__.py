from .accounts import (
    auth_router, oauth_router, passkeys_router, user_router, addresses_router
)
from .catalog import products_router, review_router, inventory_router, category_router
from .commerce import cart_router, orders_router, payments_router, refunds_router, shipping_router, shipping_tracking_router, tax_router, promocodes_router, subscriptions_router, webhooks_router
from .analytics import analytics_router
from .system import health_router

# Admin functionality lives in its domain-specific module (e.g. accounts/user.py,
# catalog/products.py, commerce/orders.py) rather than grouped here.

__all__ = [
    "auth_router", "oauth_router", "passkeys_router", "user_router", "addresses_router",
    "products_router", "review_router", "inventory_router", "category_router",
    "cart_router", "orders_router", "payments_router", "refunds_router", "shipping_router", "shipping_tracking_router", "tax_router", "promocodes_router", "subscriptions_router", "webhooks_router",
    "analytics_router",
    "health_router",
]
