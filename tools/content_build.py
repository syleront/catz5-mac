#!/usr/bin/env python3
# Purpose: Prepare the translation inputs for the rebased Petz 5 / Catz 5 content
#          PE modules that game.toml pulls in through [modules] aux_file (see
#          tools/rebase_content.py). For every module in build/content/modules.toml
#          this runs Ghidra once per batch and exports the listing layout the kit's
#          translator reads -- functions.tsv plus functions/<addr>.asm -- into
#          build/content/listings/<module name>. The kit's own ExportProgram.java
#          also decompiles every function; the translator only uses the .c files
#          for an advisory jump-table cross-check (a stat, not a decision), so
#          tools/ghidra/ExportListings.java skips the decompiler and removes the
#          dominant cost of exporting hundreds of small PEs.
#
#          Listings are cached by the SHA-256 of the rebased image: a module whose
#          stamp still matches is skipped, so reruns are incremental. Several
#          modules go through one analyzeHeadless invocation to amortise JVM and
#          Ghidra startup, and several invocations run in parallel.
#
# Usage:   .venv/bin/python tools/content_build.py [--only GLOB]... [--limit N] \
#              [--jobs N] [--batch N] [--max-memory 2G] [--no-rebase] [--force] \
#              [--list] [--ghidra-home DIR] [--java-home DIR]
#
#          --only/--limit select a trial subset; they are also passed to
#          tools/rebase_content.py so the fragment and the listings agree (bases
#          stay those of the full assignment). --list only reports which modules
#          already have cached listings and exits.
#
#          After this, tools/build.py --regenerate translates and links exactly
#          the modules in the fragment.
#
# Requires Ghidra 12.1.3 (--ghidra-home or $GHIDRA_HOME) and a JDK that
# distribution supports (--java-home or $JAVA_HOME; OpenJDK 26.0.1 is what the
# kit documents).

import argparse
import fnmatch
import hashlib
import importlib.util
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile

try:
    import tomllib
except ModuleNotFoundError:  # Python < 3.11
    import tomli as tomllib

ROOT = Path(__file__).resolve().parents[1]
REBASE = ROOT / "tools/rebase_content.py"
GHIDRA_VERSION = "12.1.3"
FRAGMENT = ROOT / "build/content/modules.toml"
LISTINGS_ROOT = ROOT / "build/content/listings"
EXPORT_SCRIPT_DIR = ROOT / "tools/ghidra"
EXPORT_SCRIPT = "ExportListings.java"
STAMP = ".source.sha256"
GHIDRA_STAGE = ROOT / "build/content/ghidra-stage"
# HeadlessAnalyzer refuses a filename with any of these (and control bytes), so
# a module such as "Jack O'Lantern.toy" must be imported from a staged copy.
GHIDRA_INVALID_NAME = re.compile(r"['\"*:<>?/\\|\x00-\x1f]")


def ghidra_safe_name(name, used):
    """A copy name Ghidra accepts, or None when the name is already safe.
    `used` keeps the copies in one batch unique."""
    safe = GHIDRA_INVALID_NAME.sub("_", name)
    if safe == name:
        return None
    stem, extension = os.path.splitext(safe)
    candidate = safe
    index = 1
    while candidate in used:
        candidate = "%s_%d%s" % (stem, index, extension)
        index += 1
    used.add(candidate)
    return candidate


def die(message):
    sys.exit("content_build: %s" % message)


def sha256_file(path):
    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        for block in iter(lambda: fh.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def read_fragment(path):
    """The [modules.aux.*] tables as records with resolved absolute paths."""
    with open(path, "rb") as fh:
        data = tomllib.load(fh)
    aux = data.get("modules", {}).get("aux", {})
    modules = []
    for key in sorted(aux):
        entry = aux[key]
        listings = entry.get("listings", "analysis/" + entry["name"])
        modules.append({
            "key": key,
            "name": entry["name"],
            "path": (ROOT / entry["path"]).resolve(),
            "listings": (ROOT / listings).resolve(),
            "sha256": entry["sha256"],
            "base": int(entry["base"]),
            "size": int(entry["size"]),
        })
    return modules


def select(modules, only, limit):
    """Filter records by --only (relative path or file name, case-insensitive)
    and --limit, in the fragment's order."""
    chosen = modules
    if only:
        patterns = [p.lower() for p in only]
        chosen = [m for m in chosen
                  if any(fnmatch.fnmatchcase(m["path"].as_posix().lower(), p)
                         or fnmatch.fnmatchcase(m["name"].lower(), p)
                         for p in patterns)]
    if limit is not None:
        chosen = chosen[:limit]
    return chosen


def cached(module):
    """True when the listing's stamp matches the rebased image and the index
    survives: a module that changed is re-exported, an unchanged one is skipped."""
    index = module["listings"] / "functions.tsv"
    stamp = module["listings"] / STAMP
    if not index.is_file() or not stamp.is_file():
        return False
    if not module["path"].is_file():
        return False
    return stamp.read_text().strip() == module["sha256"]


def fragment_matches(only, limit):
    """Whether the fragment on disk already is the requested selection, so a
    rerun of the rebaser can be skipped."""
    if not FRAGMENT.is_file():
        return False
    try:
        modules = read_fragment(FRAGMENT)
    except (OSError, tomllib.TOMLDecodeError, KeyError):
        return False
    wanted = select(modules, only, limit)
    if len(wanted) != len(modules):
        return False
    return all(m["path"].is_file() for m in modules)


def game_dir():
    """The configured installed-game directory (game.toml's developer_exe
    parent), so the rebaser reads the same copy `tools/setup.py` linked."""
    path = ROOT / "kit/tools/game_config.py"
    if not path.is_file():
        die("the kit submodule is missing: run `git submodule update --init`")
    spec = importlib.util.spec_from_file_location("game_config", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    try:
        cfg = module.load(ROOT)
    except (OSError, ValueError) as error:
        die(str(error))
    return cfg["developer_exe_path"].parent


def run_rebase(only, limit):
    command = [sys.executable, str(REBASE), str(game_dir())]
    for pattern in only or ():
        command += ["--only", pattern]
    if limit is not None:
        command += ["--limit", str(limit)]
    print("rebase: %s" % " ".join(command[1:]), flush=True)
    completed = subprocess.run(command, cwd=ROOT)
    if completed.returncode:
        die("rebasing the content modules failed (rc=%d)" % completed.returncode)


def ensure_fragment(only, limit, no_rebase, force):
    """Run the rebaser if it is stale, missing or asked for. A module whose
    image is missing, a fragment older than tools/rebase_content.py, a fresh
    checkout with no fragment, or an explicit selection all trigger it."""
    if no_rebase:
        if not FRAGMENT.is_file():
            die("%s is missing and --no-rebase was given" % FRAGMENT)
        return
    stale = (force or not FRAGMENT.is_file()
             or REBASE.stat().st_mtime > FRAGMENT.stat().st_mtime
             or (only is not None or limit is not None) and not fragment_matches(only, limit))
    if not stale:
        try:
            stale = any(not m["path"].is_file() for m in read_fragment(FRAGMENT))
        except (OSError, tomllib.TOMLDecodeError, KeyError):
            stale = True
    if stale:
        run_rebase(only, limit)


def find_ghidra(explicit):
    for candidate in (explicit, os.environ.get("GHIDRA_HOME")):
        if not candidate:
            continue
        path = Path(candidate).expanduser()
        properties = path / "Ghidra/application.properties"
        if properties.is_file() and ("application.version=%s\n" % GHIDRA_VERSION) in properties.read_text():
            return path.resolve()
    die("Ghidra %s not found: set --ghidra-home or $GHIDRA_HOME" % GHIDRA_VERSION)


def ghidra_project_parent():
    """A directory whose path Ghidra accepts as a project location.

    Ghidra refuses a path with an element that starts with '.', so a checkout
    (or a TMPDIR) under e.g. ~/.local or a hidden build directory cannot host
    the project. Try the system temp directory, then the user's cache/home."""
    for base in (Path(tempfile.gettempdir()), Path.home() / "Library/Caches", Path.home()):
        try:
            base = base.resolve()
        except OSError:
            continue
        if any(part.startswith(".") for part in base.parts) or not base.is_dir():
            continue
        return base
    die("no temporary directory without a dot-leading path element; set TMPDIR")


def ghidra_env(java_home):
    env = dict(os.environ)
    if java_home:
        home = Path(java_home).expanduser().resolve()
        env["JAVA_HOME"] = str(home)
        env["PATH"] = str(home / "bin") + os.pathsep + env.get("PATH", "")
    return env


def export_batch(ghidra, env, max_memory, max_cpu, index, batch, log_dir, project_root):
    """One analyzeHeadless invocation importing every module in `batch`; the
    post-script writes <listings root>/<name>/ for each. A module whose file
    name Ghidra rejects is imported from a staged copy and the export renamed
    back, so the listings directory still matches the fragment. Returns
    (ok, log path)."""
    name = "content-batch-%02d" % index
    log_path = log_dir / ("%s.log" % name)
    staging = GHIDRA_STAGE / name
    used = set()
    imports = []
    renames = {}
    for module in batch:
        safe = ghidra_safe_name(module["name"], used)
        if safe is None:
            imports.append(module["path"])
        else:
            staging.mkdir(parents=True, exist_ok=True)
            copy = staging / safe
            shutil.copy2(module["path"], copy)
            imports.append(copy)
            renames[safe] = module["name"]
    # Ghidra creates the project inside this directory and needs it to exist.
    project = project_root / name
    project.mkdir(parents=True, exist_ok=True)
    command = [
        str(ghidra / "support/analyzeHeadless"), str(project), name,
        "-import", *[str(path) for path in imports],
        "-deleteProject",
        "-scriptPath", str(EXPORT_SCRIPT_DIR),
        "-postScript", EXPORT_SCRIPT, str(LISTINGS_ROOT),
    ]
    if max_cpu:
        command += ["-max-cpu", str(max_cpu)]
    local_env = dict(env)
    local_env["MAXMEM"] = max_memory
    print("ghidra[%02d]: %d module(s) -> %s" % (index, len(batch), log_path.name), flush=True)
    with open(log_path, "w") as log:
        completed = subprocess.run(command, cwd=ROOT, env=local_env,
                                   stdout=log, stderr=subprocess.STDOUT)
    if completed.returncode != 0:
        print("ghidra[%02d]: FAILED rc=%d (%s)" % (index, completed.returncode, log_path), flush=True)
        return False, log_path
    for safe, original in renames.items():
        source = LISTINGS_ROOT / safe
        if source.is_dir():
            shutil.rmtree(LISTINGS_ROOT / original, ignore_errors=True)
            source.rename(LISTINGS_ROOT / original)
    shutil.rmtree(staging, ignore_errors=True)
    missing = [m["name"] for m in batch if not (m["listings"] / "functions.tsv").is_file()]
    if missing:
        print("ghidra[%02d]: FAILED, no functions.tsv for %s (%s)"
              % (index, ", ".join(missing), log_path), flush=True)
        return False, log_path
    for m in batch:
        (m["listings"] / STAMP).write_text(m["sha256"] + "\n")
    return True, log_path


def batches(items, size):
    for start in range(0, len(items), size):
        yield items[start:start + size]


def main():
    parser = argparse.ArgumentParser(description="Export Ghidra listings for the content aux modules")
    parser.add_argument("--only", action="append", metavar="GLOB", default=None,
                        help="only modules whose path or name matches (repeatable)")
    parser.add_argument("--limit", type=int, default=None, metavar="N",
                        help="only the first N selected modules (a trial subset)")
    parser.add_argument("--jobs", type=int, default=min(os.cpu_count() or 2, 8),
                        help="parallel analyzeHeadless invocations (default: min(cores, 8))")
    parser.add_argument("--batch", type=int, default=24, metavar="N",
                        help="modules imported per analyzeHeadless invocation (default 24)")
    parser.add_argument("--max-memory", default="2G", help="Ghidra Java heap per process (default 2G)")
    parser.add_argument("--max-cpu", type=int, default=1,
                        help="analysis threads per Ghidra process (default 1, so jobs can overlap)")
    parser.add_argument("--force", action="store_true", help="re-export listings even when cached")
    parser.add_argument("--no-rebase", action="store_true", help="do not run tools/rebase_content.py")
    parser.add_argument("--list", action="store_true", help="list cached modules and exit")
    parser.add_argument("--ghidra-home", type=Path, default=None)
    parser.add_argument("--java-home", type=Path, default=None)
    args = parser.parse_args()

    if args.limit is not None and args.limit < 1:
        parser.error("--limit must be at least 1")
    if args.jobs < 1 or args.batch < 1:
        parser.error("--jobs and --batch must be at least 1")

    ensure_fragment(args.only, args.limit, args.no_rebase, args.force)
    modules = read_fragment(FRAGMENT)
    targets = select(modules, args.only, args.limit)
    if not targets:
        die("no modules selected from %s" % FRAGMENT)

    cached_count = sum(1 for m in targets if cached(m))
    if args.list:
        for m in targets:
            print("%-8s %-40s %s" % ("cached" if cached(m) else "missing", m["name"],
                                     m["listings"].relative_to(ROOT)))
        print("%d/%d cached" % (cached_count, len(targets)))
        return

    pending = targets if args.force else [m for m in targets if not cached(m)]
    print("modules: %d selected, %d cached, %d to export" % (len(targets), cached_count, len(pending)))
    if pending:
        ghidra = find_ghidra(args.ghidra_home)
        env = ghidra_env(args.java_home)
        if not EXPORT_SCRIPT_DIR.is_dir():
            die("missing %s" % EXPORT_SCRIPT_DIR)
        LISTINGS_ROOT.mkdir(parents=True, exist_ok=True)
        log_dir = ROOT / "build/content/ghidra-logs"
        log_dir.mkdir(parents=True, exist_ok=True)
        project_root = Path(tempfile.mkdtemp(prefix="catz5-content-projects-",
                                             dir=ghidra_project_parent()))
        # Run batches with a bounded thread pool; subprocess.run releases the GIL.
        from concurrent.futures import ThreadPoolExecutor
        work = list(batches(pending, args.batch))
        failures = []
        try:
            with ThreadPoolExecutor(max_workers=min(args.jobs, len(work))) as pool:
                futures = {pool.submit(export_batch, ghidra, env, args.max_memory, args.max_cpu,
                                       index + 1, batch, log_dir, project_root): batch
                           for index, batch in enumerate(work)}
                for future, batch in futures.items():
                    ok, log_path = future.result()
                    if not ok:
                        failures.append((batch, log_path))
        finally:
            shutil.rmtree(project_root, ignore_errors=True)
        if failures:
            for batch, log_path in failures:
                print("failed batch: %s (%s)" % (", ".join(m["name"] for m in batch), log_path))
            die("%d of %d batches failed" % (len(failures), len(work)))

    missing = [m["name"] for m in targets if not cached(m)]
    if missing:
        die("listings still missing for %s" % ", ".join(missing))
    print("listings ready for %d module(s) under %s" % (len(targets), LISTINGS_ROOT.relative_to(ROOT)))
    print("next: .venv/bin/python tools/build.py --regenerate  (translates the fragment's modules)")


if __name__ == "__main__":
    main()
