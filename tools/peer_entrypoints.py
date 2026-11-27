"""Read launch metadata only; never import or execute a partner module."""
from pathlib import Path
import ast
import json
import re
import zipfile

from peer_inventory import PACKAGES, inventory


def main():
    root = Path(__file__).resolve().parents[1]
    result = []
    for package in PACKAGES:
        row = inventory(root, package)
        commands = []
        cli = {}
        with zipfile.ZipFile(root / package) as archive:
            for name in archive.namelist():
                base = Path(name).name.lower()
                if base.startswith("readme") and archive.getinfo(name).file_size < 200000:
                    for line in archive.read(name).decode("utf-8", errors="replace").splitlines():
                        line = line.strip().strip("`").strip()
                        # Only documented local invocation syntax, not arbitrary archive content.
                        if re.match(r"^(?:python(?:3|\.exe)?\s+|py\s+|node\s+|npm\s+(?:run|start|test)\b|uvicorn\s+)", line) and len(line)<250:
                            if not re.search(r"token|secret|password|api[_-]?key|https://", line, re.I):
                                commands.append(line)
                if base in ("controller.py", "environment.py", "__main__.py", "server.py"):
                    if archive.getinfo(name).file_size > 2000000:
                        continue
                    try:
                        tree = ast.parse(archive.read(name).decode("utf-8-sig"))
                    except (SyntaxError, UnicodeError):
                        continue
                    flags = set()
                    for node in ast.walk(tree):
                        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr in ("add_argument", "add_parser"):
                            for arg in node.args:
                                if isinstance(arg, ast.Constant) and isinstance(arg.value, str) and len(arg.value)<80:
                                    flags.add(arg.value)
                    cli[name] = sorted(flags)
                if base == "package.json":
                    try:
                        info = json.loads(archive.read(name))
                    except (ValueError, UnicodeError):
                        continue
                    cli[name] = {"scripts": sorted(info.get("scripts",{})), "dependency_names": sorted(info.get("dependencies",{}))}
        result.append({"path": package, "commands": commands[:40], "cli": cli})
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
