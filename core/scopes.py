"""Every permission scope in one place.

A scope is named `resource:action`. The rule: each distinct action gets its own scope, so sensitive actions
(suspending, assigning roles, granting permissions) are never bundled into a general one like `users:edit`.
Routes use these constants (`require_permission(Scope.USERS_SUSPEND)`), the permission seed uses `SCOPES`.

Adding a scope: add it to `Scope` and to `DESCRIPTIONS`, use it in a route, then add a migration that inserts it
(migrations hardcode their own copy of the names, so old ones never change). `python -m scripts.seed_permissions`
also creates any missing scope in a dev database.
"""
from enum import Enum


class Scope(str, Enum):
    # ---- users ----
    USERS_VIEW = "users:view"
    USERS_CREATE = "users:create"
    USERS_EDIT = "users:edit"                      # profile fields only
    USERS_CHANGE_EMAIL = "users:change_email"
    USERS_RESET_PASSWORD = "users:reset_password"
    USERS_ACTIVATE = "users:activate"
    USERS_DEACTIVATE = "users:deactivate"
    USERS_SUSPEND = "users:suspend"
    USERS_UNSUSPEND = "users:unsuspend"

    # ---- roles and permissions ----
    ROLES_VIEW = "roles:view"
    ROLES_CREATE = "roles:create"
    ROLES_EDIT = "roles:edit"                      # name and description only
    ROLES_DELETE = "roles:delete"
    ROLES_ASSIGN = "roles:assign"                  # give a user a different role
    ROLES_GRANT = "roles:grant"                    # change which permissions a role has
    PERMISSIONS_VIEW = "permissions:view"
    PERMISSIONS_CREATE = "permissions:create"
    PERMISSIONS_EDIT = "permissions:edit"
    PERMISSIONS_DELETE = "permissions:delete"

    # ---- hotels, room types and rooms (reading them is public; these are for changing them) ----
    HOTELS_CREATE = "hotels:create"
    HOTELS_EDIT = "hotels:edit"
    HOTELS_DELETE = "hotels:delete"
    ROOM_TYPES_CREATE = "room_types:create"
    ROOM_TYPES_EDIT = "room_types:edit"
    ROOM_TYPES_DELETE = "room_types:delete"
    ROOMS_CREATE = "rooms:create"
    ROOMS_EDIT = "rooms:edit"
    ROOMS_DELETE = "rooms:delete"

    # ---- bookings (guests always manage their own; these are for other people's bookings) ----
    BOOKINGS_VIEW = "bookings:view"
    BOOKINGS_CREATE = "bookings:create"            # book on behalf of a guest
    BOOKINGS_EDIT = "bookings:edit"
    BOOKINGS_CANCEL = "bookings:cancel"
    BOOKINGS_CONFIRM = "bookings:confirm"
    BOOKINGS_CHECK_IN = "bookings:check_in"
    BOOKINGS_CHECK_OUT = "bookings:check_out"
    BOOKINGS_NO_SHOW = "bookings:no_show"
    BOOKINGS_DISCOUNT = "bookings:discount"

    # ---- reviews (guests write and manage their own; approved ones are public) ----
    REVIEWS_VIEW = "reviews:view"                  # includes unapproved ones
    REVIEWS_EDIT = "reviews:edit"
    REVIEWS_DELETE = "reviews:delete"
    REVIEWS_MODERATE = "reviews:moderate"          # approve or hide

    # ---- payments (guests pay for and read their own; these are for the money side) ----
    PAYMENTS_VIEW = "payments:view"                # everyone's payments
    PAYMENTS_CONFIRM = "payments:confirm"          # record that the money arrived
    PAYMENTS_FAIL = "payments:fail"                # record that a payment did not go through
    PAYMENTS_REFUND = "payments:refund"

    def __str__(self) -> str:
        return self.value


DESCRIPTIONS = {
    Scope.USERS_VIEW: "List and read users",
    Scope.USERS_CREATE: "Create users with any role",
    Scope.USERS_EDIT: "Edit other users' profile fields",
    Scope.USERS_CHANGE_EMAIL: "Change a user's email address",
    Scope.USERS_RESET_PASSWORD: "Send a user a password-reset link",
    Scope.USERS_ACTIVATE: "Activate an inactive account",
    Scope.USERS_DEACTIVATE: "Deactivate an account",
    Scope.USERS_SUSPEND: "Suspend an account",
    Scope.USERS_UNSUSPEND: "Lift an account suspension",
    Scope.ROLES_VIEW: "List and read roles and their permissions",
    Scope.ROLES_CREATE: "Create roles",
    Scope.ROLES_EDIT: "Rename roles and edit their description",
    Scope.ROLES_DELETE: "Delete roles",
    Scope.ROLES_ASSIGN: "Give a user a different role",
    Scope.ROLES_GRANT: "Change which permissions a role has",
    Scope.PERMISSIONS_VIEW: "List and read permissions",
    Scope.PERMISSIONS_CREATE: "Create permissions",
    Scope.PERMISSIONS_EDIT: "Edit a permission's description",
    Scope.PERMISSIONS_DELETE: "Delete permissions",
    Scope.HOTELS_CREATE: "Create hotels",
    Scope.HOTELS_EDIT: "Edit hotels",
    Scope.HOTELS_DELETE: "Delete hotels",
    Scope.ROOM_TYPES_CREATE: "Create room types",
    Scope.ROOM_TYPES_EDIT: "Edit room types",
    Scope.ROOM_TYPES_DELETE: "Delete room types",
    Scope.ROOMS_CREATE: "Create rooms",
    Scope.ROOMS_EDIT: "Edit rooms",
    Scope.ROOMS_DELETE: "Delete rooms",
    Scope.BOOKINGS_VIEW: "List and read everyone's bookings",
    Scope.BOOKINGS_CREATE: "Book a room on behalf of a guest",
    Scope.BOOKINGS_EDIT: "Reschedule or change other guests' bookings",
    Scope.BOOKINGS_CANCEL: "Cancel other guests' bookings",
    Scope.BOOKINGS_CONFIRM: "Confirm a pending booking",
    Scope.BOOKINGS_CHECK_IN: "Check a guest in",
    Scope.BOOKINGS_CHECK_OUT: "Check a guest out",
    Scope.BOOKINGS_NO_SHOW: "Mark a booking as a no-show",
    Scope.BOOKINGS_DISCOUNT: "Apply a discount to a booking",
    Scope.REVIEWS_VIEW: "List and read all reviews, including unapproved ones",
    Scope.REVIEWS_EDIT: "Edit other guests' reviews",
    Scope.REVIEWS_DELETE: "Delete reviews",
    Scope.REVIEWS_MODERATE: "Approve or hide reviews",
    Scope.PAYMENTS_VIEW: "List and read everyone's payments",
    Scope.PAYMENTS_CONFIRM: "Record that a payment was received",
    Scope.PAYMENTS_FAIL: "Record that a payment failed",
    Scope.PAYMENTS_REFUND: "Refund a payment",
}

# Every scope's name, for seeding. Scopes without an entry in DESCRIPTIONS get a generic one.
SCOPES = [scope.value for scope in Scope]


def description(scope: Scope) -> str:
    return DESCRIPTIONS.get(scope, f"{scope.value.split(':')[1].replace('_', ' ').capitalize()} {scope.value.split(':')[0].replace('_', ' ')}")
