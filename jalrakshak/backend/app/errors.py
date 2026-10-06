class NotFoundError(Exception):
    """Requested row does not exist -> HTTP 404."""

class ConflictError(Exception):
    """Operation not possible in the current state -> HTTP 409."""
