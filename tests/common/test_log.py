"""
Tests for the logger's helper for errors.
"""

from copytrader.common import log


def test_an_errors_traceback_comes_indented_under_the_message():
    try:
        raise ValueError("no such account")
    except ValueError as error:
        lines = log.with_traceback("reading the fills of 0xa failed", error).splitlines()
    assert lines[0] == "reading the fills of 0xa failed"
    assert lines[1] == "    Traceback (most recent call last):" and lines[-1] == "    ValueError: no such account"
