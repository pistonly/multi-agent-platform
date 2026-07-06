"""SDK 异常体系（P2 #2 错误统一映射）。

层级：
    MAPError
    └── MAPHTTPError          (4xx/5xx 通用，保留 status_code + detail)
        ├── MAPClientError    (4xx 客户端错误)
        │   ├── MAPValidationError        (400 / 422)
        │   ├── MAPAuthenticationError    (401)
        │   ├── MAPPermissionError        (403)
        │   ├── MAPNotFoundError          (404)
        │   ├── MAPConflictError          (409)
        │   └── MAPRateLimitError         (429)
        └── MAPServerError    (5xx 服务端错误)

设计要点：
- 所有子类都是 ``MAPHTTPError`` 的子类，``except MAPHTTPError`` 仍能 catch
  全部 HTTP 错误（向后兼容）。
- ``MAPHTTPError`` 本身不再直接 raise；``_raise_for_status`` 根据 status_code
  实例化具体子类。CLI 可以 ``except MAPNotFoundError`` 写语义化错误处理，
  不用再 ``if exc.status_code == 404``。
- 未识别的 4xx 落到 ``MAPClientError``，未识别的 5xx 落到 ``MAPServerError``，
  方便 CLI 兜底。
"""


class MAPError(Exception):
    """Base SDK error."""


class MAPHTTPError(MAPError):
    """HTTP error from MAP API.

    保留 ``status_code`` / ``detail`` 字段供调用方读取；具体子类对应
    常见 HTTP 状态码，调用方可 catch 子类写语义化处理。
    """

    def __init__(self, status_code: int, detail: str) -> None:
        self.status_code = status_code
        self.detail = detail
        super().__init__(f"HTTP {status_code}: {detail}")


class MAPClientError(MAPHTTPError):
    """4xx client error (未细分子类的兜底）。"""


class MAPServerError(MAPHTTPError):
    """5xx server error."""


class MAPValidationError(MAPClientError):
    """400 / 422 — 请求参数校验失败或语义错误。"""


class MAPAuthenticationError(MAPClientError):
    """401 — 未认证或 token 失效。"""


class MAPPermissionError(MAPClientError):
    """403 — 已认证但无权限。"""


class MAPNotFoundError(MAPClientError):
    """404 — 资源不存在。"""


class MAPConflictError(MAPClientError):
    """409 — 状态冲突（重复提交、状态机非法转换等）。"""


class MAPRateLimitError(MAPClientError):
    """429 — 限流。"""


# status_code → exception class 映射表。
# 未列出的 4xx 落到 MAPClientError，5xx 落到 MAPServerError。
_STATUS_CODE_MAP: dict[int, type[MAPHTTPError]] = {
    400: MAPValidationError,
    401: MAPAuthenticationError,
    403: MAPPermissionError,
    404: MAPNotFoundError,
    409: MAPConflictError,
    422: MAPValidationError,
    429: MAPRateLimitError,
}


def raise_for_status(status_code: int, detail: str) -> None:
    """根据 status_code raise 对应的 MAPHTTPError 子类。

    单一入口，供 ``MAPClient._request`` 在 ``status_code >= 400`` 时调用。
    """
    exc_cls = _STATUS_CODE_MAP.get(status_code)
    if exc_cls is None:
        exc_cls = MAPServerError if status_code >= 500 else MAPClientError
    raise exc_cls(status_code, detail)
