"""Error responses shown in the OpenAPI docs, so each route lists what it can answer with."""

_DESCRIPTIONS = {
    400: "The request is wrong for the current state (bad page, empty update, ...)",
    401: "Not signed in, or the token is invalid or expired",
    403: "Signed in but missing the permission or the account isn't allowed",
    404: "Not found (or not yours)",
    409: "Conflicts with existing data (duplicate, still in use, not available, wrong status)",
    422: "The request body or query parameters failed validation",
    500: "Unexpected server error",
}


def errors(*codes: int) -> dict:
    """The `responses=` argument for a route: errors(401, 403, 404)."""
    return {code: {"description": _DESCRIPTIONS[code]} for code in codes}
