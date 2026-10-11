class NotFoundError(Exception):
    """Requested row does not exist -> HTTP 404."""


class ConflictError(Exception):
    """Operation not possible in the current state -> HTTP 409."""


class InvalidInput(ValueError):
    """The caller sent something the system cannot accept -> HTTP 400."""


class DatabaseUnavailable(Exception):
    """PostgreSQL cannot be reached -> HTTP 503."""
