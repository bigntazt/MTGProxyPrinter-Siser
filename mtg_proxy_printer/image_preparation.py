"""Synchronous single-image preparation, inactive until explicitly adopted by a caller."""
from dataclasses import dataclass, field
from typing import Literal

from mtg_proxy_printer import image_processing_pillow as processor
from mtg_proxy_printer.image_derivative import build_derivative_request, validate_source_plan
from mtg_proxy_printer.image_derivative_cache import DerivativeCache, CachedDerivative, CacheMiss, CacheWriteFailure
from mtg_proxy_printer.image_source import SourceIdentity, SourceSnapshot, ResolutionPlan


@dataclass(frozen=True)
class PreparedImage:
    source_identity: SourceIdentity
    canonical_dimensions: tuple[int, int]
    encoded_bytes: bytes = field(repr=False, compare=False)
    image_format: str
    actual_dimensions: tuple[int, int]
    target_ppi: int
    target_dimensions: tuple[int, int]
    outcome: Literal["original", "cache_hit", "generated_cached", "generated_uncached", "fallback_original"]
    derivative_key: str | None = None
    reason: str | None = None
    detail: str = ""


def _original(snapshot, plan, *, outcome="original", reason=None, detail="") -> PreparedImage:
    return PreparedImage(snapshot.identity, tuple(snapshot.canonical_dimensions), snapshot.encoded_bytes,
                         snapshot.image_format, tuple(snapshot.native_dimensions), plan.target_ppi,
                         tuple(plan.target_dimensions), outcome, reason=reason, detail=detail)


def _processed(snapshot, plan, request, data, dimensions, outcome, *, reason=None, detail="") -> PreparedImage:
    return PreparedImage(snapshot.identity, tuple(snapshot.canonical_dimensions), data, "png", tuple(dimensions),
                         plan.target_ppi, tuple(plan.target_dimensions), outcome, request.key, reason, detail)


def prepare_image(snapshot: SourceSnapshot, plan: ResolutionPlan, *,
                  mode: Literal["original", "lanczos"], cache: DerivativeCache) -> PreparedImage:
    if type(mode) is not str or mode not in ("original", "lanczos"):
        raise ValueError("Image preparation mode must be original or lanczos")
    if not isinstance(cache, DerivativeCache):
        raise TypeError("An explicit DerivativeCache is required")
    if mode == "original":
        validate_source_plan(snapshot, plan)
        return _original(snapshot, plan)
    try:
        runtime = processor.discover_pillow_runtime()
    except processor.ProcessingFailure as error:
        validate_source_plan(snapshot, plan)  # Invalid input is never accepted as a fallback.
        return _original(snapshot, plan, outcome="fallback_original", reason=error.reason, detail=error.detail)
    request = build_derivative_request(snapshot, plan, runtime.recipe())  # Shared validation, one source hash.
    cached = cache.lookup(request)
    if isinstance(cached, CachedDerivative):
        return _processed(snapshot, plan, request, cached.png_bytes, cached.dimensions, "cache_hit")
    if not isinstance(cached, CacheMiss):
        raise TypeError("DerivativeCache.lookup returned an unexpected result")
    try:
        png = runtime.produce_png(snapshot, request.target_dimensions)
    except processor.ProcessingFailure as error:
        return _original(snapshot, plan, outcome="fallback_original", reason=error.reason, detail=error.detail)
    stored = cache.store(request, png)  # M25 validates PNG fully before any attempted persistence.
    if isinstance(stored, CachedDerivative):
        return _processed(snapshot, plan, request, stored.png_bytes, stored.dimensions, "generated_cached")
    if not isinstance(stored, CacheWriteFailure):
        raise TypeError("DerivativeCache.store returned an unexpected result")
    if stored.reason == "write_failed":
        return _processed(snapshot, plan, request, png, request.target_dimensions, "generated_uncached",
                          reason="cache_write_failed", detail=stored.detail)
    if stored.reason == "invalid_output":
        return _original(snapshot, plan, outcome="fallback_original", reason="invalid_generated_png", detail=stored.detail)
    raise ValueError("DerivativeCache.store returned an unknown failure reason")
