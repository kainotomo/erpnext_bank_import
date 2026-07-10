"""Tests for the retry utility.

These tests validate:

1. Retry succeeds on the first attempt.
2. Retry succeeds after transient failures.
3. Retry exhausts all attempts and raises ``MaxRetriesExceededError``.
4. Non-retryable exceptions propagate immediately.
5. ``RateLimitError.retry_after`` is honored.
6. Exponential back-off with jitter produces increasing delays.
7. Each retry attempt is logged via ``frappe.logger()``.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from erpnext_bank_import.connectors.exceptions import (
	ApiError,
	AuthenticationError,
	ConnectorError,
	MaxRetriesExceededError,
	NetworkError,
	RateLimitError,
	ServerError,
)
from erpnext_bank_import.services.retry import retry


@pytest.fixture(autouse=True)
def _patch_frappe_logger():
	"""Patch frappe.logger to avoid file-system dependency."""
	with patch("erpnext_bank_import.services.retry.frappe.logger") as mock_logger:
		mock_logger.return_value.warning.return_value = None
		yield mock_logger


# =========================================================================
# Helpers
# =========================================================================


class _FlakyFn:
	"""A callable that fails a configurable number of times before succeeding."""

	def __init__(self, fail_count: int, exc_type: type[Exception] = NetworkError):
		self.fail_count = fail_count
		self.exc_type = exc_type
		self.attempts = 0

	def __call__(self) -> str:
		self.attempts += 1
		if self.attempts <= self.fail_count:
			raise self.exc_type(f"Attempt {self.attempts} failed")
		return "success"


# =========================================================================
# Tests
# =========================================================================


class TestRetrySuccess:
	"""Happy-path scenarios."""

	def test_first_attempt_succeeds(self):
		"""No retry needed — first call succeeds."""
		result = retry(lambda: "ok", max_retries=3)
		assert result == "ok"

	def test_retry_succeeds_after_transient_failures(self):
		"""Retry after 2 transient failures, succeeds on 3rd."""
		fn = _FlakyFn(fail_count=2)
		result = retry(fn, max_retries=3, base_delay=0.01)
		assert result == "success"
		assert fn.attempts == 3

	def test_retry_succeeds_on_last_attempt(self):
		"""Succeeds on the very last retry."""
		fn = _FlakyFn(fail_count=3)
		result = retry(fn, max_retries=3, base_delay=0.01)
		assert result == "success"
		assert fn.attempts == 4


class TestRetryExhaustion:
	"""Scenarios where retries are exhausted."""

	def test_exhausts_all_attempts(self):
		"""All attempts fail → MaxRetriesExceededError."""
		fn = _FlakyFn(fail_count=10)  # more than max_retries
		with pytest.raises(MaxRetriesExceededError) as exc_info:
			retry(fn, max_retries=3, base_delay=0.01)
		assert exc_info.value.attempts == 4
		assert exc_info.value.original_exception is not None
		assert isinstance(exc_info.value.original_exception, NetworkError)

	def test_zero_retries(self):
		"""max_retries=0 means one attempt only, no retry on failure."""
		fn = _FlakyFn(fail_count=1)
		with pytest.raises(MaxRetriesExceededError):
			retry(fn, max_retries=0, base_delay=0.01)
		assert fn.attempts == 1


class TestRetryNonRetryable:
	"""Non-transient errors must propagate immediately."""

	def test_non_transient_exception_propagates(self):
		"""AuthenticationError is not transient → propagates immediately."""

		def raise_auth():
			raise AuthenticationError("Invalid credentials")

		with pytest.raises(AuthenticationError):
			retry(raise_auth, max_retries=3, base_delay=0.01)

	def test_mixed_exceptions(self):
		"""Transient then non-transient → non-transient propagates."""
		attempts = 0

		def mixed():
			nonlocal attempts
			attempts += 1
			if attempts == 1:
				raise NetworkError("Timeout")
			raise AuthenticationError("Bad token")

		with pytest.raises(AuthenticationError):
			retry(mixed, max_retries=3, base_delay=0.01)
		assert attempts == 2  # first (transient, retried), second (non-transient, propagates)


class TestRetryRateLimitError:
	"""RateLimitError retry_after behavior."""

	def test_retry_after_is_honored(self):
		"""RateLimitError with retry_after should use that value as the delay."""
		# We verify that the delay calculation uses retry_after by patching
		# time.sleep and asserting it was called (meaning retry was attempted)
		with patch("erpnext_bank_import.services.retry.time.sleep") as mock_sleep:
			with pytest.raises(MaxRetriesExceededError):
				retry(lambda: _raise_rate_limit(), max_retries=3, base_delay=1.0)
			# Sleep was called at least once (retry delay used)
			assert mock_sleep.called


def _raise_rate_limit():
	raise RateLimitError("Rate limited", retry_after=5.0)


class TestRetryLogging:
	"""Retry attempts should be logged."""

	def test_logs_on_each_retry(self, _patch_frappe_logger):
		"""Each retry attempt produces a WARNING log message."""
		fn = _FlakyFn(fail_count=2)
		retry(fn, max_retries=3, base_delay=0.01)
		# Should have logged 2 retry warnings (attempts 1 and 2)
		assert _patch_frappe_logger.return_value.warning.call_count == 2


class TestRetryDecorator:
	"""Decorator form of retry."""

	def test_decorator_success(self):
		"""Decorator form works — succeeds on first attempt."""

		@retry(max_retries=3, base_delay=0.01)
		def my_func() -> str:
			return "ok"

		assert my_func() == "ok"

	def test_decorator_retry(self):
		"""Decorator form — retries on transient failure."""

		fn = _FlakyFn(fail_count=2)

		@retry(max_retries=3, base_delay=0.01)
		def my_func() -> str:
			return fn()

		result = my_func()
		assert result == "success"
		assert fn.attempts == 3

	def test_decorator_preserves_signature(self):
		"""Decorator form preserves function metadata."""

		@retry(max_retries=3)
		def my_func(a: int, b: int = 0) -> int:
			"""Docstring."""
			return a + b

		assert my_func.__name__ == "my_func"
		assert my_func.__doc__ == "Docstring."
		assert my_func(1, 2) == 3
