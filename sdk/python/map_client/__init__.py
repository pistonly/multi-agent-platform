from map_client.client import MAPClient
from map_client.errors import (
    RECOVERY_HINTS,
    STATE_MACHINE_ERROR_CODES,
    recovery_hint,
)
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
    # 156172e9 I1(a): STATE_MACHINE.* error registry + recovery hint helper
    "STATE_MACHINE_ERROR_CODES",
    "RECOVERY_HINTS",
    "recovery_hint",
]
