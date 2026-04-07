"""Tests for playground_client.py — caching, filtering, error handling."""

from playground_client import _reset_cache_for_tests


def test_reset_cache():
    """Cache reset helper works without error."""
    _reset_cache_for_tests()
    # Just verify it doesn't raise
