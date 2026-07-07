"""P2 #2: SDK 异常映射测试。

验证 ``raise_for_status`` 把 HTTP status_code 映射到正确的异常子类，
调用方可 catch 子类写语义化处理（``except MAPNotFoundError`` 等）。
"""

import pytest
from map_client.exceptions import (
    MAPAuthenticationError,
    MAPClientError,
    MAPConflictError,
    MAPHTTPError,
    MAPNotFoundError,
    MAPPermissionError,
    MAPRateLimitError,
    MAPServerError,
    MAPValidationError,
    raise_for_status,
)


@pytest.mark.parametrize(
    "status_code, expected_cls",
    [
        (400, MAPValidationError),
        (401, MAPAuthenticationError),
        (403, MAPPermissionError),
        (404, MAPNotFoundError),
        (409, MAPConflictError),
        (422, MAPValidationError),
        (429, MAPRateLimitError),
        # 未识别的 4xx 落到 MAPClientError
        (418, MAPClientError),
        (451, MAPClientError),
        # 5xx 落到 MAPServerError
        (500, MAPServerError),
        (502, MAPServerError),
        (503, MAPServerError),
    ],
)
def test_status_code_maps_to_specific_exception(status_code, expected_cls):
    with pytest.raises(expected_cls) as exc_info:
        raise_for_status(status_code, "test detail")
    assert exc_info.value.status_code == status_code
    assert exc_info.value.detail == "test detail"
    # 子类关系：所有具体异常都是 MAPHTTPError 子类（向后兼容）
    assert isinstance(exc_info.value, MAPHTTPError)


def test_all_subclasses_are_map_http_error_subclasses():
    """所有具体异常子类都是 MAPHTTPError 子类，确保 ``except MAPHTTPError`` 兜底。"""
    for cls in [
        MAPClientError,
        MAPServerError,
        MAPValidationError,
        MAPAuthenticationError,
        MAPPermissionError,
        MAPNotFoundError,
        MAPConflictError,
        MAPRateLimitError,
    ]:
        assert issubclass(cls, MAPHTTPError), f"{cls.__name__} must subclass MAPHTTPError"


def test_exception_str_format():
    """__str__ 包含 status_code 和 detail，便于 logging。"""
    exc = MAPNotFoundError(404, "Topic not found")
    assert str(exc) == "HTTP 404: Topic not found"
    assert exc.status_code == 404
    assert exc.detail == "Topic not found"
