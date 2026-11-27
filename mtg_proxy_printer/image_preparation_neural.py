"""Inactive, owner-thread neural preparation over current readiness and the M25 cache."""
import threading

from mtg_proxy_printer import image_processing_neural as processor
from mtg_proxy_printer.image_derivative import build_derivative_request, validate_source_plan
from mtg_proxy_printer.image_derivative_cache import DerivativeCache, CachedDerivative, CacheMiss, CacheWriteFailure
from mtg_proxy_printer.image_preparation import PreparedImage, _original, _processed
from mtg_proxy_printer.image_processing_pillow import ProcessingFailure
from mtg_proxy_printer.neural_worker_client import (
    NeuralWorkerConfig, NeuralWorkerSession, NeuralWorkerError, NeuralWorkerCancelled,
)

_OPTIONAL_FAILURES = frozenset(("worker_unavailable", "worker_rejected", "protocol_error",
                                "result_integrity", "timeout", "request_budget"))


def _unresolved(report):
    return report is not None and (report.limitations or not report.reaped or not report.threads_stopped
                                   or not report.temporary_tree_removed)


class NeuralImagePreparer:
    def __init__(self, worker_config: NeuralWorkerConfig, cache: DerivativeCache, cancel_event: threading.Event):
        if not isinstance(worker_config, NeuralWorkerConfig) or not isinstance(cache, DerivativeCache):
            raise TypeError("Explicit NeuralWorkerConfig and DerivativeCache required")
        if not isinstance(cancel_event, threading.Event):
            raise TypeError("Explicit threading.Event required")
        self._config, self._cache, self._event = worker_config, cache, cancel_event
        self._owner = threading.get_ident()
        self._runtime = self._session = self._disabled = None
        self._closed = self._close_complete = self._cancelled = False

    @property
    def cleanup_report(self):
        return self._session.cleanup_report if self._session is not None else None

    def _owner_open(self):
        if threading.get_ident() != self._owner:
            raise RuntimeError("NeuralImagePreparer requires its owner thread")
        if self._closed:
            raise RuntimeError("NeuralImagePreparer is closed")

    def _cancel(self, error):
        if not self._cancelled:
            self._cancelled = True
            if self._session is not None:
                try:
                    self._session.close()
                except Exception as cleanup_error:
                    error.add_note(f"Neural preparation cleanup: {cleanup_error}")
                    error.cleanup_error = cleanup_error
                    error.cleanup_report = getattr(cleanup_error, "cleanup_report", None) or self.cleanup_report
        if error.cleanup_report is None:
            error.cleanup_report = self.cleanup_report
        raise error

    def _check_cancel(self):
        if self._cancelled or self._event.is_set():
            self._cancel(NeuralWorkerCancelled())

    def _accept(self, result):
        self._check_cancel()
        return result

    def _fallback(self, snapshot, plan, reason, detail):
        return self._accept(_original(snapshot, plan, outcome="fallback_original", reason=reason, detail=str(detail)[:4096]))

    def _worker_fallback(self, snapshot, plan, error):
        if error.reason not in _OPTIONAL_FAILURES:
            raise error  # Configuration/state/input defects are not optional unavailability.
        if _unresolved(error.cleanup_report) or _unresolved(self.cleanup_report):
            raise error
        return self._fallback(snapshot, plan, error.reason, error.detail)

    def prepare(self, snapshot, plan) -> PreparedImage:
        self._owner_open()
        self._check_cancel()
        validate_source_plan(snapshot, plan)
        self._check_cancel()
        if plan.sufficient:
            return self._accept(_original(snapshot, plan, reason="source_sufficient"))
        if self._disabled is not None:
            reason, detail, report = self._disabled
            error = NeuralWorkerError(reason, detail)
            error.cleanup_report = report
            return self._worker_fallback(snapshot, plan, error)
        try:
            self._check_cancel()
            if self._runtime is None:
                self._runtime = processor.discover_neural_runtime()
            self._check_cancel()
            if self._session is None:
                self._session = NeuralWorkerSession(self._config, self._event)
            ready = self._session.start()  # Current public health/cancellation verification, even for warm hits.
            self._check_cancel()
            if snapshot.native_dimensions[0] * snapshot.native_dimensions[1] > ready.max_input_pixels:
                return self._fallback(snapshot, plan, "worker_input_limit", "Source exceeds the current worker input-pixel limit")
            request = build_derivative_request(snapshot, plan, self._runtime.recipe(ready))
            self._check_cancel()
            cached = self._cache.lookup(request)
            self._check_cancel()
            if isinstance(cached, CachedDerivative):
                return self._accept(_processed(snapshot, plan, request, cached.png_bytes, cached.dimensions, "cache_hit"))
            if not isinstance(cached, CacheMiss):
                raise TypeError("DerivativeCache.lookup returned an unexpected result")
            data = self._runtime.produce_png(snapshot, request.target_dimensions, self._session, self._check_cancel)
            self._check_cancel()  # Known cancellation here must prevent publication.
            stored = self._cache.store(request, data)
            self._check_cancel()  # A cancellation during synchronous store does not roll back a valid entry.
            if isinstance(stored, CachedDerivative):
                return self._accept(_processed(snapshot, plan, request, stored.png_bytes, stored.dimensions, "generated_cached"))
            if not isinstance(stored, CacheWriteFailure):
                raise TypeError("DerivativeCache.store returned an unexpected result")
            if stored.reason == "write_failed":
                return self._accept(_processed(snapshot, plan, request, data, request.target_dimensions, "generated_uncached",
                                               reason="cache_write_failed", detail=stored.detail[:4096]))
            if stored.reason == "invalid_output":
                return self._fallback(snapshot, plan, "invalid_generated_png", stored.detail)
            raise ValueError("DerivativeCache.store returned an unknown failure reason")
        except NeuralWorkerCancelled as error:
            self._cancel(error)
        except ProcessingFailure as error:
            return self._fallback(snapshot, plan, error.reason, error.detail)
        except NeuralWorkerError as error:
            report = error.cleanup_report or self.cleanup_report
            # M34 publishes cleanup information after disposal, unlike an idle recoverable rejection.
            if error.reason in _OPTIONAL_FAILURES and (error.reason != "worker_rejected" or report is not None):
                error.cleanup_report = report
                # Retain no exception traceback (which could own per-image buffers).
                self._disabled = (error.reason, error.detail, error.cleanup_report)
                if _unresolved(error.cleanup_report) or _unresolved(self.cleanup_report):
                    raise
                try:
                    if self._session is not None:
                        self._session.close()
                except NeuralWorkerCancelled as cancellation:
                    self._cancel(cancellation)
                except NeuralWorkerError as cleanup_error:
                    self._disabled = (cleanup_error.reason, cleanup_error.detail, cleanup_error.cleanup_report)
                    raise
            return self._worker_fallback(snapshot, plan, error)

    def close(self):
        if threading.get_ident() != self._owner:
            raise RuntimeError("NeuralImagePreparer requires its owner thread")
        if self._close_complete:
            return
        self._closed = True
        if self._session is not None:
            self._session.close()
        self._close_complete = True

    def __enter__(self):
        self._owner_open()
        return self  # Lazy: no discovery, process, cache or application storage access.

    def __exit__(self, exc_type, exc, traceback):
        try:
            self.close()
        except Exception as cleanup_error:
            if exc is None:
                raise
            exc.add_note(f"Neural preparation cleanup: {cleanup_error}")
            if isinstance(exc, NeuralWorkerError):
                exc.cleanup_error = cleanup_error
                exc.cleanup_report = getattr(cleanup_error, "cleanup_report", None) or self.cleanup_report
        return False
