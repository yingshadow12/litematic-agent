#!/usr/bin/env python3
"""Run from a checkout OR a copied skill bundle, with no pip installation."""
from pathlib import Path
import sys

here = Path(__file__).resolve()
bundled = here.parent / "lib"
source = next((p / "src" for p in here.parents if (p / "src/litematic_agent").is_dir()), None)
if bundled.is_dir():
    sys.path.insert(0, str(bundled))
elif source:
    sys.path.insert(0, str(source))
else:
    sys.exit("Missing tool library; use the repository skill or the exported skill bundle")

if len(sys.argv) > 1 and sys.argv[1] == "mcp":
    from litematic_agent.mcp import main
    main(sys.argv[2:])
else:
    from litematic_agent.cli import main
    sys.exit(main())
