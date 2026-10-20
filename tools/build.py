#!/usr/bin/env python3
"""Build Catz 5 with the kit in kit/. Every option is the kit's: see kit/tools/build.py --help.

--content (this project's own flag) prepares the rebased content modules' Ghidra
listings with tools/content_build.py *before* the kit build starts, so the kit
translates exactly the [modules.aux.*] tables in game.toml's aux_file fragment.
Its own knobs are prefixed so they never collide with the kit's:

    --content                 run tools/content_build.py first
    --content-only GLOB       (repeatable) pass --only to content_build
    --content-limit N         pass --limit to content_build
    --content-jobs N          pass --jobs to content_build
    --content-batch N         pass --batch to content_build
    --content-force           re-export cached listings
    --content-no-rebase       do not run tools/rebase_content.py
    --ghidra-home DIR         Ghidra 12.1.3 for content_build (default $GHIDRA_HOME)
    --java-home DIR           JDK for Ghidra's headless analyzer (default $JAVA_HOME)

Any of these content options (including the two tool paths) runs the content
step, exactly as --content does.
"""
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
KIT = ROOT / "kit"
CONTENT = ROOT / "tools/content_build.py"
# This project's flags before --content and the name content_build knows them by.
CONTENT_VALUE_FLAGS = {"--content-only": "--only", "--content-limit": "--limit",
                       "--content-jobs": "--jobs", "--content-batch": "--batch",
                       "--ghidra-home": "--ghidra-home", "--java-home": "--java-home"}
CONTENT_BOOL_FLAGS = {"--content-force": "--force", "--content-no-rebase": "--no-rebase"}


def split_content_flags(argv):
    """Separate --content and its prefixed options from the kit's own arguments."""
    content, rest = [], []
    index = 0
    while index < len(argv):
        arg = argv[index]
        if arg == "--content":
            content.append("--content")
        elif arg in CONTENT_VALUE_FLAGS:
            if index + 1 >= len(argv):
                sys.exit("%s needs a value" % arg)
            index += 1
            content += [CONTENT_VALUE_FLAGS[arg], argv[index]]
        elif arg in CONTENT_BOOL_FLAGS:
            content.append(CONTENT_BOOL_FLAGS[arg])
        else:
            rest.append(arg)
        index += 1
    return content, rest


def main():
    if not (KIT / "tools/build.py").is_file():
        sys.exit("The kit submodule is missing: run `git submodule update --init`")
    if any(arg in ("-h", "--help") for arg in sys.argv[1:]):
        # Show this wrapper's content options before the kit's own help; the
        # kit's parser knows none of them.
        print(__doc__.strip(), flush=True)
        print("\nKit build options (everything else is passed to kit/tools/build.py):\n", flush=True)
        sys.exit(subprocess.call([sys.executable, str(KIT / "tools/build.py"), "--help"]))
    content, rest = split_content_flags(sys.argv[1:])
    if content:
        if not CONTENT.is_file():
            sys.exit("%s is missing" % CONTENT)
        command = [sys.executable, str(CONTENT)] + [a for a in content if a != "--content"]
        code = subprocess.call(command, cwd=ROOT)
        if code:
            sys.exit(code)
    # Addresses a run reached that the translation does not carry (see
    # discovered.txt). The kit forwards --discovered to the executable and every
    # aux module, so one real run closes content-module listing gaps too.
    discovery = ROOT / "discovered.txt"
    if discovery.is_file() and "--discovered" not in rest:
        rest += ["--discovered", str(discovery)]
    sys.exit(subprocess.call([sys.executable, str(KIT / "tools/build.py"), "--game-dir", str(ROOT)] + rest,
                             cwd=ROOT))


if __name__ == "__main__":
    main()
