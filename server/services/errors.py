class NotFoundError(Exception):
    pass


class ConflictError(Exception):
    pass


class UnauthorizedError(Exception):
    pass


class ForbiddenError(Exception):
    pass


class StateTransitionError(Exception):
    pass
