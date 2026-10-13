"""
Time helpers shared across the project.

The file is not called time.py because that would shadow Python's own time module.
"""

from datetime import datetime

DAY = 86400


def epoch(value):
    """
    Seconds since 1970 for an ISO 8601 timestamp, such as the venues send, or None when there is none or it does not parse.
    """
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None
