#!/usr/bin/env python3
"""Export this game's translation inputs with Ghidra's own analysis.

    tools/analyze.py --ghidra-home /path/to/ghidra_12.1.3_PUBLIC [--java-home ...]

The kit's tools/setup.py imports a curated annotation set into Ghidra and
exports with analysis switched off, which suits a game whose functions were
recovered by hand. No such set exists for Petz 5.exe, so this script imports
the linked executable, runs Ghidra's default analyzers, and exports the same
listing layout the kit's translator reads (functions.tsv, functions/*.asm)
into ignored analysis/decompiled/Petz 5.exe with the kit's ExportProgram.java.
Run tools/setup.py --install ... --link-only first. The export of a 1.7 MB
.text section takes a long time and writes Ghidra's output to the terminal;
redirect it to a file if you want a log."""

import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
KIT = ROOT / "kit"
GHIDRA_VERSION = "12.1.3"


def ghidra_temp_parent():
    """A directory whose path Ghidra accepts as a project location.

    Ghidra refuses a path with an element that starts with '.', so a checkout
    (or a TMPDIR) under e.g. ~/.local or a hidden build directory cannot host
    the project. Try the system temp directory, then the user's cache/home."""
    candidates = [Path(tempfile.gettempdir()), Path.home() / "Library/Caches", Path.home()]
    for base in candidates:
        try:
            base = base.resolve()
        except OSError:
            continue
        if any(part.startswith(".") for part in base.parts) or not base.is_dir():
            continue
        return base
    sys.exit("No temporary directory without a dot-leading path element; set TMPDIR")


def load_game_config():
    spec = importlib.util.spec_from_file_location("game_config", KIT / "tools/game_config.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--ghidra-home", type=Path, default=os.environ.get("GHIDRA_HOME"))
    parser.add_argument("--java-home", type=Path, default=os.environ.get("JAVA_HOME"))
    parser.add_argument("--max-memory", default="8G", help="Ghidra's Java heap (default 8G)")
    args = parser.parse_args()
    if not (KIT / "tools/ExportProgram.java").is_file():
        sys.exit("The kit submodule is missing: run `git submodule update --init`")
    if not args.ghidra_home:
        sys.exit("Set --ghidra-home or GHIDRA_HOME to the extracted Ghidra %s directory" % GHIDRA_VERSION)
    ghidra = args.ghidra_home.expanduser().resolve()
    properties = ghidra / "Ghidra/application.properties"
    if not properties.is_file() or "application.version=%s\n" % GHIDRA_VERSION not in properties.read_text():
        sys.exit("Use Ghidra %s; set --ghidra-home to its extracted directory" % GHIDRA_VERSION)
    cfg = load_game_config().load(ROOT)
    exe = cfg["developer_exe_path"]
    if not exe.is_file():
        sys.exit("%s is missing: run tools/setup.py --install ... --link-only first" % exe)
    digest = hashlib.sha256(exe.read_bytes()).hexdigest()
    if digest != cfg["game"]["sha256"]:
        sys.exit("Unsupported %s: SHA-256 %s; expected %s" % (exe.name, digest, cfg["game"]["sha256"]))
    env = dict(os.environ)
    if args.java_home:
        env["JAVA_HOME"] = str(args.java_home.expanduser().resolve())
        env["PATH"] = str(Path(env["JAVA_HOME"]) / "bin") + os.pathsep + env.get("PATH", "")
    env["MAXMEM"] = args.max_memory
    listings = cfg["listings_path"]            # analysis/decompiled/Petz 5.exe
    output = listings.parent                   # analysis/decompiled
    output.mkdir(parents=True, exist_ok=True)
    # A disposable project keeps the export independent of the checkout's
    # path; -deleteProject empties it and the context removes the directory.
    with tempfile.TemporaryDirectory(prefix="catz5-ghidra-", dir=ghidra_temp_parent()) as project_dir:
        command = [
            str(ghidra / "support/analyzeHeadless"), project_dir, cfg["game"]["app_name"],
            "-import", str(exe), "-deleteProject",
            "-scriptPath", str(KIT / "tools"),
            "-postScript", "ExportProgram.java", str(output),
        ]
        subprocess.run(command, cwd=ROOT, env=env, check=True)
    index = listings / "functions.tsv"
    if not index.is_file() or len(index.read_text().splitlines()) < 2:
        sys.exit("Ghidra did not export a function index; inspect its output")
    (output / "inputs.json").write_text(json.dumps({
        "executable_sha256": cfg["game"]["sha256"],
        "annotations_revision": None,
        "ghidra_analysis": "default analyzers",
        "ghidra_version": GHIDRA_VERSION,
    }, indent=2) + "\n")
    print("Listings ready in %s. Next: tools/build.py --regenerate" % listings)


if __name__ == "__main__":
    main()
