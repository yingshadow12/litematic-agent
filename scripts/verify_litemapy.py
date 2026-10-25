"""Optional interoperability check; requires litemapy in a test-only environment.

PYTHONPATH=src /path/to/test-venv/bin/python scripts/verify_litemapy.py
Litemapy is NOT a runtime dependency of this tool.
"""
from pathlib import Path
import tempfile

from litemapy import BlockState, Region, Schematic as Reference
from litematic_agent.operations import dispatch
from litematic_agent.schematic import Schematic


def main():
    checked = 0
    with tempfile.TemporaryDirectory() as directory:
        for index, size in enumerate(((9, 3, 7), (-9, -3, -7), (-9, 3, -7))):
            source = Path(directory) / f"source-{index}.litematic"
            target = Path(directory) / f"target-{index}.litematic"
            region = Region(10, 20, 30, *size)
            variants = [BlockState("minecraft:air"), BlockState("minecraft:stone"),
                        BlockState("minecraft:glass"), BlockState("minecraft:oak_planks"),
                        BlockState("minecraft:observer", facing="east", powered="false")]
            for n, pos in enumerate(region.block_positions()):
                region[pos] = variants[n % len(variants)]
            Reference(name="交叉验证🏠", author="test", regions={"main": region}).save(str(source))
            ours = Schematic.read(source)
            for pos in region.block_positions():
                absolute = tuple(a + b for a, b in zip((10, 20, 30), pos))
                assert ours.region().state(ours.region().index(absolute)) == str(region[pos])
                checked += 1
            result = dispatch("edit", source, plan={"source_sha256": ours.sha256, "operations": [
                {"op": "replace", "match": "minecraft:stone", "state": "minecraft:diamond_block"}]},
                output=target, apply=True)
            assert result["changed_assignments"] == 38
            loaded = Reference.load(str(target))
            for pos in region.block_positions():
                before = region[pos]
                expected = BlockState("minecraft:diamond_block") if before.id == "minecraft:stone" else before
                assert loaded.regions["main"][pos] == expected
            assert loaded.name == "交叉验证🏠"
    print(f"PASS: {checked} voxels checked against Litemapy across three signed geometries; edited files and Unicode metadata reread correctly")


if __name__ == "__main__":
    main()
