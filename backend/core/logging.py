"""Colored print-based logger — drop-in replacement for structured logging."""
import traceback
from datetime import datetime

# ANSI color codes
_RESET  = "\033[0m"
_BOLD   = "\033[1m"
_COLORS = {
    "DEBUG":    "\033[36m",   # Cyan
    "INFO":     "\033[32m",   # Green
    "WARNING":  "\033[33m",   # Yellow
    "ERROR":    "\033[31m",   # Red
    "CRITICAL": "\033[35m",   # Magenta
}
_DIM    = "\033[2m"


def _print(level: str, name: str, message: str) -> None:
    color = _COLORS.get(level, _RESET)
    ts    = datetime.now().strftime("%H:%M:%S")
    print(f"{_DIM}{ts}{_RESET} {color}{_BOLD}{level:<8}{_RESET} {_DIM}[{name}]{_RESET} {message}")


class StructuredLogger:
    def __init__(self, name: str):
        self.name = name

    def debug(self, message: str, *args, **kwargs) -> None:
        _print("DEBUG", self.name, message % args if args else message)

    def info(self, message: str, *args, **kwargs) -> None:
        _print("INFO", self.name, message % args if args else message)

    def warning(self, message: str, *args, **kwargs) -> None:
        _print("WARNING", self.name, message % args if args else message)

    def error(self, message: str, *args, **kwargs) -> None:
        _print("ERROR", self.name, message % args if args else message)

    def critical(self, message: str, *args, **kwargs) -> None:
        _print("CRITICAL", self.name, message % args if args else message)

    def exception(self, message: str, *args, **kwargs) -> None:
        _print("ERROR", self.name, message % args if args else message)
        traceback.print_exc()

    # Compat shims for any callers using extended methods
    def log_request(self, *args, **kwargs) -> None:
        _print("INFO", self.name, f"request: {args} {kwargs}")

    def log_database_operation(self, *args, **kwargs) -> None:
        _print("DEBUG", self.name, f"db: {args} {kwargs}")

    def log_business_event(self, *args, **kwargs) -> None:
        _print("INFO", self.name, f"event: {args} {kwargs}")


def get_structured_logger(name: str) -> StructuredLogger:
    return StructuredLogger(name)
