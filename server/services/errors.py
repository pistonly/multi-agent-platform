class NotFoundError(Exception):
    pass


class ConflictError(Exception):
    pass


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
