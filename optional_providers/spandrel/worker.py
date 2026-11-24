"""Separately invoked sequential worker; stdout contains only JSONL records."""
import argparse
import contextlib
import json
import os
from pathlib import Path
import sys
from time import perf_counter
import uuid

from protocol import (VERSION, IMPLEMENTATION, MAX_PIXELS, MAX_LINE_BYTES, MAX_REQUESTS,
                      Rejected, canonical, parse_line, request_id, validate_infer,
                      read_samples, sample_name, sha256)
from backend import Fatal, TorchBackend


def load_checkpoint(path, expected, reference):
    if expected != reference["sha256"]:
        raise Fatal("checkpoint_pin", "Expected digest must match the explicit reference configuration")
    # Exactly one bounded read; these immutable verified bytes are passed to load().
    with Path(path).open("rb") as stream:
        payload = stream.read(reference["bytes"] + 1)
    if len(payload) != reference["bytes"] or sha256(payload) != expected:
        raise Fatal("checkpoint_rejected", "Checkpoint byte length or acquisition digest does not match reference")
    return payload


def fingerprint(reference, facts):
    directory = Path(__file__).parent
    value = {"implementation": IMPLEMENTATION, "protocol": VERSION,
             "source_sha256": {name: sha256((directory / name).read_bytes()) for name in
                               ("worker.py", "protocol.py", "backend.py", "reference-model.json")},
             "model": {key: reference[key] for key in
                       ("id", "sha256", "bytes", "architecture", "scale", "input_channels", "output_channels")},
             "runtime": facts}
    return value, sha256(canonical(value))


def publish(session, identifier, payload):
    directory = Path(session) / "output"
    destination = directory / sample_name(identifier)
    temporary = directory / (sample_name(identifier) + ".part")
    owned = False
    try:
        with temporary.open("xb") as stream:
            owned = True
            if stream.write(payload) != len(payload):
                raise OSError("Short sample write")
            stream.flush()
            os.fsync(stream.fileno())
        # Atomic no-replace publication on the same local filesystem (including NTFS).
        # Unlike POSIX rename, link never replaces an existing destination.
        os.link(temporary, destination)
        if destination.read_bytes() != payload:
            raise OSError("Published sample validation failed")
        return "output/" + destination.name
    finally:
        if owned:
            temporary.unlink(missing_ok=True)


def serve(config, input_stream, output_stream, diagnostics, backend_factory=TorchBackend):
    session_identity = uuid.uuid4().hex  # Transport binding, deliberately outside the stable fingerprint.
    def emit(kind, **fields):
        output_stream.write(canonical({"protocol": VERSION, "session": session_identity,
                                       "type": kind, **fields}).decode("utf-8") + "\n")
        output_stream.flush()

    identifier = None
    try:
        started = perf_counter()
        reference = json.loads((Path(__file__).parent / "reference-model.json").read_text(encoding="utf-8"))
        if config.model_id != reference["id"] or config.device != "cuda:0":
            raise Fatal("unsupported_configuration", "Require nomos_compact_2x on cuda:0 FP32")
        payload = load_checkpoint(config.checkpoint, config.expected_sha256, reference)
        session = Path(config.session_dir)
        if not session.is_absolute():
            raise Fatal("session_directory", "Session directory must be an explicit absolute fresh path")
        session.mkdir(mode=0o700)  # Reject existing sessions; host supplies a private parent on Windows.
        (session / "input").mkdir()
        (session / "output").mkdir()
        with contextlib.redirect_stdout(diagnostics):
            backend = backend_factory(payload, reference, config.device)
        del payload
        facts, identity = fingerprint(reference, backend.facts)
        emit("ready", fingerprint=identity, provenance=facts,
             capabilities={"format": "RGB8", "layout": "tightly-packed-row-major", "scale": 2,
                           "max_input_pixels": MAX_PIXELS, "max_requests": MAX_REQUESTS,
                           "max_line_bytes": MAX_LINE_BYTES, "sequential": True,
                           "cancel": False, "shutdown": "between-jobs"},
             timings={"startup_seconds": perf_counter() - started, "model_load_seconds": backend.load_seconds})
        seen = set()
        while True:
            identifier = None
            line = input_stream.readline(MAX_LINE_BYTES + 1)
            if not line:
                emit("bye", reason="eof")
                return 0
            if len(line) > MAX_LINE_BYTES:
                raise Fatal("message_limit", "JSON line exceeds worker message limit")
            try:
                record = parse_line(line)
                identifier = request_id(record)
                if identifier in seen:
                    raise Rejected("duplicate_id", "Request id was already used in this session")
                if len(seen) >= MAX_REQUESTS:
                    raise Fatal("session_limit", "Session request limit reached; start a fresh worker")
                seen.add(identifier)
                if record.get("type") == "shutdown":
                    if set(record) != {"protocol", "type", "id"}:
                        raise Rejected("invalid_fields", "shutdown accepts protocol, type and id only")
                    emit("bye", id=identifier, reason="shutdown")
                    return 0
                if record.get("type") != "infer":
                    raise Rejected("invalid_type", "Only infer and shutdown are supported")
                width, height = validate_infer(record)
                samples = read_samples(session, identifier, width, height, record["sha256"])
            except Rejected as error:
                emit("request_error", id=identifier, code=error.code, detail=str(error))
                continue
            # No acceptance of another request until inference, validation and publication finish.
            try:
                with contextlib.redirect_stdout(diagnostics):
                    result, timings = backend.infer(samples, width, height)
                if type(result) is not bytes or len(result) != width * height * 12:
                    raise Fatal("output_length", "Backend did not return exact native 2x RGB8 samples")
                output_digest = sha256(result)
                started = perf_counter()
                filename = publish(session, identifier, result)
                emit("result", id=identifier, fingerprint=identity,
                     input={"sha256": record["sha256"], "width": width, "height": height},
                     output={"sha256": output_digest, "width": width * 2, "height": height * 2,
                             "scale": 2, "format": "RGB8", "bytes": len(result), "file": filename},
                     timings={**timings, "publication_seconds": perf_counter() - started})
            finally:
                # Do not retain previous image payloads between requests.
                samples = result = None
    except Exception as error:
        if isinstance(error, (ImportError, ModuleNotFoundError)):
            code = "dependency_unavailable"
        elif isinstance(error, Fatal):
            code = error.code
        elif type(error).__name__ == "OutOfMemoryError":
            code = "cuda_oom"
        else:
            code = "worker_failure"
        print(f"{code}: {error}", file=diagnostics, flush=True)
        emit("fatal", id=identifier, code=code, detail=str(error))
        return 1


def main():
    # Keep even argument failures in the structured stdout contract.
    class ArgumentParser(argparse.ArgumentParser):
        def _print_message(self, message, file=None):
            super()._print_message(message, sys.stderr)
    parser = ArgumentParser(description=__doc__)
    for option in ("checkpoint", "expected-sha256", "model-id", "device", "session-dir"):
        parser.add_argument("--" + option, required=True)
    try:
        config = parser.parse_args()
    except SystemExit as error:
        if error.code:
            print(canonical({"protocol": VERSION, "type": "fatal", "id": None,
                             "code": "startup_arguments", "detail": "Explicit startup arguments are required"}).decode(), flush=True)
        return error.code
    return serve(config, sys.stdin.buffer, sys.stdout, sys.stderr)


if __name__ == "__main__":
    raise SystemExit(main())
