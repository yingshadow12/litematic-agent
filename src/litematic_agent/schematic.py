"""Litematica v4-v7 region geometry and continuous packed block states."""

import gzip
import hashlib
import math
from pathlib import Path
import re
import struct
import zlib

from .nbt import FormatError, Tag, dumps, get, loads

MAX_FILE = 64 * 1024 * 1024
MAX_NBT = 256 * 1024 * 1024
MAX_VOLUME = 16_000_000
AIR = {"minecraft:air", "minecraft:cave_air", "minecraft:void_air"}
STATE = re.compile(r"^([a-z0-9_.-]+:[a-z0-9_./-]+)(?:\[([^\[\]]+)\])?$")
PROPERTY = re.compile(r"^[a-z0-9_]+$")


def state_tag(state):
    if ":" not in state.split("[", 1)[0]:
        state = "minecraft:" + state
    match = STATE.fullmatch(state)
    if not match:
        raise ValueError("Expected block ID with optional [key=value,...] properties")
    name, props = match.groups()
    result = {"Name": Tag(8, name)}
    if props:
        values = {}
        for item in props.split(","):
            parts = item.split("=")
            if len(parts) != 2 or not all(PROPERTY.fullmatch(x) for x in parts):
                raise ValueError("Invalid block-state property")
            k, v = parts
            if k in values:
                raise ValueError("Duplicate block-state property")
            values[k] = Tag(8, v)
        result["Properties"] = Tag(10, values)
    return Tag(10, result)


def state_text(tag):
    name = get(tag, "Name", 8).value
    if "Properties" in tag.value:
        props = get(tag, "Properties", 10).value
        if any(v.kind != 8 for v in props.values()):
            raise FormatError("Block properties must be strings")
        if props:
            name += "[" + ",".join(f"{k}={v.value}" for k, v in sorted(props.items())) + "]"
    return name


def vector(tag):
    return tuple(get(tag, a, 3).value for a in "xyz")


class Packed:
    def __init__(self, data, count, palette_size):
        self.bits = max(2, (palette_size - 1).bit_length())
        self.count = count
        self.mask = (1 << self.bits) - 1
        expected = ((count * self.bits + 63) // 64) * 8
        if len(data) != expected:
            raise FormatError(f"Invalid packed array length: expected {expected}, got {len(data)}")
        self.data = bytearray(data)

    def _word(self, i):
        return struct.unpack_from(">Q", self.data, i * 8)[0]

    def __getitem__(self, i):
        word, shift = divmod(i * self.bits, 64)
        value = self._word(word) >> shift
        if shift + self.bits > 64:
            value |= self._word(word + 1) << (64 - shift)
        return value & self.mask

    def __setitem__(self, i, value):
        word, shift = divmod(i * self.bits, 64)
        limit = (1 << 64) - 1
        a = (self._word(word) & ~(self.mask << shift)) | (value << shift)
        struct.pack_into(">Q", self.data, word * 8, a & limit)
        extra = shift + self.bits - 64
        if extra > 0:
            b = (self._word(word + 1) & ~((1 << extra) - 1)) | (value >> (64 - shift))
            struct.pack_into(">Q", self.data, (word + 1) * 8, b)

    def resized(self, palette_size):
        bits = max(2, (palette_size - 1).bit_length())
        if bits == self.bits:
            return self
        other = Packed(bytes(((self.count * bits + 63) // 64) * 8), self.count, palette_size)
        for i in range(self.count):
            other[i] = self[i]
        return other


class Region:
    def __init__(self, name, tag):
        self.name, self.tag = name, tag
        self.position = vector(get(tag, "Position", 10))
        self.size = vector(get(tag, "Size", 10))
        self.shape = tuple(abs(x) for x in self.size)
        self.volume = math.prod(self.shape)
        if not self.volume or self.volume > MAX_VOLUME:
            raise FormatError(f"Region volume must be 1..{MAX_VOLUME}")
        self.low = tuple(p + min(0, s + 1) for p, s in zip(self.position, self.size))
        self.high = tuple(a + n - 1 for a, n in zip(self.low, self.shape))
        palette = get(tag, "BlockStatePalette", 9).value
        if palette[0] != 10 or not palette[1]:
            raise FormatError("BlockStatePalette must be a nonempty compound list")
        self.palette = palette[1]
        self.states = [state_text(t) for t in self.palette]
        self.blocks = Packed(get(tag, "BlockStates", 12).value, self.volume, len(self.palette))

    def index(self, pos):
        if len(pos) != 3 or any(p < a or p > b for p, a, b in zip(pos, self.low, self.high)):
            raise ValueError(f"Position outside region {self.name}")
        x, y, z = (p - a for p, a in zip(pos, self.low))
        return (y * self.shape[2] + z) * self.shape[0] + x

    def pos(self, i):
        x = i % self.shape[0]
        z = (i // self.shape[0]) % self.shape[2]
        y = i // (self.shape[0] * self.shape[2])
        return tuple(a + b for a, b in zip(self.low, (x, y, z)))

    def state(self, i):
        value = self.blocks[i]
        if value >= len(self.states):
            raise FormatError(f"Palette index {value} out of range in {self.name}")
        return self.states[value]

    def set(self, i, state):
        if state not in self.states:
            self.palette.append(state_tag(state))
            self.states.append(state)
            self.blocks = self.blocks.resized(len(self.states))
        self.blocks[i] = self.states.index(state)

    def sync(self):
        self.tag.value["BlockStates"] = Tag(12, bytes(self.blocks.data))


class Schematic:
    def __init__(self, data):
        self.sha256 = hashlib.sha256(data).hexdigest()
        if len(data) > MAX_FILE:
            raise FormatError("Compressed file exceeds 64 MiB")
        if data.startswith(b"\x1f\x8b"):
            import io
            try:
                with gzip.GzipFile(fileobj=io.BytesIO(data)) as f:
                    raw = f.read(MAX_NBT + 1)
            except (OSError, EOFError, zlib.error) as e:
                raise FormatError("Invalid or truncated gzip schematic") from e
        else:
            raw = data
        if len(raw) > MAX_NBT:
            raise FormatError("Decompressed NBT exceeds 256 MiB")
        self.root_name, self.root = loads(raw)
        self.version = get(self.root, "Version", 3).value
        if self.version not in (4, 5, 6, 7):
            raise FormatError("Only Litematica format versions 4..7 are supported")
        self.regions = {name: Region(name, tag) for name, tag in get(self.root, "Regions", 10).value.items()}
        if not self.regions or sum(r.volume for r in self.regions.values()) > MAX_VOLUME:
            raise FormatError(f"File must contain 1..{MAX_VOLUME} total region voxels")

    @classmethod
    def read(cls, path):
        with Path(path).open("rb") as f:
            data = f.read(MAX_FILE + 1)
        return cls(data)

    def region(self, name=None):
        if name is None:
            if len(self.regions) != 1:
                raise ValueError("Specify region: file has multiple regions")
            return next(iter(self.regions.values()))
        if name not in self.regions:
            raise ValueError(f"Unknown region: {name}")
        return self.regions[name]

    def encoded(self):
        for region in self.regions.values():
            region.sync()
        return gzip.compress(dumps(self.root_name, self.root), mtime=0)

    def validate(self):
        total = 0
        for region in self.regions.values():
            for i in range(region.volume):
                region.state(i)
            total += region.volume
        return {"valid": True, "regions": len(self.regions), "voxels_checked": total,
                "scope": "NBT structure and palette indices; not Minecraft block registry or simulation"}
