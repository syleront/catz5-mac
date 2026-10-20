#!/usr/bin/env python3
# Purpose: Rebase every Petz 5 / Catz 5 content PE module (.toy/.clo/.cat/.dog/
#          .env under the installed game directory, excluding the PetzPlayer copies and the
#          Resource/*.dll resource-only DLLs) to a unique guest base so the
#          recomp-kit's existing [modules.aux.<key>] machinery can translate and
#          map them beside the main image. The kit loader has no relocation
#          support (kit/runtime/loader.cpp:364-371) and requires every module to
#          sit at its own PE ImageBase; all content modules prefer 0x10000000
#          and collide, so the fix is to pre-relocate them at build time and
#          hand each one a distinct base above the shim region.
#
#          Outputs, all under build/ (git-ignored):
#            build/content/<relative path>   the rebased PE images
#            build/content/modules.toml      a TOML fragment of [modules.aux.*]
#
#          This is the interim, build-time route. A follow-up that translates
#          each distinct body once and emits module_base+offset is not
#          implemented here.
#
# Usage:   .venv/bin/python tools/rebase_content.py [pcgame_dir=original/pcgame] \
#              [--out build/content] [--toml build/content/modules.toml] \
#              [--alloc-base 0x10000000] [--verify N] [--seed S] \
#              [--only GLOB]... [--limit N]
#
#          --verify N re-parses N randomly chosen rebased images, checks
#          ImageBase == assigned base, and checks every HIGHLOW reloc target
#          equals original + delta. It recomputes the same deterministic
#          assignment and reads the files already written.
#
#          --only GLOB / --limit N write a subset: a trial fragment with only
#          the matching modules. Bases are still assigned over the full set, so
#          a subset build and a full build agree on every module's base and on
#          guest_size, and a later full run needs no re-translation of the
#          modules the trial already covered.
#
# Requires pefile in .venv (2024.8.26+). No kit files are modified.

import argparse
import fnmatch
import hashlib
import random
import sys
from pathlib import Path

# Where tools/content_build.py caches each module's Ghidra listing. The
# fragment names it per module so game_config resolves it relative to the game
# directory; this script only writes the path string.
LISTINGS_DIR = "build/content/listings"

import pefile

ROOT = Path(__file__).resolve().parents[1]

# Content module extensions. Everything else (the resource-only DLLs, the
# ActiveX control, data files) is left alone.
CONTENT_EXTS = {".toy", ".clo", ".cat", ".dog", ".env"}

# Layout constants, kept in step with kit/runtime by hand (this script is a
# build-time tool and must not import kit code):
#   GUEST_SHIM_END 0x10000000  kit/runtime/x86.h:71  - first base an aux module
#                              may use (kit/runtime/loader.cpp:372)
#   GUEST_SIZE     0x10000000  kit/runtime/x86.h:44  - default arena
# The heap arena (0x01000000..0x0e000000), stack, TEB and import-shim
# trampolines all live below 0x10000000, so packing above GUEST_SHIM_END never
# overlaps them.
GUEST_SHIM_END = 0x10000000
GUEST_SIZE_DEFAULT = 0x10000000
ALLOC_ALIGN = 0x10000  # 64 KiB between module bases
PAGE = 0x1000


def is_pe(head: bytes) -> bool:
    if len(head) < 0x40 or head[:2] != b"MZ":
        return False
    nt = int.from_bytes(head[0x3C:0x40], "little")
    if nt + 4 > len(head):
        return False
    return head[nt:nt + 4] == b"PE\0\0"


def align_up(value: int, alignment: int) -> int:
    return (value + alignment - 1) // alignment * alignment


def read_highlow(pe):
    """Return the HIGHLOW (type 3) reloc RVAs. Any other non-padding type is
    an error: the loader applies no relocations at all, and this tool only
    knows how to add a delta to a 32-bit dword (IMAGE_REL_BASED_HIGHLOW)."""
    bdir = pe.OPTIONAL_HEADER.DATA_DIRECTORY[
        pefile.DIRECTORY_ENTRY["IMAGE_DIRECTORY_ENTRY_BASERELOC"]]
    if not bdir.VirtualAddress or not bdir.Size:
        raise ValueError("no base-relocation directory")
    raw = pe.get_data(bdir.VirtualAddress, bdir.Size)
    out = []
    i = 0
    while i + 8 <= len(raw):
        va = int.from_bytes(raw[i:i + 4], "little")
        sz = int.from_bytes(raw[i + 4:i + 8], "little")
        if sz < 8:
            break
        for k in range((sz - 8) // 2):
            e = int.from_bytes(raw[i + 8 + k * 2:i + 10 + k * 2], "little")
            kind = e >> 12
            if kind == 3:  # IMAGE_REL_BASED_HIGHLOW
                out.append(va + (e & 0xFFF))
            elif kind != 0:  # 0 = IMAGE_REL_BASED_ABSOLUTE padding
                raise ValueError("unsupported relocation type %d at %#x"
                                 % (kind, va + (e & 0xFFF)))
        i += sz
    return out


def image_base_field_offset(pe) -> int:
    """File offset of OptionalHeader.ImageBase: pe_off + 4 (COFF) + 20
    (optional header up to and including the 4-byte Magic comes first)."""
    return pe.DOS_HEADER.e_lfanew + 4 + 20 + 28


def apply_rebase(dst: Path, pe, new_base: int) -> int:
    """Write the image to `dst` with every HIGHLOW target and ImageBase moved
    by (new_base - old_base). Returns the number of relocations applied."""
    if pe.OPTIONAL_HEADER.Magic != 0x10B:
        raise ValueError("not a PE32 image")
    old_base = pe.OPTIONAL_HEADER.ImageBase
    delta = new_base - old_base
    rvas = read_highlow(pe)
    data = bytearray(pe.__data__)
    for rva in rvas:
        off = pe.get_offset_from_rva(rva)
        val = int.from_bytes(data[off:off + 4], "little")
        data[off:off + 4] = ((val + delta) & 0xFFFFFFFF).to_bytes(4, "little")
    ib_off = image_base_field_offset(pe)
    data[ib_off:ib_off + 4] = (new_base & 0xFFFFFFFF).to_bytes(4, "little")
    dst.parent.mkdir(parents=True, exist_ok=True)
    dst.write_bytes(bytes(data))
    return len(rvas)


def sanitize_key(rel: Path) -> str:
    """A key usable as a CMake list entry, an output directory name and a C
    symbol prefix: [a-z0-9_], unique because it spells the whole relative path."""
    text = rel.as_posix().lower()
    return "".join(c if c.isalnum() else "_" for c in text).strip("_")


def scan(pcgame: Path):
    """Return (content, resource_dlls, petzplayer) as lists of Paths."""
    content, resource_dlls, petzplayer = [], [], []
    for path in sorted(pcgame.rglob("*")):
        if not path.is_file():
            continue
        rel = path.relative_to(pcgame)
        try:
            head = path.open("rb").read(0x400)
        except OSError:
            continue
        if not is_pe(head):
            continue
        if rel.parts and rel.parts[0] == "PetzPlayer":
            petzplayer.append(path)
            continue
        if path.suffix.lower() in CONTENT_EXTS:
            content.append(path)
        elif path.suffix.lower() == ".dll":
            resource_dlls.append(path)
    return content, resource_dlls, petzplayer


def collect(pcgame: Path, alloc_base: int):
    """Parse every content module and assign a base in scan order. Returns
    (records, resource_dlls, petzplayer, guest_size); records carry the parsed
    pe but nothing is written yet."""
    content, resource_dlls, petzplayer = scan(pcgame)
    records = []
    cursor = align_up(alloc_base, PAGE)
    for path in content:
        rel = path.relative_to(pcgame)
        try:
            pe = pefile.PE(str(path), fast_load=True)
            pe.parse_data_directories(directories=[
                pefile.DIRECTORY_ENTRY["IMAGE_DIRECTORY_ENTRY_BASERELOC"]])
        except pefile.PEFormatError as exc:
            print("  !! skipping %s: %s" % (rel, exc))
            continue
        size = pe.OPTIONAL_HEADER.SizeOfImage
        base = align_up(cursor, ALLOC_ALIGN)
        records.append({
            "src": path, "rel": rel, "pe": pe, "size": size,
            "old_base": pe.OPTIONAL_HEADER.ImageBase, "base": base,
        })
        cursor = base + size
    guest_size = max(align_up(cursor, PAGE), GUEST_SIZE_DEFAULT)
    return records, resource_dlls, petzplayer, guest_size


def select_subset(records, only, limit):
    """Filter records for a trial fragment. Bases were assigned over the full
    set, so selection changes only which images get written, never a base."""
    selected = records
    if only:
        patterns = [p.lower() for p in only]
        selected = [r for r in selected
                    if any(fnmatch.fnmatchcase(r["rel"].as_posix().lower(), p)
                           or fnmatch.fnmatchcase(r["rel"].name.lower(), p)
                           for p in patterns)]
    if limit is not None:
        selected = selected[:limit]
    return selected


def render_toml(records, guest_size) -> str:
    lines = [
        "# Generated by tools/rebase_content.py -- do not edit.",
        "# A TOML fragment for game.toml's [modules.aux.*] tables: every Catz 5",
        "# content PE (.toy/.clo/.cat/.dog/.env), rebased to a unique guest base",
        "# so the recomp-kit loader (which does not relocate) can map it.",
        "#",
        "# [game] guest_size must be at least 0x%08x; the tables below are merged" % guest_size,
        "# into game.toml (tomllib has no include directive, so the build",
        "# integration merges this fragment into [modules.aux].",
        "",
        "[game]",
        "guest_size = 0x%08x" % guest_size,
        "",
    ]
    for r in records:
        lines.append("[modules.aux.%s]" % r["key"])
        lines.append('name = "%s"' % r["name"])
        lines.append('path = "%s"' % r["path"])
        lines.append('sha256 = "%s"' % r["sha256"])
        lines.append("base = 0x%08x" % r["base"])
        lines.append("size = 0x%08x" % r["size"])
        lines.append('listings = "%s/%s"' % (LISTINGS_DIR, r["name"]))
        lines.append("")
    return "\n".join(lines)


def build(pcgame: Path, out_dir: Path, toml_path: Path, alloc_base: int, only=None, limit=None):
    records, resource_dlls, petzplayer, guest_size = collect(pcgame, alloc_base)
    total = len(records)
    selected = select_subset(records, only, limit)

    # Duplicate basenames are a blocker: kernel32.cpp's load_library_named
    # matches by basename only (library_name @1084, loader_module_named @446),
    # so two modules with the same file name cannot both be addressed.
    by_name = {}
    for r in records:
        by_name.setdefault(r["rel"].name.lower(), []).append(r["rel"])
    dupes = {k: v for k, v in by_name.items() if len(v) > 1}

    mapped_bytes = 0
    seen = {}
    for r in records:
        r["key"] = sanitize_key(r["rel"])
        r["name"] = r["rel"].name
        if r["key"] in seen:
            raise SystemExit("key %r collides (%s and %s)"
                             % (r["key"], seen[r["key"]], r["rel"]))
        seen[r["key"]] = r["rel"]

    for r in selected:
        dst = out_dir / r["rel"]
        r["relocs"] = apply_rebase(dst, r["pe"], r["base"])
        r["sha256"] = hashlib.sha256(dst.read_bytes()).hexdigest()
        r["path"] = dst.relative_to(ROOT).as_posix()
        mapped_bytes += r["size"]

    toml_path.parent.mkdir(parents=True, exist_ok=True)
    toml_path.write_text(render_toml(selected, guest_size))

    first = selected[0]["base"] if selected else 0
    last = selected[-1]["base"] + selected[-1]["size"] if selected else 0

    print("scanned %s" % pcgame)
    print("  content modules:        %d%s (.toy/.clo/.cat/.dog/.env, PetzPlayer excluded)"
          % (len(selected), "" if len(selected) == total else " of %d" % total))
    print("  resource-only DLLs:     %d (excluded, stay data-only)" % len(resource_dlls))
    print("  PetzPlayer copies:      %d (excluded)" % len(petzplayer))
    print("  total mapped bytes:     %d (%#x)" % (mapped_bytes, mapped_bytes))
    print("  base range:             %#010x .. %#010x" % (first, last))
    print("  required guest_size:    %#010x (%d MiB), default %#010x"
          % (guest_size, guest_size >> 20, GUEST_SIZE_DEFAULT))
    print("  relocations applied:    %d" % sum(r["relocs"] for r in selected))
    print("  wrote %d images under %s" % (len(selected), out_dir.relative_to(ROOT)))
    print("  wrote %s" % toml_path.relative_to(ROOT))
    if dupes:
        print("\nBLOCKER: %d basename(s) shared by more than one module; the loader" % len(dupes))
        print("matches LoadLibrary by basename only and cannot distinguish them:")
        for name, rels in sorted(dupes.items()):
            print("  %s: %s" % (name, ", ".join(str(p) for p in rels)))
    else:
        print("\nno duplicate basenames: every LoadLibrary name is unambiguous")
    return selected, guest_size


def verify(pcgame: Path, out_dir: Path, count: int, seed: int) -> int:
    """Re-parse `count` random rebased images and check ImageBase and every
    reloc target against the original + delta."""
    records, _, _, _ = collect(pcgame, GUEST_SHIM_END)
    rng = random.Random(seed)
    picks = rng.sample(records, min(count, len(records)))
    ok = True
    for r in picks:
        dst = out_dir / r["rel"]
        delta = r["base"] - r["old_base"]
        reb = pefile.PE(str(dst), fast_load=True)
        reb.parse_data_directories(directories=[
            pefile.DIRECTORY_ENTRY["IMAGE_DIRECTORY_ENTRY_BASERELOC"]])
        good = True
        if reb.OPTIONAL_HEADER.ImageBase != r["base"]:
            print("  %s: ImageBase %#x, expected %#x"
                  % (r["rel"], reb.OPTIONAL_HEADER.ImageBase, r["base"]))
            good = False
        orig_rvas = read_highlow(r["pe"])
        reb_rvas = read_highlow(reb)
        if orig_rvas != reb_rvas:
            print("  %s: reloc RVA set changed after rebase" % r["rel"])
            good = False
        mism = 0
        for rva in orig_rvas:
            o = int.from_bytes(r["pe"].get_data(rva, 4), "little")
            n = int.from_bytes(reb.get_data(rva, 4), "little")
            if n != ((o + delta) & 0xFFFFFFFF):
                mism += 1
        if mism:
            print("  %s: %d/%d reloc targets wrong" % (r["rel"], mism, len(orig_rvas)))
            good = False
        print("  %s: base %#010x (orig %#010x) relocs %d -> %s"
              % (r["rel"], reb.OPTIONAL_HEADER.ImageBase, r["old_base"], len(orig_rvas),
                 "OK" if good else "FAIL"))
        ok = ok and good
    print("\nverify: %d module(s), %s" % (len(picks), "all OK" if ok else "FAILURES"))
    return 0 if ok else 1


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("pcgame", nargs="?", default=str(ROOT / "original/pcgame"),
                    help="the installed game directory (default original/pcgame)")
    ap.add_argument("--out", default=str(ROOT / "build/content"),
                    help="directory for the rebased images (default build/content)")
    ap.add_argument("--toml", default=None,
                    help="TOML fragment path (default <out>/modules.toml)")
    ap.add_argument("--alloc-base", type=lambda s: int(s, 0), default=GUEST_SHIM_END,
                    help="first base to allocate (default 0x10000000)")
    ap.add_argument("--verify", type=int, default=0,
                    help="re-check N random rebased images instead of writing")
    ap.add_argument("--seed", type=int, default=0, help="random seed for --verify")
    ap.add_argument("--only", action="append", metavar="GLOB", default=None,
                    help="write only modules whose relative path or file name matches GLOB "
                         "(repeatable); bases stay those of the full assignment")
    ap.add_argument("--limit", type=int, default=None, metavar="N",
                    help="write only the first N selected modules (for a trial)")
    args = ap.parse_args()

    pcgame = Path(args.pcgame)
    if not pcgame.is_dir():
        sys.exit("not a directory: %s" % pcgame)
    out_dir = Path(args.out)
    toml_path = Path(args.toml) if args.toml else out_dir / "modules.toml"

    if args.verify:
        sys.exit(verify(pcgame, out_dir, args.verify, args.seed))
    if args.limit is not None and args.limit < 1:
        sys.exit("--limit must be at least 1")
    build(pcgame, out_dir, toml_path, args.alloc_base, args.only, args.limit)


if __name__ == "__main__":
    main()
