"""Shared API utility functions."""


def escape_ilike(value: str) -> str:
    """Escape ILIKE wildcard characters in user input.

    Prevents SQL ILIKE injection where user-supplied % or _ characters
    would act as wildcards in LIKE/ILIKE queries.

    Usage: f"%{escape_ilike(q)}%" as the bound parameter value.
    """
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
