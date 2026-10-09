from fastapi import HTTPException
from datetime import datetime
from typing import Any, Dict, Optional
from fastapi import Request
from fastapi.responses import JSONResponse
from fastapi.exceptions import RequestValidationError
from sqlalchemy.exc import SQLAlchemyError
import traceback

def format_error_response(
    message: str,
    status_code: int = 500,
    error_code: Optional[str] = None,
    **kwargs
) -> Dict[str, Any]:
    """Format a standardized error response"""
    return {
        "success": False,
        "message": message,
        "error_code": error_code or f"ERR_{status_code}",
        "timestamp": datetime.now().isoformat(),
        **kwargs
    }

class APIException(HTTPException):
    """Custom API exception with enhanced error details"""

    def __init__(
        self,
        status_code: int,
        message: str = "An unexpected API error occurred",  # Provide a default value
        detail: Optional[str] = None,
        error_code: Optional[str] = None,
        **kwargs
    ):
        self.message = message
        self.detail = detail or message
        self.error_code = error_code or f"ERR_{status_code}"
        self.timestamp = datetime.now().isoformat()

        super().__init__(status_code=status_code, detail=self.detail, **kwargs)


class ValidationException(APIException):
    """Exception for validation errors"""

    def __init__(self, message: str = "Validation failed", errors: Optional[Dict[str, Any]] = None):
        self.errors = errors or {}
        super().__init__(
            status_code=422,
            message=message,
            error_code="VALIDATION_ERROR"
        )


class AuthenticationException(APIException):
    """Exception for authentication errors"""

    def __init__(self, message: str = "Authentication failed"):
        super().__init__(
            status_code=401,
            message=message,
            error_code="AUTH_ERROR"
        )


class AuthorizationException(APIException):
    """Exception for authorization errors"""

    def __init__(self, message: str = "Access denied"):
        super().__init__(
            status_code=403,
            message=message,
            error_code="AUTHORIZATION_ERROR"
        )


class NotFoundException(APIException):
    """Exception for resource not found errors"""

    def __init__(self, message: str = "Resource not found", resource: Optional[str] = None):
        self.resource = resource
        super().__init__(
            status_code=404,
            message=message,
            error_code="NOT_FOUND"
        )


class ConflictException(APIException):
    """Exception for conflict errors"""

    def __init__(self, message: str = "Resource conflict"):
        super().__init__(
            status_code=409,
            message=message,
            error_code="CONFLICT_ERROR"
        )


class RateLimitException(APIException):
    """Exception for rate limiting errors"""

    def __init__(self, message: str = "Rate limit exceeded", retry_after: Optional[int] = None):
        self.retry_after = retry_after
        super().__init__(
            status_code=429,
            message=message,
            error_code="RATE_LIMIT_ERROR"
        )


class DatabaseException(APIException):
    """Exception for database errors"""

    def __init__(self, message: str = "Database error occurred"):
        super().__init__(
            status_code=500,
            message=message,  # Explicitly pass message
            error_code="DATABASE_ERROR"
        )


class ExternalServiceException(APIException):
    """Exception for external service errors"""

    def __init__(self, message: str = "External service error", service: Optional[str] = None):
        self.service = service
        super().__init__(
            status_code=502,
            message=message,
            error_code="EXTERNAL_SERVICE_ERROR"
        )

async def api_exception_handler(request: Request, exc: APIException) -> JSONResponse:
    """Handle custom API exceptions"""
    return JSONResponse(
        status_code=exc.status_code,
        content={
            "success": False,
            "message": exc.message,
            "error_code": exc.error_code,
            "timestamp": exc.timestamp,
            "detail": exc.detail if exc.detail != exc.message else None
        }
    )


async def http_exception_handler(request: Request, exc: HTTPException) -> JSONResponse:
    """Handle standard HTTP exceptions"""

    return JSONResponse(
        status_code=exc.status_code,
        content={
            "success": False,
            "message": exc.detail,
            "error_code": f"HTTP_{exc.status_code}",
            "timestamp": datetime.now().isoformat()
        }
    )


async def validation_exception_handler(request: Request, exc: RequestValidationError) -> JSONResponse:
    """Handle validation errors"""
    # Format validation errors
    errors = {}
    for error in exc.errors():
        field = ".".join(str(loc)
                         for loc in error["loc"][1:])  # Skip 'body' prefix
        errors[field] = error["msg"]

    return JSONResponse(
        status_code=422,
        content={
            "success": False,
            "message": "Validation failed",
            "error_code": "VALIDATION_ERROR",
            "timestamp": datetime.now().isoformat(),
            "errors": errors
        }
    )


async def sqlalchemy_exception_handler(request: Request, exc: SQLAlchemyError) -> JSONResponse:
    """Handle SQLAlchemy database errors"""

    # Log the full error for debugging
    print(traceback.format_exc())

    return JSONResponse(
        status_code=500,
        content={
            "success": False,
            "message": "A database error occurred",
            "error_code": "DATABASE_ERROR",
            "timestamp": datetime.now().isoformat()
        }
    )


async def general_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    """Handle unexpected exceptions"""

    # Log the full error for debugging
    print(traceback.format_exc())

    return JSONResponse(
        status_code=500,
        content={
            "success": False,
            "message": "An unexpected error occurred",
            "error_code": "INTERNAL_ERROR",
            "timestamp": datetime.now().isoformat()
        }
    )
