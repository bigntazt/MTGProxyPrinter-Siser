"""Inactive completed-PNG cache. Manifest-last publication; published blobs stay."""
from dataclasses import dataclass, field
import hashlib
from pathlib import Path
from typing import Literal

from PySide6.QtCore import QBuffer, QIODevice, QSaveFile
from PySide6.QtGui import QImageReader

from mtg_proxy_printer.image_derivative import (
    DerivativeRequest, _canonical_json, _load_json, _fields, _version, _digest, _dimensions,
)


@dataclass(frozen=True)
class CachedDerivative:
    request_key: str
    png_sha256: str
    dimensions: tuple[int, int]
    png_bytes: bytes = field(repr=False, compare=False)


@dataclass(frozen=True)
class CacheMiss:
    reason: Literal["not_found", "invalid_entry", "io_error"]
    detail: str


@dataclass(frozen=True)
class CacheWriteFailure:
    reason: Literal["invalid_output", "write_failed"]
    detail: str


def _read_bytes(path: Path) -> bytes:
    return path.read_bytes()  # Open/read/close before any further operation.


def _write_atomic(path: Path, data: bytes) -> None:
    """Local equivalent of the accepted QSaveFile prepared-output pattern."""
    output = QSaveFile(str(path))
    output.setDirectWriteFallback(False)
    committed = False
    try:
        if not output.open(QIODevice.OpenModeFlag.WriteOnly):
            raise OSError(output.errorString())
        written = output.write(data)
        if written != len(data):
            raise OSError(f"Incomplete write ({written}/{len(data)} bytes): {output.errorString()}")
        if not output.commit():
            raise OSError(output.errorString())
        committed = True
    finally:
        if not committed:
            output.cancelWriting()
        del output


def _validate_png(data: bytes, target: tuple[int, int], *, digest=None, size=None):
    if size is not None and len(data) != size:
        raise ValueError("PNG byte count differs from the manifest")
    actual_digest = hashlib.sha256(data).hexdigest()
    if digest is not None and actual_digest != digest:
        raise ValueError("PNG digest differs from the manifest")
    buffer = QBuffer()
    buffer.setData(data)
    if not buffer.open(QIODevice.OpenModeFlag.ReadOnly):
        raise RuntimeError("Could not open the in-memory PNG buffer")
    reader = QImageReader(buffer)
    reader.setAutoDetectImageFormat(True)
    reader.setAutoTransform(False)
    if bytes(reader.format()) != b"png":
        raise ValueError("Output must be an encoded PNG")
    image = reader.read()
    if image.isNull():
        raise ValueError("PNG decode failed: " + reader.errorString())
    dimensions = image.width(), image.height()
    if dimensions != target:
        raise ValueError("Decoded PNG dimensions differ from the requested target")
    return actual_digest, dimensions


def _manifest_output(data: bytes, request: DerivativeRequest):
    manifest = _load_json(data)
    _fields(manifest, ("schema_version", "request_key", "request", "output"))
    _version(manifest["schema_version"])
    _digest(manifest["request_key"])
    descriptor = _canonical_json(manifest["request"])
    if (descriptor != request.canonical_bytes or hashlib.sha256(descriptor).hexdigest() != request.key
            or manifest["request_key"] != request.key):
        raise ValueError("Manifest request does not match the independently supplied request")
    output = manifest["output"]
    _fields(output, ("sha256", "size_bytes", "dimensions", "format"))
    _digest(output["sha256"])
    if type(output["size_bytes"]) is not int or output["size_bytes"] <= 0:
        raise ValueError("PNG byte count must be a positive integer")
    dimensions = _dimensions(output["dimensions"])
    if dimensions != request.target_dimensions or output["format"] != "png":
        raise ValueError("Manifest output must declare a target-sized PNG")
    return output["sha256"], output["size_bytes"], dimensions


@dataclass(frozen=True)
class DerivativeCache:
    root: Path

    def __post_init__(self):
        root = Path(self.root)
        if not root.is_absolute():
            raise ValueError("Derivative cache root must be explicitly absolute")
        object.__setattr__(self, "root", root)

    def _entry(self, request: DerivativeRequest) -> Path:
        if not isinstance(request, DerivativeRequest):
            raise TypeError("Expected a DerivativeRequest built from current source/settings")
        key = request.key
        _digest(key)
        return self.root / "v1" / key[:2] / key

    def lookup(self, request: DerivativeRequest) -> CachedDerivative | CacheMiss:
        entry = self._entry(request)
        try:
            manifest_bytes = _read_bytes(entry / "manifest.json")
        except FileNotFoundError:
            return CacheMiss("not_found", "No committed manifest")
        except OSError as error:
            return CacheMiss("io_error", str(error))
        try:
            digest, size, dimensions = _manifest_output(manifest_bytes, request)
        except (ValueError, TypeError, RecursionError) as error:
            return CacheMiss("invalid_entry", str(error))
        # The blob name is derived only from a validated digest, never a manifest path.
        try:
            png_bytes = _read_bytes(entry / (digest + ".png"))
        except FileNotFoundError:
            return CacheMiss("invalid_entry", "Committed manifest references a missing PNG")
        except OSError as error:
            return CacheMiss("io_error", str(error))
        try:
            _validate_png(png_bytes, dimensions, digest=digest, size=size)
        except ValueError as error:
            return CacheMiss("invalid_entry", str(error))
        return CachedDerivative(request.key, digest, dimensions, png_bytes)

    def store(self, request: DerivativeRequest, png_bytes: bytes) -> CachedDerivative | CacheWriteFailure:
        entry = self._entry(request)
        if type(png_bytes) is not bytes:
            raise TypeError("PNG output must be immutable bytes")
        try:
            digest, dimensions = _validate_png(png_bytes, request.target_dimensions)
        except ValueError as error:
            return CacheWriteFailure("invalid_output", str(error))
        manifest = {
            "schema_version": 1, "request_key": request.key, "request": request.to_spec(),
            "output": {"sha256": digest, "size_bytes": len(png_bytes),
                       "dimensions": list(dimensions), "format": "png"},
        }
        manifest_bytes = _canonical_json(manifest)
        try:
            entry.mkdir(parents=True, exist_ok=True)
            _write_atomic(entry / (digest + ".png"), png_bytes)
            _write_atomic(entry / "manifest.json", manifest_bytes)  # Commit point, last writer wins.
        except OSError as error:
            # Never remove completed blobs: an existing/concurrent reader may need them.
            return CacheWriteFailure("write_failed", str(error))
        return CachedDerivative(request.key, digest, dimensions, png_bytes)
