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

    # ---- bookings (routes for these come later) ----
    HOTELS_VIEW = "hotels:view"
    HOTELS_CREATE = "hotels:create"
    HOTELS_EDIT = "hotels:edit"
    HOTELS_DELETE = "hotels:delete"
    ROOM_TYPES_VIEW = "room_types:view"
    ROOM_TYPES_CREATE = "room_types:create"
    ROOM_TYPES_EDIT = "room_types:edit"
    ROOM_TYPES_DELETE = "room_types:delete"
    ROOMS_VIEW = "rooms:view"
    ROOMS_CREATE = "rooms:create"
    ROOMS_EDIT = "rooms:edit"
    ROOMS_DELETE = "rooms:delete"
    BOOKINGS_VIEW = "bookings:view"
    BOOKINGS_CREATE = "bookings:create"
    BOOKINGS_EDIT = "bookings:edit"
    BOOKINGS_DELETE = "bookings:delete"
    REVIEWS_VIEW = "reviews:view"
    REVIEWS_CREATE = "reviews:create"
    REVIEWS_EDIT = "reviews:edit"
    REVIEWS_DELETE = "reviews:delete"

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
}

# Every scope's name, for seeding. Scopes without an entry in DESCRIPTIONS get a generic one.
SCOPES = [scope.value for scope in Scope]


def description(scope: Scope) -> str:
    return DESCRIPTIONS.get(scope, f"{scope.value.split(':')[1].replace('_', ' ').capitalize()} {scope.value.split(':')[0].replace('_', ' ')}")
