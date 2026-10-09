"""Response utility for consistent API responses."""
from typing import Any, Optional, Dict
from fastapi.responses import JSONResponse
from fastapi import status
from pydantic import BaseModel
from uuid import UUID
from datetime import datetime, date
from decimal import Decimal
from sqlalchemy import inspect


class Response(JSONResponse):
    """Standardized API response wrapper, directly returnable from FastAPI routes."""

    def __init__(
        self,
        success: bool = True,
        data: Any = None,
        message: str = "Success",
        status_code: int = status.HTTP_200_OK,
        pagination: Optional[Dict[str, Any]] = None,
        errors: Optional[list] = None,
        **kwargs
    ):
        """Initialize response object that can be returned directly from FastAPI routes."""

        # Convert Pydantic models to dictionaries for JSON serialization
        serialized_data = self._serialize_data(data)

        response_data = {
            "success": success,
            "data": serialized_data,
            "message": message
        }

        if pagination:
            response_data["pagination"] = pagination

        if errors:
            response_data["errors"] = errors

        super().__init__(
            content=response_data,
            status_code=status_code,
            **kwargs
        )

    def _serialize_data(self, data: Any) -> Any:
        """Convert Pydantic models, SQLAlchemy models, and other non-serializable objects to JSON-serializable format."""
        if data is None:
            return None
        elif isinstance(data, BaseModel):
            # Convert Pydantic model to dict with JSON serialization mode
            return data.model_dump(mode='json')
        elif self._is_sqlalchemy_model(data):
            # Convert SQLAlchemy model to dict
            return self._sqlalchemy_model_to_dict(data)
        elif isinstance(data, UUID):
            return str(data)
        elif isinstance(data, (datetime, date)):
            return data.isoformat()
        elif isinstance(data, Decimal):
            return float(data)
        elif isinstance(data, list):
            # Convert list of items (may contain Pydantic/SQLAlchemy models)
            return [self._serialize_data(item) for item in data]
        elif isinstance(data, dict):
            # Convert dict values (may contain Pydantic/SQLAlchemy models)
            return {key: self._serialize_data(value) for key, value in data.items()}
        else:
            # Return as-is for JSON-serializable types
            return data

    def _is_sqlalchemy_model(self, obj: Any) -> bool:
        """Check if an object is a SQLAlchemy model instance."""
        try:
            return inspect(obj).mapper is not None
        except Exception:
            return False

    def _sqlalchemy_model_to_dict(self, obj: Any) -> Dict[str, Any]:
        """Convert a SQLAlchemy model instance to a dictionary."""
        result = {}
        for column in inspect(obj).mapper.columns:
            value = getattr(obj, column.key)
            # Handle nested serialization for common types
            if isinstance(value, UUID):
                value = str(value)
            elif isinstance(value, (datetime, date)):
                value = value.isoformat() if value else None
            elif isinstance(value, Decimal):
                value = float(value)
            result[column.key] = value
        return result

    @staticmethod
    def success(
        data: Any = None,
        message: str = "Success",
        status_code: int = status.HTTP_200_OK,
        pagination: Optional[Dict[str, Any]] = None
    ) -> "Response":
        """Create a successful response."""
        return Response(
            success=True,
            data=data,
            message=message,
            status_code=status_code,
            pagination=pagination
        )

    @staticmethod
    def error(
        message: str = "An error occurred",
        status_code: int = status.HTTP_400_BAD_REQUEST,
        data: Any = None,
        errors: Optional[list] = None
    ) -> "Response":
        """Create an error response."""
        return Response(
            success=False,
            data=data,
            message=message,
            status_code=status_code,
            errors=errors
        )
