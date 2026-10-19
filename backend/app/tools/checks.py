"""Checks on whether a request makes sense, before policy decides whether it is allowed."""


def check_quantity(requested: int, bought: int, refunded: int) -> str | None:
    """Return a message the agent can act on if the quantity is invalid, else None.

    ``refunded`` is the number of units already in refunds that count (pending, approved or
    completed).
    """
    if requested < 1:
        return "The quantity must be at least 1."
    remaining = bought - refunded
    if remaining <= 0:
        return "This item has already been fully refunded."
    if requested > remaining:
        unit = "unit" if remaining == 1 else "units"
        return f"Only {remaining} {unit} can still be refunded."
    return None
