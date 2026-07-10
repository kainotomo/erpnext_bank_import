"""Retry utility for transient connector failures.

Provides a standalone ``retry()`` function and a decorator form for
wrapping connector calls (e.g. API requests) that may fail with
transient errors such as rate limits, server errors, or network
timeouts.

Usage
-----
.. code-block:: python

    from erpnext_bank_import.services.retry import retry

    # Function form
    data = retry(
        lambda: requests.get(url, timeout=10),
        max_retries=3,
    )


    # Decorator form
    @retry(max_retries=3, retryable_exceptions=(RateLimitError, ServerError))
    def fetch_page(self, account_id, page_token): ...
"""

from __future__ import annotations

import random
import time
from collections.abc import Callable
from functools import wraps
from typing import Any, TypeVar

import frappe

from erpnext_bank_import.connectors.exceptions import (
	MaxRetriesExceededError,
	RateLimitError,
	is_transient_error,
)

F = TypeVar("F", bound=Callable[..., Any])

# Default retryable exception types (anything tagged as transient).
# Callers can override via the ``retryable_exceptions`` parameter.
_DEFAULT_RETRYABLE = (Exception,)  # filtered through is_transient_error


def retry(
	fn: Callable[[], Any] | None = None,
	*,
	max_retries: int = 3,
	base_delay: float = 1.0,
	backoff_factor: float = 2.0,
	max_delay: float = 60.0,
	retryable_exceptions: tuple[type[Exception], ...] = _DEFAULT_RETRYABLE,
) -> Any:
	"""Execute *fn* with exponential back-off retry for transient errors.

	Can be used as a function wrapper or a decorator::

	    # Function form
	    result = retry(lambda: connector.fetch_transactions(...))


	    # Decorator form
	    @retry(max_retries=3)
	    def do_fetch():
	        return connector.fetch_transactions(...)

	Args:
	    fn: The zero-argument callable to execute and possibly retry.
	    max_retries: Maximum number of retry attempts (default 3).
	    base_delay: Initial delay in seconds before the first retry (default 1).
	    backoff_factor: Multiplier applied to the delay after each attempt (default 2).
	    max_delay: Maximum delay in seconds between retries (default 60).
	    retryable_exceptions: Tuple of exception types that are eligible
	        for retry.  Defaults to all exceptions; the ``is_transient_error``
	        helper filters which ones actually trigger a retry.

	Returns:
	    The return value of *fn* on success.

	Raises:
	    MaxRetriesExceededError: If all attempts fail with transient errors.
	    Any non-transient exception propagates immediately.
	"""
	if fn is not None:
		# Direct call: retry(lambda: ...)
		return _do_retry(
			fn,
			max_retries=max_retries,
			base_delay=base_delay,
			backoff_factor=backoff_factor,
			max_delay=max_delay,
			retryable_exceptions=retryable_exceptions,
		)

	# Decorator form: @retry(...)
	def decorator(func: F) -> F:
		@wraps(func)
		def wrapper(*args: Any, **kwargs: Any) -> Any:
			return _do_retry(
				lambda: func(*args, **kwargs),
				max_retries=max_retries,
				base_delay=base_delay,
				backoff_factor=backoff_factor,
				max_delay=max_delay,
				retryable_exceptions=retryable_exceptions,
			)

		return wrapper  # type: ignore[return-value]

	return decorator


def _do_retry(
	fn: Callable[[], Any],
	*,
	max_retries: int,
	base_delay: float,
	backoff_factor: float,
	max_delay: float,
	retryable_exceptions: tuple[type[Exception], ...],
) -> Any:
	"""Core retry loop."""
	last_exc: Exception | None = None

	for attempt in range(max_retries + 1):
		try:
			return fn()
		except retryable_exceptions as exc:
			if not is_transient_error(exc):
				raise

			last_exc = exc
			if attempt < max_retries:
				delay = _compute_delay(exc, attempt, base_delay, backoff_factor, max_delay)
				frappe.logger().warning(
					"Retry attempt %d/%d failed with %s: %s. Retrying in %.2fs",
					attempt + 1,
					max_retries,
					type(exc).__name__,
					exc,
					delay,
				)
				time.sleep(delay)

	msg = f"All {max_retries + 1} attempt(s) failed"
	raise MaxRetriesExceededError(
		message=msg,
		original_exception=last_exc,
		attempts=max_retries + 1,
	)


def _compute_delay(
	exc: Exception,
	attempt: int,
	base_delay: float,
	backoff_factor: float,
	max_delay: float,
) -> float:
	"""Compute the delay before the next retry.

	If the exception is a ``RateLimitError`` with a ``retry_after``
	value, that value takes precedence over exponential back-off.
	Otherwise uses exponential back-off with ±20% jitter.
	"""
	if isinstance(exc, RateLimitError) and exc.retry_after is not None:
		return min(exc.retry_after, max_delay)

	# Exponential back-off: base * backoff_factor^attempt
	delay = base_delay * (backoff_factor**attempt)
	delay = min(delay, max_delay)

	# Add ±20% jitter
	jitter = 1.0 + (random.random() - 0.5) * 0.4  # 0.8 .. 1.2
	return delay * jitter


__all__ = [
	"retry",
]
