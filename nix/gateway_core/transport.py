"""Bounded transport primitives, independent of routing and credentials."""


def bounded_response_read(response, maximum):
    """Read decoded bodies with a cap even when Content-Length is absent."""
    raw = response.read(maximum + 1)
    if len(raw) > maximum:
        raise ValueError("upstream response too large")
    return raw
