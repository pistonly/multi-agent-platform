class MAPError(Exception):
    """Base SDK error."""


class MAPHTTPError(MAPError):
    def __init__(self, status_code: int, detail: str) -> None:
        self.status_code = status_code
        self.detail = detail
        super().__init__(f"HTTP {status_code}: {detail}")
