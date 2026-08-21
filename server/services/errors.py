class NotFoundError(Exception):
    pass


class ConflictError(Exception):
    """Domain conflict (HTTP 409). Optional ``error`` is a stable machine code."""

    error: str | None = None

    def __init__(self, message: str, *, error: str | None = None) -> None:
        super().__init__(message)
        if error is not None:
            self.error = error


class BadRequestError(Exception):
    """Caller-supplied payload is well-formed (syntactically valid JSON /
    body) but semantically rejected — e.g. a missing cross-reference, an
    out-of-range enum value, or a field combination that does not
    satisfy a domain precondition. Maps to HTTP 400.

    Distinct from pydantic's 422 (which fires on shape-level schema
    failures before the handler runs).
    """


class UnauthorizedError(Exception):
    pass


class ForbiddenError(Exception):
    pass


class StateTransitionError(Exception):
    """A request was structurally valid but the domain state machine refuses
    the requested transition.

    Optional attributes are surfaced to the HTTP layer by
    :func:`server.main.register_domain_exception_handlers` so callers can
    programmatically route on the failure mode instead of pattern-matching
    the human-readable ``detail`` string:

    - ``error_code`` — stable machine identifier (default ``"state_machine_error"``).
      I1(d) introduces the ``REVIEW_REJECT_RESULT_MISUSE`` subcode so the
      client can distinguish "wrong CLI command for this intent" from
      generic state-machine refusals.
    - ``hint`` — short remediation hint surfaced to operators and CLI users.
    - ``retryable`` — whether the caller can succeed by retrying with the
      same input after fixing state out-of-band. Always ``False`` for misuse
      subcodes; preserved as a structured field for future retryable errors.
    """

    error_code: str = "state_machine_error"
    hint: str | None = None
    retryable: bool = False

    def __init__(
        self,
        message: str,
        *,
        error_code: str | None = None,
        hint: str | None = None,
        retryable: bool | None = None,
    ) -> None:
        super().__init__(message)
        if error_code is not None:
            self.error_code = error_code
        if hint is not None:
            self.hint = hint
        if retryable is not None:
            self.retryable = retryable
