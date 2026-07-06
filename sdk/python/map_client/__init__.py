from map_client.client import MAPClient
from map_client.exceptions import (
    MAPAuthenticationError,
    MAPClientError,
    MAPConflictError,
    MAPError,
    MAPHTTPError,
    MAPNotFoundError,
    MAPPermissionError,
    MAPRateLimitError,
    MAPServerError,
    MAPValidationError,
)

__all__ = [
    "MAPClient",
    "MAPError",
    "MAPHTTPError",
    # P2 #2: 细分子类，调用方可 catch 具体异常写语义化处理
    "MAPClientError",
    "MAPServerError",
    "MAPValidationError",
    "MAPAuthenticationError",
    "MAPPermissionError",
    "MAPNotFoundError",
    "MAPConflictError",
    "MAPRateLimitError",
]
