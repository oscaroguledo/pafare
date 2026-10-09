from pydantic import BaseModel, ConfigDict
from typing import TypeVar, Generic, Optional

# Define a generic type for the data payload
DataType = TypeVar("DataType")


class APIResponse(BaseModel, Generic[DataType]):
    success: bool
    message: str
    data: Optional[DataType]
    pagination: Optional[dict]

    model_config = ConfigDict(from_attributes=True)  # For Pydantic v2
