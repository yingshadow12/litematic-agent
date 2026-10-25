import copy
import gzip
import hashlib
import json
from pathlib import Path
import struct
import subprocess
import sys
import tempfile
import unittest
import zipfile

from litematic_agent import nbt
from litematic_agent.mcp import Server
from litematic_agent.operations import dispatch
from litematic_agent.schematic import Packed, Schematic, state_tag


def independent_pack(values, bits):
    """Reference uses one big integer, not production word/shift code."""
    number = sum(v << (i * bits) for i, v in enumerate(values))
    return b"".join(((number >> (64 * j)) & ((1 << 64) - 1)).to_bytes(8, "big")
                    for j in range((len(values) * bits + 63) // 64))


def fixture(size=(5, 2, 7), pos=(10, 20, 30), states=None, values=None):
    states = states or ["minecraft:air", "minecraft:stone", "minecraft:repeater[delay=1,facing=north,locked=false,powered=false]"]
    volume = abs(size[0] * size[1] * size[2])
    values = values or [i % len(states) for i in range(volume)]
    vec = lambda v: nbt.Tag(10, {a: nbt.Tag(3, n) for a, n in zip("xyz", v)})
    r = nbt.Tag(10, {
        "Position": vec(pos), "Size": vec(size),
        "BlockStatePalette": nbt.Tag(9, (10, [state_tag(s) for s in states])),
        "BlockStates": nbt.Tag(12, independent_pack(values, max(2, (len(states) - 1).bit_length()))),
        "TileEntities": nbt.Tag(9, (10, [])), "Entities": nbt.Tag(9, (10, [])),
        "PendingBlockTicks": nbt.Tag(9, (10, [])), "PendingFluidTicks": nbt.Tag(9, (10, [])),
        "UnknownRegionData": nbt.Tag(7, b"\x01\x02\xff"),
    })
    return nbt.Tag(10, {
        "Version": nbt.Tag(3, 6), "SubVersion": nbt.Tag(3, 1),
        "MinecraftDataVersion": nbt.Tag(3, 3700),
        "Metadata": nbt.Tag(10, {"Name": nbt.Tag(8, "测试🏠"), "Author": nbt.Tag(8, "test"),
                                 "Description": nbt.Tag(8, "fixture"), "RegionCount": nbt.Tag(3, 1),
                                 "TotalVolume": nbt.Tag(3, volume), "TotalBlocks": nbt.Tag(3, 0),
                                 "TimeCreated": nbt.Tag(4, 100), "TimeModified": nbt.Tag(4, 100),
                                 "EnclosingSize": vec(tuple(abs(s) for s in size))}),
        "Regions": nbt.Tag(10, {"main": r}),
        "UnknownRootData": nbt.Tag(11, struct.pack(">ii", -42, 99)),
    })


class ToolTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.path = self.root / "input.litematic"
        self.write(fixture())

    def write(self, root):
        self.path.write_bytes(gzip.compress(nbt.dumps("", root)))

    def plan(self, ops):
        return {"source_sha256": hashlib.sha256(self.path.read_bytes()).hexdigest(), "operations": ops}

    def test_nbt_all_tags_and_unicode(self):
        value = nbt.Tag(10, {"text": nbt.Tag(8, "nul\0中文🏠"),
                             **{str(k): nbt.Tag(k, 1.5 if k in (5, 6) else -7) for k in range(1, 7)},
                             "bytes": nbt.Tag(7, b"\0\xff"), "ints": nbt.Tag(11, b"\0" * 8),
                             "longs": nbt.Tag(12, b"\xff" * 8),
                             "list": nbt.Tag(9, (8, [nbt.Tag(8, "x")])),
                             "endlist": nbt.Tag(9, (0, []))})
        self.assertEqual(nbt.loads(nbt.dumps("root🏠", value)), ("root🏠", value))

    def test_packed_cross_word_all_widths(self):
        for palette in (1, 4, 5, 8, 9, 17, 129, 513):
            bits = max(2, (palette - 1).bit_length())
            values = [(i * 7) % palette for i in range(135)]
            packed = Packed(independent_pack(values, bits), len(values), palette)
            self.assertEqual([packed[i] for i in range(len(values))], values)
            for i in range(len(values)):
                packed[i] = palette - 1
                values[i] = palette - 1
            self.assertEqual(bytes(packed.data), independent_pack(values, bits))

    def test_standard_utf8_strings_preserve_original_encoding(self):
        raw = "🏠".encode("utf-8")
        data = b"\x0a\0\0\x08\0\x01x" + struct.pack(">H", len(raw)) + raw + b"\0"
        name, root = nbt.loads(data)
        self.assertEqual(root.value["x"].value, "🏠")
        self.assertEqual(nbt.dumps(name, root), data)

    def test_corrupt_gzip_tool_error_does_not_crash_mcp(self):
        self.path.write_bytes(b"\x1f\x8b\x08\0\0\0\0\0\0\xffinvalid stream")
        response = Server(self.root).handle({"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {
            "name": "litematic_summary", "arguments": {"path": "input.litematic"}}})
        self.assertTrue(response["result"]["isError"])
        self.assertIn("gzip", response["result"]["content"][0]["text"])

    def test_summary_and_validate(self):
        self.assertEqual(dispatch("summary", self.path)["total_voxels"], 70)
        self.assertEqual(dispatch("validate", self.path)["voxels_checked"], 70)

    def test_signed_size_coordinates(self):
        for size in ((-5, 2, -7), (5, -2, 7), (-5, -2, -7)):
            self.write(fixture(size=size))
            r = Schematic.read(self.path).region()
            self.assertEqual(r.low, tuple(p + min(0, s + 1) for p, s in zip((10, 20, 30), size)))
            for i in range(r.volume):
                self.assertEqual(r.index(r.pos(i)), i)
                self.assertEqual(r.state(i), r.states[i % 3])

    def test_query_pagination_no_duplicates(self):
        cursor, seen = 0, []
        while cursor is not None:
            page = dispatch("query", self.path, match="stone", limit=3, cursor=cursor, max_scan=8)
            seen.extend(tuple(b["pos"]) for b in page["blocks"])
            cursor = page["next_cursor"]
        self.assertEqual(len(seen), 23)
        self.assertEqual(len(set(seen)), len(seen))

    def test_query_exact_properties_and_redstone(self):
        result = dispatch("query", self.path, category="redstone", limit=128)
        self.assertEqual(result["returned"], 23)
        self.assertTrue(result["complete"])
        exact = dispatch("query", self.path, match="repeater[powered=false,locked=false,facing=north,delay=1]")
        self.assertEqual(exact["returned"], 23)

    def test_empty_match_still_advances_scan_cursor(self):
        result = dispatch("query", self.path, match="diamond_block", max_scan=10)
        self.assertEqual(result["blocks"], [])
        self.assertEqual(result["next_cursor"], 10)
        self.assertFalse(result["complete"])

    def test_box_scan(self):
        result = dispatch("query", self.path, box=[10, 20, 30, 11, 20, 31])
        self.assertEqual([b["pos"] for b in result["blocks"]], [(10, 20, 30), (11, 20, 30), (10, 20, 31), (11, 20, 31)])

    def test_slices_all_axes(self):
        for axis, at, nrows, width in (("x", 10, 2, 7), ("y", 20, 7, 5), ("z", 30, 2, 5)):
            result = dispatch("slice", self.path, axis=axis, at=at)
            self.assertEqual(len(result["rows"]), nrows)
            self.assertTrue(all(len(row) == width for row in result["rows"]))
            self.assertIn("minecraft:stone", result["legend"].values())

    def test_inspect_outside_not_air(self):
        result = dispatch("inspect", self.path, pos=[10, 20, 30])
        self.assertTrue(result["neighbors"]["west"]["outside_region"])
        self.assertEqual(result["neighbors"]["east"]["state"], "minecraft:stone")

    def test_analysis_partial_and_complete(self):
        partial = dispatch("analyze", self.path, max_scan=4)
        self.assertFalse(partial["complete"])
        self.assertEqual(sum(partial["materials"]["counts"].values()), 4)
        full = dispatch("analyze", self.path)
        self.assertTrue(full["complete"])
        self.assertEqual(full["non_air"], 46)
        self.assertEqual(full["redstone_components"]["counts"]["minecraft:repeater"], 23)

    def test_dry_run_does_not_write(self):
        before = self.path.read_bytes()
        output = self.root / "out.litematic"
        plan = self.plan([{"op": "set", "pos": [10, 20, 30], "state": "stone"}])
        result = dispatch("edit", self.path, plan=plan, output=output)
        self.assertEqual(result["changed_assignments"], 1)
        self.assertFalse(output.exists())
        self.assertEqual(self.path.read_bytes(), before)

    def test_edit_reopen_preserves_unknown_and_entities(self):
        root = fixture()
        root.value["Regions"].value["main"].value["Entities"].value[1].append(nbt.Tag(10, {"id": nbt.Tag(8, "minecraft:armor_stand")}))
        self.write(root)
        output = self.root / "out.litematic"
        plan = self.plan([{"op": "replace", "match": "stone", "state": "glass"}])
        result = dispatch("edit", self.path, plan=plan, output=output, apply=True)
        self.assertTrue(result["applied"])
        modified = Schematic.read(output)
        self.assertEqual(dispatch("query", output, match="glass")["returned"], 23)
        self.assertEqual(modified.root.value["UnknownRootData"], root.value["UnknownRootData"])
        for field in ("UnknownRegionData", "Entities", "Position", "Size"):
            self.assertEqual(modified.region().tag.value[field], root.value["Regions"].value["main"].value[field])
        self.assertEqual(modified.root.value["Metadata"].value["TotalBlocks"].value, 46)

    def test_palette_growth_changes_bits_without_corruption(self):
        states = ["air", "stone", "glass", "dirt"]
        self.write(fixture(states=states))
        before = Schematic.read(self.path)
        output = self.root / "out.litematic"
        plan = self.plan([{"op": "set", "pos": [10, 20, 30], "state": "diamond_block"}])
        dispatch("edit", self.path, plan=plan, output=output, apply=True)
        after = Schematic.read(output)
        self.assertEqual(after.region().blocks.bits, 3)
        for i in range(1, 70):
            self.assertEqual(after.region().state(i), before.region().state(i))

    def test_property_patch_retains_other_properties(self):
        output = self.root / "out.litematic"
        plan = self.plan([{"op": "properties", "match": "repeater", "properties": {"delay": "4"}}])
        dispatch("edit", self.path, plan=plan, output=output, apply=True)
        state = dispatch("inspect", output, pos=[12, 20, 30])["state"]
        self.assertEqual(state, "minecraft:repeater[delay=4,facing=north,locked=false,powered=false]")

    def test_fill_requires_box(self):
        with self.assertRaisesRegex(ValueError, "explicit box"):
            dispatch("edit", self.path, plan=self.plan([{"op": "fill", "state": "air"}]))

    def test_source_hash_required_and_mismatch(self):
        ops = [{"op": "set", "pos": [10, 20, 30], "state": "stone"}]
        with self.assertRaisesRegex(ValueError, "requires plan.source_sha256"):
            dispatch("edit", self.path, plan={"operations": ops}, output=self.root / "x.litematic", apply=True)
        plan = self.plan(ops)
        plan["source_sha256"] = "stale"
        with self.assertRaisesRegex(ValueError, "SHA256 changed"):
            dispatch("edit", self.path, plan=plan)

    def test_source_and_existing_output_not_overwritten(self):
        before = self.path.read_bytes()
        plan = self.plan([{"op": "set", "pos": [10, 20, 30], "state": "stone"}])
        with self.assertRaisesRegex(ValueError, "already exists"):
            dispatch("edit", self.path, plan=plan, output=self.path, apply=True)
        self.assertEqual(self.path.read_bytes(), before)

    def test_block_entity_protection_negative_region(self):
        root = fixture(size=(-5, 2, -7))
        root.value["Regions"].value["main"].value["TileEntities"].value[1].append(
            nbt.Tag(10, {a: nbt.Tag(3, 0) for a in "xyz"}))
        self.write(root)
        pos = [6, 20, 24]
        self.assertTrue(dispatch("inspect", self.path, pos=pos)["block_entity_present"])
        with self.assertRaisesRegex(ValueError, "block entity"):
            dispatch("edit", self.path, plan=self.plan([{"op": "set", "pos": pos, "state": "glass"}]))

    def test_scheduled_tick_protection(self):
        root = fixture()
        root.value["Regions"].value["main"].value["PendingBlockTicks"].value[1].append(nbt.Tag(10, {}))
        self.write(root)
        with self.assertRaisesRegex(ValueError, "scheduled ticks"):
            dispatch("edit", self.path, plan=self.plan([{"op": "set", "pos": [10, 20, 30], "state": "glass"}]))

    def test_multi_region_requires_name_and_preserves_other(self):
        root = fixture()
        root.value["Regions"].value["second"] = copy.deepcopy(root.value["Regions"].value["main"])
        self.write(root)
        with self.assertRaisesRegex(ValueError, "multiple regions"):
            dispatch("query", self.path)
        self.assertEqual(dispatch("summary", self.path, limit=1)["next_region_offset"], 1)
        output = self.root / "out.litematic"
        plan = self.plan([{"op": "set", "region": "main", "pos": [10, 20, 30], "state": "stone"}])
        dispatch("edit", self.path, plan=plan, output=output, apply=True)
        self.assertEqual(Schematic.read(output).region("second").tag, root.value["Regions"].value["second"])

    def test_failure_late_in_plan_writes_nothing(self):
        output = self.root / "out.litematic"
        plan = self.plan([{"op": "set", "pos": [10, 20, 30], "state": "stone"},
                          {"op": "set", "pos": [999, 0, 0], "state": "stone"}])
        with self.assertRaises(ValueError):
            dispatch("edit", self.path, plan=plan, output=output, apply=True)
        self.assertFalse(output.exists())

    def test_corrupt_palette_index_rejected_before_write(self):
        root = fixture()
        r = root.value["Regions"].value["main"]
        data = bytearray(r.value["BlockStates"].value)
        data[7] |= 3
        r.value["BlockStates"].value = bytes(data)
        self.write(root)
        with self.assertRaisesRegex(ValueError, "out of range"):
            dispatch("validate", self.path)

    def test_truncated_nbt_and_unsupported_version(self):
        with self.assertRaises(nbt.FormatError):
            nbt.loads(b"\x0a\0\0\x03\0")
        root = fixture()
        root.value["Version"].value = 99
        self.write(root)
        with self.assertRaisesRegex(ValueError, "versions"):
            dispatch("summary", self.path)

    def test_bounds_and_budget_enforced(self):
        for kwargs in ({"limit": 1000}, {"max_scan": 0}, {"cursor": -1}, {"box": [1, 2, 3, 0, 2, 3]}):
            with self.assertRaises(ValueError):
                dispatch("query", self.path, **kwargs)

    def test_mcp_root_and_symlink_escape(self):
        server = Server(self.root)
        self.assertEqual(server.call("litematic_summary", {"path": "input.litematic"})["region_count"], 1)
        for name in ("../escape.litematic", "input.txt"):
            with self.assertRaises(ValueError):
                server.call("litematic_summary", {"path": name})
        with tempfile.TemporaryDirectory() as outside:
            (self.root / "escape").symlink_to(outside, target_is_directory=True)
            with self.assertRaises(ValueError):
                server.path("escape/file.litematic")

    def test_mcp_tool_error_and_notifications(self):
        server = Server(self.root)
        self.assertIsNone(server.handle({"jsonrpc": "2.0", "method": "notifications/initialized"}))
        response = server.handle({"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": "litematic_query", "arguments": {"path": "input.litematic", "limit": 999}}})
        self.assertTrue(response["result"]["isError"])

    def test_stdio_mcp_end_to_end_and_edit(self):
        plan = self.plan([{"op": "set", "pos": [10, 20, 30], "state": "glass"}])
        requests = [
            {"id": 1, "method": "initialize", "params": {"protocolVersion": "2025-06-18"}},
            {"method": "notifications/initialized"},
            {"id": 2, "method": "tools/list"},
            {"id": 3, "method": "tools/call", "params": {"name": "litematic_edit", "arguments": {"path": "input.litematic", "plan": plan, "output": "mcp-out.litematic", "apply": True}}},
            {"id": 4, "method": "tools/call", "params": {"name": "litematic_inspect", "arguments": {"path": "mcp-out.litematic", "pos": [10, 20, 30]}}},
        ]
        wire = "\n".join(json.dumps({"jsonrpc": "2.0", **r}) for r in requests) + "\n"
        p = subprocess.run([sys.executable, "-m", "litematic_agent.mcp", "--root", str(self.root)], input=wire, text=True, capture_output=True)
        self.assertEqual(p.returncode, 0, p.stderr)
        responses = [json.loads(line) for line in p.stdout.splitlines()]
        self.assertEqual([r["id"] for r in responses], [1, 2, 3, 4])
        self.assertEqual(len(responses[1]["result"]["tools"]), 7)
        self.assertTrue(json.loads(responses[2]["result"]["content"][0]["text"])["applied"])
        self.assertEqual(json.loads(responses[3]["result"]["content"][0]["text"])["state"], "minecraft:glass")

    def test_cli_real_commands_and_failure_status(self):
        runner = Path(__file__).resolve().parents[1] / ".agents/skills/litematic-agent/scripts/litematic_tool.py"
        result = subprocess.run([sys.executable, str(runner), "summary", str(self.path)], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)["total_voxels"], 70)
        failure = subprocess.run([sys.executable, str(runner), "validate", str(self.root / "missing.litematic")], capture_output=True, text=True)
        self.assertEqual(failure.returncode, 1)
        self.assertIn("error", json.loads(failure.stdout))

    def test_exported_skill_runs_without_repository(self):
        repo = Path(__file__).resolve().parents[1]
        archive = self.root / "skill.zip"
        subprocess.run([sys.executable, str(repo / "scripts/package_skill.py"), "--output", str(archive)],
                       capture_output=True, text=True, check=True)
        with zipfile.ZipFile(archive) as z:
            z.extractall(self.root / "exported")
        runner = self.root / "exported/litematic-agent/scripts/litematic_tool.py"
        result = subprocess.run([sys.executable, "-I", str(runner), "summary", str(self.path)],
                                cwd=self.root, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)["total_voxels"], 70)


if __name__ == "__main__":
    unittest.main()
