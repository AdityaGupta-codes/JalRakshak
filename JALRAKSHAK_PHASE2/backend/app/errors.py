class NotFoundError(Exception):
    """Requested row does not exist -> HTTP 404."""


class ConflictError(Exception):
    """Operation not possible in the current state -> HTTP 409."""


class InvalidInput(ValueError):
    """The caller sent something the system cannot accept -> HTTP 400."""


class DatabaseUnavailable(Exception):
    """PostgreSQL cannot be reached -> HTTP 503."""


class NotSignedIn(Exception):
    """No valid session -> HTTP 401."""


class Forbidden(Exception):
    """Signed in, but not allowed to do this -> HTTP 403."""


class TooManyAttempts(Exception):
    """Too many wrong passwords -> HTTP 429."""
