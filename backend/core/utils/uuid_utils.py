"""UUID utilities with UUIDv7 support for better performance and ordering."""
import uuid
import time
import random
from typing import Union


def uuid7() -> uuid.UUID:
    """Generate a UUIDv7 (time-ordered UUID) for better DB index locality than UUIDv4."""
    # Get current timestamp in milliseconds
    timestamp_ms = int(time.time() * 1000)
    
    # Convert timestamp to 48-bit value
    timestamp_bytes = timestamp_ms.to_bytes(6, byteorder='big')
    
    # Generate 10 bytes of random data
    random_bytes = random.randbytes(10)
    
    # Combine timestamp and random bytes
    uuid_bytes = timestamp_bytes + random_bytes
    
    # Set version (4 bits) to 0111 (7)
    uuid_bytes = bytearray(uuid_bytes)
    uuid_bytes[6] = (uuid_bytes[6] & 0x0f) | 0x70
    
    # Set variant (2 bits) to 10
    uuid_bytes[8] = (uuid_bytes[8] & 0x3f) | 0x80
    
    return uuid.UUID(bytes=bytes(uuid_bytes))


def uuid7_str() -> str:
    """Generate UUIDv7 as string"""
    return str(uuid7())


def is_uuid7(uuid_obj: Union[str, uuid.UUID]) -> bool:
    """Check if UUID is version 7"""
    if isinstance(uuid_obj, str):
        try:
            uuid_obj = uuid.UUID(uuid_obj)
        except ValueError:
            return False
    
    return uuid_obj.version == 7


def extract_timestamp_from_uuid7(uuid_obj: Union[str, uuid.UUID]) -> int:
    """Extract timestamp (ms since epoch) from UUIDv7; returns 0 if not a valid UUIDv7."""
    if isinstance(uuid_obj, str):
        try:
            uuid_obj = uuid.UUID(uuid_obj)
        except ValueError:
            return 0
    
    if not is_uuid7(uuid_obj):
        return 0
    
    # Extract first 48 bits (6 bytes) as timestamp
    uuid_bytes = uuid_obj.bytes
    timestamp_bytes = uuid_bytes[:6]
    
    return int.from_bytes(timestamp_bytes, byteorder='big')


def uuid7_from_timestamp(timestamp_ms: int) -> uuid.UUID:
    """Generate UUIDv7 with specific timestamp (for testing/migration)."""
    # Convert timestamp to 48-bit value
    timestamp_bytes = timestamp_ms.to_bytes(6, byteorder='big')
    
    # Generate 10 bytes of random data
    random_bytes = random.randbytes(10)
    
    # Combine timestamp and random bytes
    uuid_bytes = timestamp_bytes + random_bytes
    
    # Set version (4 bits) to 0111 (7)
    uuid_bytes = bytearray(uuid_bytes)
    uuid_bytes[6] = (uuid_bytes[6] & 0x0f) | 0x70
    
    # Set variant (2 bits) to 10
    uuid_bytes[8] = (uuid_bytes[8] & 0x3f) | 0x80
    
    return uuid.UUID(bytes=bytes(uuid_bytes))