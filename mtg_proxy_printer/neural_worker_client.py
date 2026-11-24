"""Explicit, standard-library host for the optional sequential RGB8 worker.

No application initialization, image decoding, target resizing or fallback policy.
"""
from dataclasses import dataclass
import hashlib
import json
import math
import os
from pathlib import Path
import queue
import re
import shutil
import stat
import subprocess
import tempfile
import threading
import time
import uuid

MAX_RESPONSE_BYTES = 64 * 1024
MAX_DIAGNOSTIC_BYTES = 64 * 1024
RESPONSE_QUEUE_CAPACITY = 4
POLL_SECONDS = 0.05
REFERENCE_SHA256 = "dec25af2672783ce2214c38d6a70caf8c3d8a640d6f4d87b543b264cafd74110"
_HEX64 = re.compile(r"[0-9a-f]{64}\Z")
_HEX32 = re.compile(r"[0-9a-f]{32}\Z")
_MODEL = {"id": "nomos_compact_2x", "sha256": REFERENCE_SHA256, "bytes": 2406816,
          "architecture": "Compact", "scale": 2, "input_channels": 3, "output_channels": 3}
_SOURCES = ("worker.py", "protocol.py", "backend.py", "reference-model.json")


class NeuralWorkerError(RuntimeError):
    """Stable reason plus bounded detail/diagnostics; no Original fallback."""
    def __init__(self, reason, detail):
        self.reason, self.detail = reason, str(detail)[:4096]
        self.diagnostics = ""
        self.cleanup_report = None
        super().__init__(f"{reason}: {self.detail}")


class NeuralWorkerCancelled(NeuralWorkerError):
    def __init__(self):
        super().__init__("cancelled", "Neural worker operation cancelled")


@dataclass(frozen=True)
class NeuralWorkerConfig:
    interpreter: Path
    worker_script: Path
    checkpoint: Path
    expected_sha256: str
    model_id: str
    device: str
    private_parent: Path
    startup_timeout: float = 120
    inference_timeout: float = 300
    shutdown_timeout: float = 5
    terminate_timeout: float = 2
    kill_timeout: float = 5

    def __post_init__(self):
        for name in ("interpreter", "worker_script", "checkpoint", "private_parent"):
            try:
                path = Path(getattr(self, name))
            except TypeError as error:
                raise NeuralWorkerError("invalid_config", f"{name} must be an absolute path") from error
            if not path.is_absolute():
                raise NeuralWorkerError("invalid_config", f"{name} must be an absolute path")
            object.__setattr__(self, name, path)
        if (self.expected_sha256 != REFERENCE_SHA256 or self.model_id != _MODEL["id"]
                or self.device != "cuda:0" or self.worker_script.name != "worker.py"):
            raise NeuralWorkerError("invalid_config", "Require explicit pinned Compact worker.py on cuda:0")
        for name in ("startup_timeout", "inference_timeout", "shutdown_timeout", "terminate_timeout", "kill_timeout"):
            value = getattr(self, name)
            if type(value) not in (int, float) or not math.isfinite(value) or value <= 0:
                raise NeuralWorkerError("invalid_config", f"{name} must be positive and finite")


@dataclass(frozen=True)
class NeuralWorkerReady:
    session_token: str
    fingerprint: str
    provenance_json: bytes  # Canonical immutable JSON, never an internally shared dictionary.
    max_input_pixels: int
    max_requests: int
    max_request_line_bytes: int
    timings: tuple[tuple[str, float], ...]


@dataclass(frozen=True)
class NativeRGBResult:
    rgb_bytes: bytes
    width: int
    height: int
    input_sha256: str
    output_sha256: str
    fingerprint: str
    provenance_json: bytes
    session_token: str
    request_id: str
    timings: tuple[tuple[str, float], ...]


@dataclass(frozen=True)
class NeuralWorkerCleanup:
    pid: int | None
    returncode: int | None
    reaped: bool
    threads_stopped: bool
    temporary_tree_removed: bool
    limitations: tuple[str, ...] = ()


def _hash(data):
    return hashlib.sha256(data).hexdigest()


def _canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")


def _require(condition, detail):
    if not condition:
        raise NeuralWorkerError("protocol_error", detail)


def _exact(actual, expected):
    # Equality alone accepts True == 1; recursively require JSON types too.
    if type(actual) is not type(expected):
        return False
    if type(expected) is dict:
        return set(actual) == set(expected) and all(_exact(actual[k], v) for k, v in expected.items())
    return actual == expected


def _text(value, maximum=256):
    return type(value) is str and 0 < len(value) <= maximum


def _integer(value, low, high):
    return type(value) is int and low <= value <= high


def _parse(data):
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError("Duplicate JSON key")
            result[key] = value
        return result

    def finite(value):
        if type(value) is float and not math.isfinite(value):
            raise ValueError("Nonfinite JSON")
        if type(value) is dict:
            for item in value.values(): finite(item)
        elif type(value) is list:
            for item in value: finite(item)

    try:
        record = json.loads(data.decode("utf-8"), object_pairs_hook=pairs,
                            parse_constant=lambda _: (_ for _ in ()).throw(ValueError("Nonfinite JSON")))
        finite(record)
    except (ValueError, UnicodeError, RecursionError) as error:
        raise NeuralWorkerError("protocol_error", "Expected strict UTF-8 JSON without duplicate keys or nonfinite values") from error
    _require(type(record) is dict and type(record.get("protocol")) is int and record["protocol"] == 1,
             "Unsupported protocol record/version")
    return record


def _timings(record, keys):
    _require(type(record) is dict and set(record) == set(keys), "Unexpected timing fields")
    _require(all(type(v) in (int, float) and math.isfinite(v) and v >= 0 for v in record.values()),
             "Timings must be finite nonnegative numbers")
    return tuple(sorted((k, float(v)) for k, v in record.items()))


def _validate_ready(record, hashes, config):
    _require(set(record) == {"protocol", "type", "session", "fingerprint", "provenance", "capabilities", "timings"}
             and record["type"] == "ready", "Expected one ready record")
    _require(type(record["session"]) is str and _HEX32.fullmatch(record["session"]), "Invalid session token")
    _require(type(record["fingerprint"]) is str and _HEX64.fullmatch(record["fingerprint"]), "Invalid fingerprint")
    provenance = record["provenance"]
    _require(type(provenance) is dict and set(provenance) == {
        "implementation", "protocol", "source_sha256", "model", "runtime"}, "Invalid provenance structure")
    _require(provenance["implementation"] == "m33-neural-worker-v1"
             and type(provenance["protocol"]) is int and provenance["protocol"] == 1, "Unknown worker implementation")
    _require(_exact(provenance["source_sha256"], hashes), "Launched worker bundle source hashes differ")
    _require(_exact(provenance["model"], _MODEL), "Reference model identity differs")
    _require(provenance["model"]["sha256"] == config.expected_sha256
             and provenance["model"]["id"] == config.model_id, "Configured model differs")
    runtime = provenance["runtime"]
    _require(type(runtime) is dict and set(runtime) == {"packages", "python", "device", "device_name",
        "device_capability", "cuda_runtime", "cudnn", "driver", "architecture", "scale", "input_channels",
        "output_channels", "settings"}, "Incomplete runtime identity")
    for key in ("python", "device_name", "cuda_runtime"):
        _require(_text(runtime[key]), f"Invalid runtime {key}")
    _require(runtime["device"] == config.device and _integer(runtime["cudnn"], 1, 2**31-1), "Invalid CUDA runtime")
    _require(runtime["driver"] is None or _text(runtime["driver"]), "Invalid driver identity")
    capability = runtime["device_capability"]
    _require(type(capability) is list and len(capability) == 2
             and all(_integer(v, 0, 999) for v in capability), "Invalid device capability")
    for key in ("architecture", "scale", "input_channels", "output_channels"):
        _require(_exact(runtime[key], _MODEL[key]), f"Runtime {key} differs")
    packages = runtime["packages"]
    _require(type(packages) is dict and set(packages) == {
        "torch", "torchvision", "spandrel", "numpy", "safetensors", "einops", "packaging"}
        and all(_text(v, 128) for v in packages.values()), "Missing actual package versions")
    settings = {"precision": "float32", "seed": 2200, "torch_threads": 4, "cudnn_benchmark": False,
                "cudnn_deterministic": True, "matmul_tf32": False, "cudnn_tf32": False,
                "deterministic_algorithms": False, "eval": True, "inference_mode": True,
                "passes": 1, "tiling": None, "precision_fallback": False,
                "quantization": "round-nearest-ties-even-rgb8-v1"}
    _require(_exact(runtime["settings"], settings), "Unsupported CUDA FP32 inference recipe")
    canonical = _canonical(provenance)
    _require(_hash(canonical) == record["fingerprint"], "Provenance fingerprint mismatch")
    caps = record["capabilities"]
    _require(type(caps) is dict and set(caps) == {"format", "layout", "scale", "max_input_pixels", "max_requests",
                                               "max_line_bytes", "sequential", "cancel", "shutdown"}, "Invalid capabilities")
    for key, value in {"format": "RGB8", "layout": "tightly-packed-row-major", "scale": 2,
                       "sequential": True, "cancel": False, "shutdown": "between-jobs"}.items():
        _require(_exact(caps[key], value), f"Unsupported capability {key}")
    _require(_integer(caps["max_input_pixels"], 1, 4_194_304)
             and _integer(caps["max_requests"], 2, 4096)
             and _integer(caps["max_line_bytes"], 256, 8192), "Invalid bounded worker limits")
    return NeuralWorkerReady(record["session"], record["fingerprint"], canonical,
                             caps["max_input_pixels"], caps["max_requests"], caps["max_line_bytes"],
                             _timings(record["timings"], ("startup_seconds", "model_load_seconds")))


class NeuralWorkerSession:
    """Single owner thread. Only cancellation Event.set() is cross-thread API."""
    def __init__(self, config, cancel_event=None):
        if not isinstance(config, NeuralWorkerConfig):
            raise NeuralWorkerError("invalid_config", "Explicit NeuralWorkerConfig required")
        if cancel_event is not None and not isinstance(cancel_event, threading.Event):
            raise NeuralWorkerError("invalid_config", "Cancellation must be threading.Event")
        self._config = config
        self._cancel = cancel_event if cancel_event is not None else threading.Event()
        self._owner = threading.get_ident()
        self._state = "new"
        self._process = self._root = self._session = self._private_parent = None
        self._readers = []
        self._writer = None
        self._records = queue.Queue(RESPONSE_QUEUE_CAPACITY)
        self._wake = threading.Event()
        self._stdout_eof = threading.Event()
        self._lock = threading.Lock()
        self._fault = None
        self._tail = bytearray()
        self._used = 0
        self._ready = None
        self.cleanup_report = None

    @property
    def config(self):
        return self._config

    @property
    def ready(self):
        return self._ready

    @property
    def diagnostics(self):
        with self._lock:
            return bytes(self._tail).decode("utf-8", errors="replace")

    def _check_owner(self):
        if threading.get_ident() != self._owner:
            raise NeuralWorkerError("wrong_thread", "start/infer/close require the constructing owner thread")

    def _check_cancel(self):
        if self._cancel.is_set():
            raise NeuralWorkerCancelled()

    def _faulted(self, detail):
        with self._lock:
            if self._fault is None: self._fault = detail
        self._wake.set()

    def _check_traffic(self):
        with self._lock: fault = self._fault
        if fault:
            raise NeuralWorkerError("protocol_error", fault)

    def _read_stdout(self):
        pending = bytearray()
        try:
            while chunk := self._process.stdout.read(4096):
                pending.extend(chunk)
                while b"\n" in pending:
                    line, _, remainder = pending.partition(b"\n")
                    if len(line) + 1 > MAX_RESPONSE_BYTES:
                        self._faulted("Oversized response record"); return
                    try: self._records.put_nowait(bytes(line))
                    except queue.Full:
                        self._faulted("Response queue overflow"); return
                    pending = bytearray(remainder)
                    self._wake.set()
                if len(pending) >= MAX_RESPONSE_BYTES:
                    self._faulted("Oversized partial response"); return
            if pending:
                self._faulted("Truncated JSON-lines response")
        except (OSError, ValueError) as error:
            self._faulted(f"Response pipe failed: {str(error)[:256]}")
        finally:
            self._stdout_eof.set(); self._wake.set()

    def _drain_stderr(self):
        try:
            while chunk := self._process.stderr.read(4096):
                with self._lock:
                    self._tail.extend(chunk)
                    if len(self._tail) > MAX_DIAGNOSTIC_BYTES:
                        del self._tail[:-MAX_DIAGNOSTIC_BYTES]
        except (OSError, ValueError):
            pass  # Closing pipes during resolved process teardown is expected.

    def _pause(self, deadline):
        self._check_cancel()
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise NeuralWorkerError("timeout", "Operation deadline expired")
        self._wake.wait(min(POLL_SECONDS, remaining)); self._wake.clear()

    def _receive(self, deadline):
        while True:
            self._check_cancel(); self._check_traffic()
            if time.monotonic() >= deadline:
                raise NeuralWorkerError("timeout", "Response deadline expired")
            try: return _parse(self._records.get_nowait())
            except queue.Empty: pass
            if self._stdout_eof.is_set():
                raise NeuralWorkerError("worker_unavailable", "Unexpected worker EOF")
            self._pause(deadline)

    def _send(self, record, deadline):
        data = _canonical(record) + b"\n"
        _require(len(data) <= self.ready.max_request_line_bytes, "Request exceeds advertised line limit")
        done, failure = threading.Event(), []
        def write():
            try:
                remaining = memoryview(data)
                while remaining:
                    count = self._process.stdin.write(remaining)
                    if not count: raise OSError("Short request write")
                    remaining = remaining[count:]
                self._process.stdin.flush()
            except (OSError, ValueError) as error:
                failure.append(str(error)[:1024])
            finally: done.set(); self._wake.set()
        self._writer = threading.Thread(target=write, name="neural-request-writer", daemon=False)
        self._writer.start()
        while not done.is_set():
            self._check_traffic(); self._pause(deadline)
        self._check_cancel()
        self._writer.join(max(0, deadline - time.monotonic()))
        if self._writer.is_alive():
            raise NeuralWorkerError("timeout", "Request writer did not finish before deadline")
        if time.monotonic() >= deadline:
            raise NeuralWorkerError("timeout", "Request write deadline expired")
        if failure:
            raise NeuralWorkerError("worker_unavailable", "Request write failed: " + failure[0])

    def _worker_failure(self, record, identifier=None):
        kind = record.get("type")
        if kind not in ("fatal", "request_error"): return
        expected = {"protocol", "type", "id", "code", "detail"}
        if "session" in record: expected.add("session")
        _require(set(record) == expected and _text(record.get("code"), 128)
                 and type(record.get("detail")) is str, "Malformed worker error")
        if self.ready is not None:
            _require(record.get("session") == self.ready.session_token and record.get("id") == identifier,
                     "Worker error does not match current session/request")
        else:
            _require(kind == "fatal" and record.get("id") is None, "Unexpected startup error")
        error = NeuralWorkerError("worker_rejected" if kind == "request_error" else "worker_unavailable",
                                  f"{record['code']}: {record['detail']}")
        error.diagnostics = self.diagnostics
        raise error

    def _quiet(self):
        self._check_traffic()
        _require(self._records.empty(), "Unsolicited/duplicate response while idle")
        if self._stdout_eof.is_set() or self._process.poll() is not None:
            raise NeuralWorkerError("worker_unavailable", "Worker exited while session was idle")

    def start(self):
        self._check_owner()
        if self._state == "idle": return self.ready
        if self._state != "new":
            raise NeuralWorkerError("session_state", "Session cannot be started/restarted")
        self._state = "starting"
        deadline = time.monotonic() + self.config.startup_timeout
        try:
            self._check_cancel()
            script = self.config.worker_script
            hashes = {name: _hash((script if name == "worker.py" else script.parent / name).read_bytes()) for name in _SOURCES}
            self._private_parent = self.config.private_parent.resolve(strict=True)
            self._root = Path(tempfile.mkdtemp(prefix="neural-worker-", dir=self._private_parent))
            self._session = self._root / "session"  # Deliberately nonexistent until the worker creates it.
            command = [str(self.config.interpreter), "-B", str(script), "--checkpoint", str(self.config.checkpoint),
                       "--expected-sha256", self.config.expected_sha256, "--model-id", self.config.model_id,
                       "--device", self.config.device, "--session-dir", str(self._session)]
            environment = os.environ.copy()
            for name in ("PYTHONPATH", "PYTHONHOME"): environment.pop(name, None)
            environment["PYTHONDONTWRITEBYTECODE"] = "1"
            environment["TORCH_HOME"] = str(self._root / "torch-cache")
            environment["CUDA_CACHE_PATH"] = str(self._root / "cuda-cache")
            self._process = subprocess.Popen(command, shell=False, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                             stderr=subprocess.PIPE, bufsize=0, env=environment)
            for name, function in (("neural-stdout-reader", self._read_stdout), ("neural-stderr-drainer", self._drain_stderr)):
                thread = threading.Thread(target=function, name=name, daemon=False)
                self._readers.append(thread); thread.start()
            record = self._receive(deadline)
            self._worker_failure(record)
            self._ready = _validate_ready(record, hashes, self.config)
            self._check_cancel(); self._quiet()
            self._state = "idle"
            return self.ready
        except Exception as error:
            self._fail(error if isinstance(error, NeuralWorkerError) else NeuralWorkerError("worker_unavailable", error))

    def infer(self, rgb_bytes, width, height):
        self._check_owner()
        if self._state != "idle":
            raise NeuralWorkerError("session_state", "Start a usable idle session before inference")
        if (type(rgb_bytes) is not bytes or any(type(v) is not int or v <= 0 for v in (width, height))
                or width * height > self.ready.max_input_pixels or len(rgb_bytes) != width * height * 3):
            raise NeuralWorkerError("invalid_input", "Require immutable exact-sized RGB8 bytes within worker pixel limit")
        if self._used >= self.ready.max_requests - 1:
            raise NeuralWorkerError("request_budget", "Session request budget exhausted; shutdown capacity is reserved")
        self._state = "busy"
        deadline = time.monotonic() + self.config.inference_timeout
        identifier, digest = uuid.uuid4().hex, _hash(rgb_bytes)
        input_path = self._session / "input" / f"job-{identifier}.rgb8"
        output_path = self._session / "output" / f"job-{identifier}.rgb8"
        try:
            self._check_cancel(); self._quiet()
            with input_path.open("xb") as stream:
                if stream.write(rgb_bytes) != len(rgb_bytes): raise OSError("Short RGB8 input write")
            self._used += 1
            self._send({"protocol": 1, "type": "infer", "id": identifier, "width": width,
                        "height": height, "sha256": digest}, deadline)
            record = self._receive(deadline)
            try:
                self._worker_failure(record, identifier)
            except NeuralWorkerError as error:
                if error.reason == "worker_rejected":
                    try:
                        input_path.unlink(missing_ok=True); output_path.unlink(missing_ok=True)
                    except OSError as cleanup_error:
                        error.add_note(f"Rejected-job file cleanup: {str(cleanup_error)[:512]}")
                        raise error  # Abort the session; preserve the primary rejection.
                    self._state = "idle"
                    raise
                raise
            _require(set(record) == {"protocol", "type", "session", "id", "fingerprint", "input", "output", "timings"}
                     and record["type"] == "result", "Expected matching result record")
            _require(record["session"] == self.ready.session_token and record["id"] == identifier
                     and record["fingerprint"] == self.ready.fingerprint, "Result session/request/fingerprint differs")
            _require(_exact(record["input"], {"sha256": digest, "width": width, "height": height}), "Input echo differs")
            expected_size = width * height * 12
            output = record["output"]
            _require(type(output) is dict and set(output) == {"sha256", "width", "height", "scale", "format", "bytes", "file"},
                     "Invalid output metadata")
            for key, value in {"width": width * 2, "height": height * 2, "scale": 2, "format": "RGB8",
                               "bytes": expected_size, "file": f"output/job-{identifier}.rgb8"}.items():
                _require(_exact(output[key], value), f"Output {key} differs")
            _require(type(output["sha256"]) is str and _HEX64.fullmatch(output["sha256"]), "Invalid output digest")
            output_directory = self._session / "output"
            _require(self._root.resolve() == self._root and self._session.resolve() == self._session
                     and output_directory.resolve() == output_directory
                     and not output_path.is_symlink() and output_path.resolve() == output_path,
                     "Output is not an ordinary owned-session file")
            with output_path.open("rb") as stream:
                info = os.fstat(stream.fileno())
                _require(stat.S_ISREG(info.st_mode) and info.st_size == expected_size, "Output file length/type differs")
                data = stream.read(expected_size + 1)
            _require(len(data) == expected_size and _hash(data) == output["sha256"], "Output content length/digest differs")
            timings = _timings(record["timings"], ("inference_seconds", "quantization_seconds", "publication_seconds"))
            self._check_cancel(); self._check_traffic()
            try:
                input_path.unlink(); output_path.unlink()
            except OSError as error:
                raise NeuralWorkerError("cleanup_failed", f"Cannot release validated job files: {error}") from error
            self._state = "idle"
            result = NativeRGBResult(data, width * 2, height * 2, digest, output["sha256"], self.ready.fingerprint,
                                     self.ready.provenance_json, self.ready.session_token, identifier, timings)
            self._check_cancel()  # Last acceptance boundary, after owning validated bytes and releasing files.
            return result
        except NeuralWorkerError as error:
            if error.reason == "worker_rejected" and self._state == "idle": raise
            self._fail(error)
        except Exception as error:
            self._fail(NeuralWorkerError("result_integrity", error))

    def _wait_exit(self, deadline, cancellable=True):
        while True:
            if cancellable: self._check_cancel()
            remaining = deadline - time.monotonic()
            if remaining <= 0: raise NeuralWorkerError("timeout", "Worker exit deadline expired")
            try: return self._process.wait(timeout=min(POLL_SECONDS, remaining))
            except subprocess.TimeoutExpired: pass

    def _dispose(self):
        limitations = []
        process = self._process
        if process is not None and process.poll() is None:
            try:
                process.terminate()
                try: self._wait_exit(time.monotonic() + self.config.terminate_timeout, False)
                except NeuralWorkerError:
                    process.kill()
                    self._wait_exit(time.monotonic() + self.config.kill_timeout, False)
            except (OSError, NeuralWorkerError) as error:
                limitations.append(f"Direct child teardown: {str(error)[:512]}")
        reaped = process is None or process.poll() is not None
        if reaped and process is not None:
            for stream in (process.stdin, process.stdout, process.stderr):
                try: stream.close()
                except (OSError, ValueError) as error: limitations.append(str(error)[:512])
        deadline = time.monotonic() + self.config.kill_timeout
        threads = self._readers + ([self._writer] if self._writer is not None else [])
        for thread in threads:
            if thread.ident is not None:
                thread.join(max(0, deadline - time.monotonic()))
        stopped = not any(thread.is_alive() for thread in threads)
        if not stopped: limitations.append("Owned I/O threads did not all end; temporary tree retained")
        removed = self._root is None
        if self._root is not None and reaped and stopped:
            try:
                if (self._root.is_symlink() or self._root.parent.resolve() != self._private_parent
                        or not self._root.name.startswith("neural-worker-")):
                    raise OSError("Owned temporary tree identity changed; refusing removal")
                if self._root.exists(): shutil.rmtree(self._root)
                removed = not self._root.exists()
            except OSError as error: limitations.append(f"Temporary cleanup: {str(error)[:512]}")
        self.cleanup_report = NeuralWorkerCleanup(process.pid if process else None,
            process.returncode if process else None, reaped, stopped, removed, tuple(limitations))
        return self.cleanup_report

    def _fail(self, error):
        self._state = "failed"
        report = self._dispose()
        error.diagnostics = self.diagnostics
        error.cleanup_report = report
        if report.limitations: error.add_note("Cleanup limitations: " + "; ".join(report.limitations))
        raise error

    def close(self):
        self._check_owner()
        if self._state == "closed": return
        primary = None
        if self._state == "idle":
            try:
                self._check_cancel(); self._quiet()
                deadline = time.monotonic() + self.config.shutdown_timeout
                identifier = uuid.uuid4().hex
                self._send({"protocol": 1, "type": "shutdown", "id": identifier}, deadline)
                record = self._receive(deadline)
                self._worker_failure(record, identifier)
                _require(_exact(record, {"protocol": 1, "type": "bye", "session": self.ready.session_token,
                                        "id": identifier, "reason": "shutdown"}), "Shutdown acknowledgement differs")
                _require(self._wait_exit(deadline) == 0, "Worker exited abnormally after shutdown")
                self._check_traffic()
                _require(self._records.empty(), "Unexpected record after shutdown")
            except Exception as error:
                primary = error if isinstance(error, NeuralWorkerError) else NeuralWorkerError("worker_unavailable", error)
        report = self._dispose()
        self._state = "failed" if report.limitations else "closed"
        if primary:
            primary.diagnostics = self.diagnostics; primary.cleanup_report = report
            if report.limitations: primary.add_note("Cleanup limitations: " + "; ".join(report.limitations))
            raise primary
        if report.limitations:
            error = NeuralWorkerError("cleanup_failed", "; ".join(report.limitations))
            error.cleanup_report = report; error.diagnostics = self.diagnostics
            raise error

    def __enter__(self):
        self.start(); return self

    def __exit__(self, exc_type, exc, traceback):
        try: self.close()
        except NeuralWorkerError as cleanup_error:
            if exc is None: raise
            exc.add_note(f"Neural worker close: {cleanup_error}")
        return False
