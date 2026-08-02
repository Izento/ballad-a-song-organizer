"""Transaction boundary errors."""


class ApplyBlocked(RuntimeError):
    """A reviewed action cannot safely cross the mutation boundary."""


__all__ = ["ApplyBlocked"]
