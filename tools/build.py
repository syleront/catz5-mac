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

On macOS, a successful --target app build is always followed by the kit's
finish_bundle.py, even when ninja had no work. CMake regenerates the bundle's
Info.plist during a configure-only re-run, and a POST_BUILD command does not
run then, so the bundle would otherwise keep an invalid (stale) signature.
"""
import platform
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
KIT = ROOT / "kit"
CONTENT = ROOT / "tools/content_build.py"
FINISH = KIT / "tools/recomp/finish_bundle.py"
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


def _option(arguments, name, default=None):
    """Read `--name VALUE` or `--name=VALUE` without consuming the kit's parser."""
    for index, argument in enumerate(arguments):
        if argument == name and index + 1 < len(arguments):
            return arguments[index + 1]
        if argument.startswith(name + "="):
            return argument.split("=", 1)[1]
    return default


def finish_app(arguments):
    """Re-run finish_bundle.py so the bundle plist, resources and signature agree.

    Ninja's POST_BUILD is skipped on a configure-only re-run, yet CMake has
    already rewritten the bundle's Info.plist, leaving the ad-hoc signature
    stale. Running the finisher unconditionally closes that hole. The flags
    mirror kit/cmake/MacBundle.cmake (version and name come from game.toml; the
    optional FFmpeg dylibs from the build tree)."""
    import tomllib

    if platform.system() != "Darwin" or _option(arguments, "--target", "app") != "app":
        return 0
    if not FINISH.is_file():
        sys.exit("The kit finisher is missing: %s" % FINISH)
    cfg = tomllib.loads((ROOT / "game.toml").read_text())
    game = cfg["game"]
    config = _option(arguments, "--config", "Release")
    preset = _option(arguments, "--preset", "macos-debug" if config == "Debug" else "macos")
    if "--stub" in arguments:
        preset += "-stub"
    build_root = ROOT / "build"
    bundle = build_root / (game["app_name"] + ".app")
    if not bundle.is_dir():
        sys.exit("No bundle at %s after the build" % bundle)
    command = [sys.executable, str(FINISH),
               "--bundle", str(bundle),
               "--name", game["app_name"],
               "--cc", "/usr/bin/cc",
               "--version", game["version"],
               "--build", str(game["build"]),
               "--build-root", str(build_root),
               "--game-dir", str(ROOT)]
    libdir = build_root / "cmake" / preset / "ffmpeg" / "lib"
    for stem in ("libavformat", "libavcodec", "libavutil"):
        # Only the installed major-version links (libavformat.61.dylib), not
        # their fully versioned targets or the unversioned development names.
        libraries = sorted(path for path in libdir.glob(stem + ".*.dylib")
                           if path.name.count(".") == 2)
        command += [item for path in libraries for item in ("--ffmpeg-library", str(path))]
    return subprocess.call(command, cwd=KIT)


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
    code = subprocess.call([sys.executable, str(KIT / "tools/build.py"), "--game-dir", str(ROOT)] + rest,
                           cwd=ROOT)
    if code == 0:
        code = finish_app(rest)
    sys.exit(code)


if __name__ == "__main__":
    main()
