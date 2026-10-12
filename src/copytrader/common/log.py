"""
The one line logger the command line tools and the recorder use, and a helper for logging errors with their tracebacks.
"""

import traceback
from datetime import UTC, datetime


def log(message):
    """
    Print a message with the current UTC time in front, at once rather than buffered, so a log file shows it as it happens.
    """
    print(f"{datetime.now(UTC):%Y-%m-%d %H:%M:%S} {message}", flush=True)


def with_traceback(message, error):
    """
    The message followed by the error's traceback, indented under it, so an
    unexpected error in the log shows where it came from and not only its name.
    """
    lines = "".join(traceback.format_exception(error)).rstrip().splitlines()
    return message + "".join(f"\n    {line}" for line in lines)
