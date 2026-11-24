"""Version-1 sample transport. Standard library only; no application imports."""
import hashlib
import json
from pathlib import Path
import re

VERSION = 1
IMPLEMENTATION = "m33-neural-worker-v1"
MAX_PIXELS = 4_194_304
MAX_LINE_BYTES = 8192
MAX_REQUESTS = 4096
ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}\Z")
DIGEST = re.compile(r"[0-9a-f]{64}\Z")


class Rejected(ValueError):
    def __init__(self, code, detail):
        super().__init__(detail)
        self.code = code


def sha256(data):
    return hashlib.sha256(data).hexdigest()


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")


def parse_line(line):
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError("Duplicate JSON key")
            result[key] = value
        return result

    try:
        record = json.loads(line, object_pairs_hook=pairs,
                            parse_constant=lambda _: (_ for _ in ()).throw(ValueError("Nonfinite JSON")))
    except (ValueError, UnicodeError, RecursionError) as error:
        raise Rejected("invalid_json", "Expected one strict UTF-8 JSON object") from error
    if type(record) is not dict:
        raise Rejected("invalid_message", "Expected an object")
    if type(record.get("protocol")) is not int or record["protocol"] != VERSION:
        raise Rejected("invalid_version", "protocol must be integer 1")
    return record


def request_id(record):
    value = record.get("id")
    if type(value) is not str or not ID.fullmatch(value):
        raise Rejected("invalid_id", "id must be 1-64 ASCII letters/digits/underscore/hyphen, starting alphanumeric")
    return value


def validate_infer(record):
    if set(record) != {"protocol", "type", "id", "width", "height", "sha256"}:
        raise Rejected("invalid_fields", "infer requires protocol, type, id, width, height and sha256 only")
    width, height = record["width"], record["height"]
    if any(type(axis) is not int or axis <= 0 for axis in (width, height)):
        raise Rejected("invalid_dimensions", "width/height must be positive integers")
    if width * height > MAX_PIXELS:
        raise Rejected("input_limit", f"Input exceeds worker limit of {MAX_PIXELS} pixels")
    if type(record["sha256"]) is not str or not DIGEST.fullmatch(record["sha256"]):
        raise Rejected("invalid_digest", "sha256 must be 64 lowercase hexadecimal characters")
    return width, height


def sample_name(identifier):
    return f"job-{identifier}.rgb8"


def read_samples(session, identifier, width, height, expected):
    path = Path(session) / "input" / sample_name(identifier)
    size = width * height * 3
    try:
        if path.is_symlink() or not path.is_file():
            raise Rejected("invalid_input_file", "Input must be an ordinary session file")
        if path.stat().st_size != size:
            raise Rejected("input_length", f"Expected exactly {size} RGB8 bytes")
        with path.open("rb") as stream:
            payload = stream.read(size + 1)
    except OSError as error:
        raise Rejected("input_io", str(error)) from error
    if len(payload) != size:
        raise Rejected("input_length", f"Expected exactly {size} RGB8 bytes")
    if sha256(payload) != expected:
        raise Rejected("input_digest", "Input digest mismatch")
    return payload
