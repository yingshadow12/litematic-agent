#!/usr/bin/env python3
"""Export one portable skill directory with its dependency-free Python library."""
import argparse
from pathlib import Path
import zipfile


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", default="dist/litematic-agent-skill.zip")
    args = parser.parse_args()
    repo = Path(__file__).resolve().parents[1]
    skill = repo / ".agents/skills/litematic-agent"
    output = Path(args.output).resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(output, "x", compression=zipfile.ZIP_DEFLATED) as archive:
        for file in sorted(skill.rglob("*")):
            if file.is_file() and "__pycache__" not in file.parts:
                archive.write(file, Path("litematic-agent") / file.relative_to(skill))
        for file in sorted((repo / "src/litematic_agent").glob("*.py")):
            archive.write(file, Path("litematic-agent/scripts/lib/litematic_agent") / file.name)
    print(output)


if __name__ == "__main__":
    main()
