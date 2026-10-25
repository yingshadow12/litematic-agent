"""Small, bounded responses; edits require a reviewable JSON plan."""

from collections import Counter
import fnmatch
import hashlib
import itertools
import os
from pathlib import Path
import tempfile
import time

from .nbt import Tag, get
from .schematic import AIR, Schematic, state_tag, state_text

MAX_SCAN = 1_000_000
DEFAULT_SCAN = 200_000
REDSTONE = {
    "redstone_wire", "repeater", "comparator", "observer", "piston", "sticky_piston",
    "piston_head", "moving_piston", "redstone_torch", "redstone_wall_torch",
    "redstone_block", "lever", "tripwire", "tripwire_hook", "target", "daylight_detector",
    "hopper", "dropper", "dispenser", "crafter", "note_block", "redstone_lamp",
    "sculk_sensor", "calibrated_sculk_sensor", "sculk_shrieker", "tnt",
    "powered_rail", "detector_rail", "activator_rail", "trapped_chest", "lightning_rod",
}


def is_redstone(state):
    block = state.split("[", 1)[0]
    if not block.startswith("minecraft:"):
        return False
    name = block.split(":", 1)[1]
    return name in REDSTONE or name.endswith(("_button", "_pressure_plate", "_trapdoor", "_door", "_fence_gate"))


def integer(value, low, high, label):
    if type(value) is not int or not low <= value <= high:
        raise ValueError(f"{label} must be an integer in {low}..{high}")
    return value


def position(value):
    if not isinstance(value, (list, tuple)) or len(value) != 3 or any(type(n) is not int for n in value):
        raise ValueError("pos must be three integers [x,y,z]")
    return tuple(value)


def bounds(region, box=None):
    if box is None:
        return region.low, region.high
    if not isinstance(box, (list, tuple)) or len(box) != 6 or any(type(n) is not int for n in box):
        raise ValueError("box must be six integers [minX,minY,minZ,maxX,maxY,maxZ]")
    low, high = tuple(box[:3]), tuple(box[3:])
    if any(a > b for a, b in zip(low, high)):
        raise ValueError("box minimum exceeds maximum")
    region.index(low)
    region.index(high)
    return low, high


def selection(region, box=None, cursor=0):
    low, high = bounds(region, box)
    shape = tuple(b - a + 1 for a, b in zip(low, high))
    volume = shape[0] * shape[1] * shape[2]
    integer(cursor, 0, volume, "cursor")

    def items():
        for ordinal in range(cursor, volume):
            x = ordinal % shape[0]
            z = (ordinal // shape[0]) % shape[2]
            y = ordinal // (shape[0] * shape[2])
            p = tuple(a + b for a, b in zip(low, (x, y, z)))
            yield ordinal, region.index(p), p
    return volume, items()


def matcher(pattern=None):
    if pattern is None:
        return lambda _: True
    if not isinstance(pattern, str) or len(pattern) > 1024:
        raise ValueError("match must be a block ID glob or exact block state, up to 1024 characters")
    if "[" in pattern:
        exact = state_text(state_tag(pattern))
        return lambda state: state == exact
    if ":" not in pattern:
        pattern = "minecraft:" + pattern
    return lambda state: fnmatch.fnmatchcase(state.split("[", 1)[0], pattern)


def summary(s, region_offset=0, limit=16):
    integer(region_offset, 0, len(s.regions), "region_offset")
    integer(limit, 1, 32, "limit")
    regions = list(s.regions.values())[region_offset:region_offset + limit]
    return {
        "sha256": s.sha256, "format_version": s.version,
        "minecraft_data_version": s.root.value.get("MinecraftDataVersion", Tag(3, None)).value,
        "coordinate_system": "schematic coordinates, inclusive bounds; storage order x,z,y",
        "region_count": len(s.regions), "total_voxels": sum(r.volume for r in s.regions.values()),
        "regions": [{"name": r.name, "position": r.position, "signed_size": r.size,
                     "box": r.low + r.high, "voxels": r.volume, "palette_size": len(r.palette),
                     "block_entities": len(r.tag.value.get("TileEntities", Tag(9, (10, []))).value[1]),
                     "entities": len(r.tag.value.get("Entities", Tag(9, (10, []))).value[1])}
                    for r in regions],
        "next_region_offset": region_offset + len(regions) if region_offset + len(regions) < len(s.regions) else None,
    }


def query(s, region=None, box=None, match=None, category="all", limit=32, cursor=0, max_scan=DEFAULT_SCAN):
    integer(limit, 1, 128, "limit")
    integer(max_scan, 1, MAX_SCAN, "max_scan")
    if category not in ("all", "redstone"):
        raise ValueError("category must be all or redstone")
    r = s.region(region)
    total, items = selection(r, box, cursor)
    test = matcher(match)
    found, scanned = [], 0
    for ordinal, i, pos in itertools.islice(items, max_scan):
        state = r.state(i)
        scanned += 1
        if test(state) and (category == "all" or is_redstone(state)):
            found.append({"pos": pos, "state": state})
            if len(found) == limit:
                break
    end = cursor + scanned
    return {"region": r.name, "sha256": s.sha256, "blocks": found, "returned": len(found),
            "scanned": scanned, "selection_voxels": total, "complete": end == total,
            "next_cursor": end if end < total else None}


def inspect(s, pos, region=None):
    r = s.region(region)
    p = position(pos)
    state = r.state(r.index(p))
    adjacent = {}
    for name, offset in {"west": (-1, 0, 0), "east": (1, 0, 0), "down": (0, -1, 0),
                         "up": (0, 1, 0), "north": (0, 0, -1), "south": (0, 0, 1)}.items():
        target = tuple(a + b for a, b in zip(p, offset))
        try:
            index = r.index(target)
        except ValueError:
            adjacent[name] = {"pos": target, "state": None, "outside_region": True}
        else:
            adjacent[name] = {"pos": target, "state": r.state(index)}
    protected = protected_positions(r)
    return {"region": r.name, "pos": p, "state": state, "neighbors": adjacent,
            "block_entity_present": p in protected,
            "note": "outside_region is unknown, not air; other regions may overlap"}


def slice_view(s, axis, at, region=None, box=None):
    if axis not in "xyz" or len(axis) != 1:
        raise ValueError("axis must be x, y, or z")
    if type(at) is not int:
        raise ValueError("at must be an integer")
    r = s.region(region)
    low, high = bounds(r, box)
    fixed = "xyz".index(axis)
    if not low[fixed] <= at <= high[fixed]:
        raise ValueError("Slice coordinate is outside selected bounds")
    # Top-down Y slices: columns X, rows Z. Vertical slices: rows Y, ascending.
    col, row = {"x": (2, 1), "y": (0, 2), "z": (0, 1)}[axis]
    width, height = high[col] - low[col] + 1, high[row] - low[row] + 1
    if width > 128 or height > 128 or width * height > 4096:
        raise ValueError("Slice exceeds 4096 cells or 128 per edge; narrow box")
    symbols = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz!#$%&()*+,-/:;<=>?@^_{}~"
    legend, rows = {}, []
    for v in range(low[row], high[row] + 1):
        line = []
        for u in range(low[col], high[col] + 1):
            pos = [0, 0, 0]
            pos[fixed], pos[col], pos[row] = at, u, v
            state = r.state(r.index(pos))
            if state.split("[", 1)[0] in AIR:
                symbol = "."
                legend.setdefault(state, symbol)
            else:
                if state not in legend:
                    used = {x for x in legend.values() if x != "."}
                    if len(used) >= len(symbols):
                        raise ValueError("Too many block states for slice; narrow box")
                    legend[state] = symbols[len(used)]
                symbol = legend[state]
            line.append(symbol)
        rows.append("".join(line))
    return {"region": r.name, "axis": axis, "at": at, "columns": "xyz"[col], "rows_axis": "xyz"[row],
            "origin": {"xyz"[col]: low[col], "xyz"[row]: low[row]}, "direction": "both axes ascending",
            "legend": {symbol: state for state, symbol in legend.items() if symbol != "."} | {".": "air variants"},
            "rows": rows}


def analyze(s, region=None, box=None, max_scan=DEFAULT_SCAN, limit=16):
    integer(max_scan, 1, MAX_SCAN, "max_scan")
    integer(limit, 1, 32, "limit")
    r = s.region(region)
    total, items = selection(r, box)
    materials, redstone, facings, layers = Counter(), Counter(), Counter(), Counter()
    scanned, nonair = 0, 0
    for _, i, pos in itertools.islice(items, max_scan):
        state = r.state(i)
        name = state.split("[", 1)[0]
        materials[name] += 1
        scanned += 1
        if name not in AIR:
            nonair += 1
            layers[pos[1]] += 1
        if is_redstone(state):
            redstone[name] += 1
            props = state_tag(state).value.get("Properties", Tag(10, {})).value
            if "facing" in props:
                facings[props["facing"].value] += 1
    def top(counter):
        return {"counts": dict(counter.most_common(limit)), "omitted_keys": max(0, len(counter) - limit)}
    return {"region": r.name, "scanned": scanned, "selection_voxels": total, "complete": scanned == total,
            "count_scope": "entire selection" if scanned == total else "first scanned voxels only; not estimates",
            "non_air": nonair, "materials": top(materials), "redstone_components": top(redstone),
            "redstone_facings": dict(facings), "densest_layers": top(layers),
            "limitations": "Static layout only. Does not simulate connectivity, timing, quasi-connectivity, update order, or performance. Region counts may overlap."}


def protected_positions(r):
    result = set()
    for tag in r.tag.value.get("TileEntities", Tag(9, (10, []))).value[1]:
        # Litematica block entities use normalized container-local coordinates.
        pos = tuple(get(tag, a, 3).value for a in "xyz")
        result.add(tuple(a + b for a, b in zip(r.low, pos)))
    return result


def _atomic_new(path, data):
    """Publish a complete file atomically, without overwriting existing paths."""
    path = Path(path)
    if not path.parent.is_dir():
        raise ValueError("Output parent directory does not exist")
    fd, tmp = tempfile.mkstemp(prefix=".litematic-", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        os.link(tmp, path)  # same filesystem, atomic no-clobber publication
    finally:
        os.unlink(tmp)


def edit(s, plan, output=None, apply=False):
    if not isinstance(plan, dict) or set(plan) - {"source_sha256", "operations"}:
        raise ValueError("Plan allows only source_sha256 and operations")
    expected = plan.get("source_sha256")
    if expected is not None and expected != s.sha256:
        raise ValueError("Source SHA256 changed; inspect the file and regenerate the plan")
    if apply and not expected:
        raise ValueError("Applying edits requires plan.source_sha256 from summary or preview")
    ops = plan.get("operations")
    if not isinstance(ops, list) or not 1 <= len(ops) <= 64:
        raise ValueError("Plan requires 1..64 operations")
    if apply and not output:
        raise ValueError("Applying edits requires a new output path")
    if output and Path(output).exists():
        raise ValueError("Output already exists; choose a new path (source is never overwritten)")
    # Dry-run mutates only this fresh in-memory document; callers never cache it.
    changes, samples, touched, work = 0, [], set(), 0
    for op in ops:
        if not isinstance(op, dict) or set(op) - {"op", "region", "pos", "box", "match", "state", "properties"}:
            raise ValueError("Invalid operation fields")
        kind = op.get("op")
        if kind not in ("set", "fill", "replace", "properties"):
            raise ValueError("op must be set, fill, replace, or properties")
        r = s.region(op.get("region"))
        if kind == "set":
            p = position(op.get("pos"))
            items = iter([(0, r.index(p), p)])
            count = 1
        else:
            if kind == "fill" and "box" not in op:
                raise ValueError("fill requires an explicit box")
            if kind in ("replace", "properties") and "match" not in op:
                raise ValueError(f"{kind} requires match")
            count, items = selection(r, op.get("box"))
        work += count
        if work > MAX_SCAN:
            raise ValueError("Edit scan budget exceeds 1000000 voxels; narrow the selection or split plans")
        test = matcher(op.get("match"))
        if kind != "properties":
            replacement = state_text(state_tag(op.get("state", "")))
        else:
            props = op.get("properties")
            if not isinstance(props, dict) or not props or any(not isinstance(k, str) or not isinstance(v, str) for k, v in props.items()):
                raise ValueError("properties must be a nonempty string-to-string object")
            # Validate names/values without guessing the version-specific block registry.
            state_tag("minecraft:air[" + ",".join(f"{k}={v}" for k, v in props.items()) + "]")
        protected = protected_positions(r)
        pending = any(r.tag.value.get(k, Tag(9, (10, []))).value[1]
                      for k in ("PendingBlockTicks", "PendingFluidTicks"))
        for _, i, p in items:
            old = r.state(i)
            if not test(old):
                continue
            if kind == "properties":
                tag = state_tag(old)
                old_props = tag.value.setdefault("Properties", Tag(10, {})).value
                missing = set(props) - set(old_props)
                if missing:
                    raise ValueError(f"Cannot add unknown properties {sorted(missing)} to {old}; only patch existing keys")
                old_props.update({k: Tag(8, v) for k, v in props.items()})
                replacement = state_text(tag)
            if old == replacement:
                continue
            if p in protected:
                raise ValueError(f"Edit touches block entity at {p}; block-entity editing is not supported")
            if pending:
                raise ValueError("Region has scheduled ticks; editing its blocks requires game-aware tick handling")
            r.set(i, replacement)
            changes += 1
            touched.add(r.name)
            if len(samples) < 12:
                samples.append({"region": r.name, "pos": p, "from": old, "to": replacement})
    result = {"source_sha256": s.sha256, "applied": False, "changed_assignments": changes,
              "touched_regions": sorted(touched), "samples": samples,
              "sample_truncated": changes > len(samples),
              "validation_scope": "NBT and palette integrity; block IDs and property values require in-game validation"}
    if not apply:
        return result
    # Check every region before writing, including untouched regions. Metadata counts
    # are recomputed from final states (overlap is counted per Litematica region).
    s.validate()
    non_air = sum(r.state(i).split("[", 1)[0] not in AIR for r in s.regions.values() for i in range(r.volume))
    metadata = s.root.value.get("Metadata")
    if metadata is not None and metadata.kind == 10:
        metadata.value["TotalBlocks"] = Tag(3, non_air)
        metadata.value["TimeModified"] = Tag(4, int(time.time() * 1000))
    data = s.encoded()
    validation = Schematic(data).validate()
    _atomic_new(output, data)
    result.update(applied=True, output=str(output), output_sha256=hashlib.sha256(data).hexdigest(), validation=validation)
    return result


def dispatch(action, path, **kwargs):
    functions = {"summary": summary, "query": query, "inspect": inspect, "slice": slice_view,
                 "analyze": analyze, "edit": edit, "validate": lambda s: s.validate()}
    if action not in functions:
        raise ValueError(f"Unknown action: {action}")
    return functions[action](Schematic.read(path), **kwargs)
