"""Date bounds for Qdrant DatetimeRange."""


def date_bound(value: str, end: bool) -> str:
    v = value.strip()
    if len(v) == 4 and v.isdigit():
        return f"{v}-12-31T23:59:59Z" if end else f"{v}-01-01T00:00:00Z"
    if len(v) == 10:
        return f"{v}T23:59:59Z" if end else f"{v}T00:00:00Z"
    return v
