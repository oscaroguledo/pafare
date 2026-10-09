from .accounts import auth_router, user_router, role_router, permission_router
from .bookings import hotel_router, room_type_router, room_router, booking_router, review_router
from .billings import payment_router
from .system import health_router

__all__ = [
    "auth_router", "user_router", "role_router", "permission_router",
    "hotel_router", "room_type_router", "room_router", "booking_router", "review_router",
    "payment_router",
    "health_router",
]
