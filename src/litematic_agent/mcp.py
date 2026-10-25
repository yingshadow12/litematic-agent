"""Dependency-free MCP stdio server (newline-delimited JSON-RPC)."""

import argparse
import json
from pathlib import Path
import sys

from . import __version__
from .cli import compact
from .operations import dispatch

STRING = {"type": "string"}
INT = {"type": "integer"}
BOX = {"type": "array", "items": INT, "minItems": 6, "maxItems": 6}
POS = {"type": "array", "items": INT, "minItems": 3, "maxItems": 3}
COMMON = {"path": {"type": "string", "description": "Input .litematic under server root"}}
REGION = {"region": {"type": "string", "description": "Required for multi-region files; schematic coordinates"}}
SELECTION = REGION | {"box": BOX}
LIMIT = {"type": "integer", "minimum": 1, "maximum": 32, "default": 16}
BUDGET = {"type": "integer", "minimum": 1, "maximum": 1_000_000, "default": 200_000}
OP = {
    "type": "object", "additionalProperties": False,
    "properties": {"op": {"enum": ["set", "fill", "replace", "properties"]}, "region": STRING,
                   "pos": POS, "box": BOX, "match": STRING, "state": STRING,
                   "properties": {"type": "object", "additionalProperties": STRING}},
    "required": ["op"],
}
PLAN = {"type": "object", "additionalProperties": False, "required": ["operations"],
        "properties": {"source_sha256": STRING,
                       "operations": {"type": "array", "items": OP, "minItems": 1, "maxItems": 64}}}


def definition(action, description, props=None, required=()):
    return {"name": "litematic_" + action, "description": description,
            "inputSchema": {"type": "object", "properties": COMMON | (props or {}),
                            "required": ["path", *required], "additionalProperties": False}}


TOOLS = [
    definition("summary", "Start here: file SHA256, regions and bounds; no full voxel dump.",
               {"region_offset": INT, "limit": LIMIT}),
    definition("query", "Find blocks by ID glob or exact state, or redstone category. Bounded scan with next_cursor; reuse identical filters and unchanged source SHA256 when paging.",
               SELECTION | {"match": STRING, "category": {"enum": ["all", "redstone"]},
                            "limit": {"type": "integer", "minimum": 1, "maximum": 128, "default": 32},
                            "cursor": INT, "max_scan": BUDGET}),
    definition("inspect", "Inspect one position and its six neighbors; outside region means unknown, not air.",
               REGION | {"pos": POS}, ("pos",)),
    definition("slice", "Compact ASCII plane and block-state legend; at most 4096 cells. Narrow box for large builds.",
               SELECTION | {"axis": {"enum": ["x", "y", "z"]}, "at": INT}, ("axis", "at")),
    definition("analyze", "Material counts, redstone component counts/facings and dense layers. Partial scans are explicit. Static layout only, no game simulation.",
               SELECTION | {"limit": LIMIT, "max_scan": BUDGET}),
    definition("edit", "Preview JSON plan by default. apply=true requires source_sha256 and a NEW output path. Protects block entities and scheduled ticks. Never overwrites. Property patches retain other properties but do not validate Minecraft registry values.",
               {"plan": PLAN, "output": STRING, "apply": {"type": "boolean", "default": False}}, ("plan",)),
    definition("validate", "Read all packed block states and validate NBT/palette integrity; does not simulate Minecraft."),
]


class Server:
    def __init__(self, root):
        self.root = Path(root).resolve(strict=True)
        if not self.root.is_dir():
            raise ValueError("MCP root must be a directory")

    def path(self, value):
        if not isinstance(value, str) or not value:
            raise ValueError("path must be a nonempty string")
        path = Path(value)
        path = (self.root / path).resolve() if not path.is_absolute() else path.resolve()
        if not path.is_relative_to(self.root) or path.suffix.lower() != ".litematic":
            raise ValueError("Input and output must be .litematic paths inside the configured root")
        return str(path)

    def call(self, name, arguments):
        tool = next((t for t in TOOLS if t["name"] == name), None)
        if tool is None:
            raise ValueError("Unknown tool")
        if not isinstance(arguments, dict):
            raise ValueError("arguments must be an object")
        schema = tool["inputSchema"]
        if set(arguments) - set(schema["properties"]) or set(schema["required"]) - set(arguments):
            raise ValueError("Unexpected or missing tool arguments")
        args = dict(arguments)
        path = self.path(args.pop("path"))
        if "output" in args:
            args["output"] = self.path(args["output"])
        if "apply" in args and type(args["apply"]) is not bool:
            raise ValueError("apply must be a boolean")
        return dispatch(name.removeprefix("litematic_"), path, **args)

    def handle(self, request):
        if not isinstance(request, dict) or request.get("jsonrpc") != "2.0" or not isinstance(request.get("method"), str):
            return {"jsonrpc": "2.0", "id": request.get("id") if isinstance(request, dict) else None,
                    "error": {"code": -32600, "message": "Invalid request"}}
        if "id" not in request:
            return None
        ident, method, params = request["id"], request["method"], request.get("params", {})
        response = {"jsonrpc": "2.0", "id": ident}
        if not isinstance(params, dict):
            response["error"] = {"code": -32602, "message": "params must be an object"}
            return response
        if method == "initialize":
            supported = ("2024-11-05", "2025-03-26", "2025-06-18", "2025-11-25")
            version = params.get("protocolVersion")
            result = {"protocolVersion": version if version in supported else supported[0],
                      "capabilities": {"tools": {"listChanged": False}},
                      "serverInfo": {"name": "litematic-agent", "version": __version__}}
        elif method == "ping":
            result = {}
        elif method == "tools/list":
            result = {"tools": TOOLS}
        elif method == "tools/call":
            try:
                value = self.call(params.get("name"), params.get("arguments", {}))
                result = {"content": [{"type": "text", "text": compact(value)}], "isError": False}
            except (ValueError, OSError, TypeError, KeyError, OverflowError, EOFError) as e:
                result = {"content": [{"type": "text", "text": json.dumps({"error": str(e)}, ensure_ascii=False)}], "isError": True}
        else:
            response["error"] = {"code": -32601, "message": "Method not found"}
            return response
        response["result"] = result
        return response


def main(argv=None):
    parser = argparse.ArgumentParser(description="Litematica MCP stdio server; no network listener")
    parser.add_argument("--root", required=True, help="Restrict all schematic reads/writes to this directory")
    args = parser.parse_args(argv)
    server = Server(args.root)
    while True:
        line = sys.stdin.buffer.readline(256_001)
        if not line:
            break
        if len(line) > 256_000:
            # Drain this one overlong message, then keep the stream synchronized.
            while not line.endswith(b"\n"):
                line = sys.stdin.buffer.readline(256_001)
                if not line:
                    break
            response = {"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": "Message exceeds 256000 bytes"}}
        else:
            try:
                response = server.handle(json.loads(line))
            except (ValueError, UnicodeError, RecursionError):
                response = {"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": "Invalid JSON"}}
        if response is not None:
            print(json.dumps(response, ensure_ascii=False, separators=(",", ":")), flush=True)


if __name__ == "__main__":
    main()
