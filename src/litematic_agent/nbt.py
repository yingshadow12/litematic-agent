"""Typed big-endian Java NBT. Unknown fields and numeric types survive edits."""

from dataclasses import dataclass
import io
import struct


class FormatError(ValueError):
    pass


@dataclass
class Tag:
    kind: int
    value: object


NUMBERS = {1: "b", 2: "h", 3: "i", 4: "q", 5: "f", 6: "d"}
MAX_ITEMS = 2_000_000


class NbtString(str):
    """Retain original bytes for unchanged strings from Java or Python writers."""
    def __new__(cls, value, raw):
        result = super().__new__(cls, value)
        result.raw = raw
        return result


def _decode_string(data):
    # Java's modified UTF-8 encodes NUL as C0 80 and astral characters as
    # surrogate pairs. Also accept conventional UTF-8 emitted by NBT tools.
    s = data.replace(b"\xc0\x80", b"\0").decode("utf-8", "surrogatepass")
    return NbtString(s.encode("utf-16", "surrogatepass").decode("utf-16"), data)


def _encode_string(s):
    if isinstance(s, NbtString):
        return struct.pack(">H", len(s.raw)) + s.raw
    units = s.encode("utf-16-be")
    out = bytearray()
    for i in range(0, len(units), 2):
        n = int.from_bytes(units[i:i + 2], "big")
        if 0 < n < 128:
            out.append(n)
        elif n < 2048:
            out.extend((192 | (n >> 6), 128 | (n & 63)))
        else:
            out.extend((224 | (n >> 12), 128 | ((n >> 6) & 63), 128 | (n & 63)))
    if len(out) > 65535:
        raise FormatError("NBT string exceeds 65535 encoded bytes")
    return struct.pack(">H", len(out)) + out


def loads(data):
    stream = io.BytesIO(data)
    remaining = MAX_ITEMS

    def read(n):
        b = stream.read(n)
        if len(b) != n:
            raise FormatError("Truncated NBT")
        return b

    def string():
        return _decode_string(read(struct.unpack(">H", read(2))[0]))

    def payload(kind, depth=0):
        nonlocal remaining
        remaining -= 1
        if remaining < 0 or depth > 64:
            raise FormatError("NBT structure exceeds safety limits")
        if kind in NUMBERS:
            fmt = ">" + NUMBERS[kind]
            value = struct.unpack(fmt, read(struct.calcsize(fmt)))[0]
        elif kind in (7, 11, 12):
            count = struct.unpack(">i", read(4))[0]
            if count < 0:
                raise FormatError("Negative NBT array size")
            value = read(count * {7: 1, 11: 4, 12: 8}[kind])
        elif kind == 8:
            value = string()
        elif kind == 9:
            child, count = struct.unpack(">Bi", read(5))
            if count < 0 or count > remaining or (child == 0 and count):
                raise FormatError("Invalid NBT list")
            value = (child, [payload(child, depth + 1) for _ in range(count)])
        elif kind == 10:
            value = {}
            while True:
                child = read(1)[0]
                if not child:
                    break
                name = string()
                if name in value:
                    raise FormatError("Duplicate NBT compound key")
                value[name] = payload(child, depth + 1)
        else:
            raise FormatError(f"Unsupported NBT tag: {kind}")
        return Tag(kind, value)

    if read(1) != b"\x0a":
        raise FormatError("NBT root must be a compound")
    name = string()
    root = payload(10)
    if stream.read(1):
        raise FormatError("Trailing data after NBT root")
    return name, root


def dumps(name, root):
    out = io.BytesIO()

    def payload(tag):
        kind, value = tag.kind, tag.value
        if kind in NUMBERS:
            out.write(struct.pack(">" + NUMBERS[kind], value))
        elif kind in (7, 11, 12):
            width = {7: 1, 11: 4, 12: 8}[kind]
            out.write(struct.pack(">i", len(value) // width))
            out.write(value)
        elif kind == 8:
            out.write(_encode_string(value))
        elif kind == 9:
            child, items = value
            out.write(struct.pack(">Bi", child, len(items)))
            for item in items:
                if item.kind != child:
                    raise FormatError("Heterogeneous NBT list")
                payload(item)
        elif kind == 10:
            for key, item in value.items():
                out.write(bytes([item.kind]))
                out.write(_encode_string(key))
                payload(item)
            out.write(b"\0")
        else:
            raise FormatError(f"Unsupported NBT tag: {kind}")

    out.write(b"\x0a" + _encode_string(name))
    payload(root)
    return out.getvalue()


def get(compound, key, kind):
    try:
        tag = compound.value[key]
    except (KeyError, AttributeError) as e:
        raise FormatError(f"Missing NBT field {key}") from e
    if tag.kind != kind:
        raise FormatError(f"Wrong NBT type for {key}: expected {kind}")
    return tag
