# Catz 5 — macOS recompilation port

A static recompilation of the 2002 Win32 game **Catz 5** (Ubi Soft / Studio
Mythos) into a native macOS app, built with
[recomp-kit](https://github.com/syleront/recomp-kit) (vendored here as the `kit/`
submodule). The game's x86 code is translated to C ahead of time and linked into
the app, and the Win32/DirectX calls it makes are served by the kit's native
runtime shims. A small interpreter fallback handles the few paths the
translation does not carry, such as one dialog procedure.

The app opens a 1024×768 window (the game sizes its play area from the reported
desktop), plays the game's audio, and quits cleanly from both the window close
button and ⌘Q.

> This repository contains **no game files**. You supply your own copy of the
> game; the build reads it from a local, git-ignored directory and never commits
> or bundles anything derived from it. The generated translation is also
> git-ignored.

## Status

Working today:

- The intro/splash sequence and the main UI.
- The **Go To** menu with its built-in scenes (Adoption Center, Editor, Custom
  Scenes, Desktop, …) and the downloaded areas, plus the **More Scenes** lists
  with their owner-drawn row art.
- The full adoption flow: pick a breed, name the kitten in the edit control,
  accept the pledge, and bring it home. The pet is saved as
  `Adopted Petz/<name>.pet` and appears in **Pick a Pet** after a relaunch.
- Core cat interactions: a toy or treat dragged out of the carrying case onto
  the room floor is fetched and used; petting is implemented and was observed,
  but is not yet reliably reproducible in a scripted run.
- Audio, including the game's MS ADPCM waves.
- Clean shutdown, and saves redirected to a writable profile directory.

Still open, roughly in order of visibility:

- The dialog engine supports the reached dialogs but remains minimal (strings,
  one selection, a single-line edit; no scrolling multi-line editing, caret
  blink or theme).
- The directory-change watcher does not see a real change, so newly downloaded
  content is not refreshed in a running game.
- A non-fatal presenter message (`drawable acknowledgement exceeded queue
  grace`) appears once per windowed run before the fallback engages.
- The `GetSystemPaletteEntries` palette is empty.
- The cat's walk is random, so scripted tests anchor it with a toy or treat
  before interacting rather than following it.

The authoritative, up-to-date list is the
[issue tracker](https://github.com/syleront/catz5-mac/issues). Please report
reproducible problems there rather than in this file.

## Prerequisites

- macOS 14 or later on Apple Silicon, with the Xcode command-line tools
  (`xcode-select --install`).
- Python 3.9 or later.
- [Ghidra 12.1.3](https://github.com/NationalSecurityAgency/ghidra/releases/tag/Ghidra_12.1.3_build)
  and a Java runtime it supports (OpenJDK 26.0.1 was used for this port). Ghidra
  is needed only for the first build: it produces the function listings the
  translator consumes.
- Your own installation of the game, sold as **Catz 5** and **Petz 5**. The
  supported executable is `Petz 5.exe` from the matching retail build: `game.toml`
  pins its SHA-256 and setup refuses any other image. The build fetches SDL3 and
  a pinned FFmpeg build on first run; no Homebrew libraries are required.

## Build

From a clean checkout:

```sh
git clone --recursive https://github.com/syleront/catz5-mac.git
cd catz5-mac

# 1. Python environment
python3 -m venv .venv
.venv/bin/python -m pip install -r kit/requirements-dev.txt

# 2. Link your game installation. This validates the executable's SHA-256 and
#    creates the ignored original/pcgame symlink. It does not copy the game.
.venv/bin/python tools/setup.py --install /path/to/your/installed/Petz5 --link-only

# 3. Export the executable listing with Ghidra's own analyzers. This is a long
#    step; it writes no log of its own, so redirect its output.
mkdir -p analysis
.venv/bin/python tools/analyze.py \
  --ghidra-home /path/to/ghidra_12.1.3_PUBLIC \
  --java-home /path/to/your/jdk/Contents/Home \
  2>&1 | tee analysis/analyze.log

# 4. First full build: translates the executable and all 376 content modules,
#    exports their Ghidra listings, and links the app. The tool paths are
#    forwarded to the content step; GHIDRA_HOME and JAVA_HOME can replace them.
mkdir -p build
.venv/bin/python tools/build.py --content --regenerate --jobs 10 \
  --ghidra-home /path/to/ghidra_12.1.3_PUBLIC \
  --java-home /path/to/your/jdk/Contents/Home \
  --allow-unmodelled "Ghidra function-start patterns decoded from linker padding bytes" \
  --allow-table-gaps "content-module jump tables whose entries lie outside the listing" \
  > build/build.log 2>&1
```

The first `--content` build takes roughly **30 minutes** (most of it is Ghidra
exporting ~376 small content modules in parallel). Later builds are much faster:

- a **runtime-only** change (shims, host, audio) needs no `--regenerate` and no
  `--content`; `--target app` rebuilds just the native side in a couple of
  minutes:
  ```sh
  .venv/bin/python tools/build.py --target app --jobs 10 > build/build.log 2>&1
  ```
- a **translation** change needs `--regenerate`, but not `--content` unless the
  content listings are stale.

Output: `build/Catz5Recomp.app` (the packaged app). For testing,
`tools/build.py --target headless` also builds `build/recomp/pop_headless` (a
headless host that writes PPM frames and a run report). A failed build leaves
`build/build.log`.

The app's icon is not stored in this repository. The build reads the icon
resources out of `Petz 5.exe` in your own installation, writes them to
`Contents/Resources/AppIcon.icns`, and caches the result in `build/macos-icon/`
(git-ignored). A build without the game linked still succeeds; it simply keeps
the generic app icon.

## Run

Double-click `build/Catz5Recomp.app`, or:

```sh
build/Catz5Recomp.app/Contents/MacOS/Catz5Recomp
```

It opens the game window and starts the game. Close the window (or press ⌘Q) to
quit; the game asks "Do you really want to quit the game?" and then exits
cleanly.

## Releases

Release builds are **ad-hoc signed, not notarized**: they are built without an
Apple Developer ID. Gatekeeper therefore warns on first launch ("unidentified
developer", or "damaged" when the download carries a quarantine attribute).
Right-click the app and choose **Open**, or remove the attribute once:

```sh
xattr -dr com.apple.quarantine "/Applications/Catz5Recomp.app"
```

The archive is named `Catz5Recomp-<version>-macos-arm64.zip` and contains
`Catz5Recomp.app` with its licences and notices; drag it to `/Applications`.
The version and build number come from `[game] version` and `[game] build` in
`game.toml`.

A release still needs **your own copy of Catz 5**. The app contains no game
files. On first launch its launcher asks for the installed folder containing
`Petz 5.exe`, `Resource/`, `Adopted Petz/` and `Skinz/`, and verifies the executable's
SHA-256 against `game.toml`. It can play that installation in place, or copy it
into `~/Library/Application Support/Catz5Recomp/game`. The profile, settings,
saves and adopted pets live under
`~/Library/Application Support/Catz5Recomp/`. Without those game files the app
cannot start.

The icon is generated at build time from the maintainer's own `Petz 5.exe`; no
game artwork is committed or redistributed. A release binary contains translated
code derived from the game executable, but no game data, audio, models or other
assets.

Useful environment switches (see `kit/host/README.md` and
`kit/runtime/README.md` for the full lists):

- `RECOMP_LOG=2` — verbose import tracing.
- `RECOMP_MOUSE_SYNTH="x,y;x,y"` (windowed) — push synthetic mouse motion at
  these window points through the real event path, for testing the windowed
  input mapping where no pointer can reach the window.
- `RECOMP_FRAME_EVERY=N` (headless) — write every Nth frame to
  `RECOMP_FRAMES=<dir>` as a PPM.
- `RECOMP_HOST_AUDIO_CAPTURE=<file.wav>` — write the mixer's own output.
- `RECOMP_MAX_FRAMES=N`, `RECOMP_MAX_SECONDS=S` (headless) — stop after N frames
  or S seconds, then ask the game to quit.

## Where saves go

The game's writes are redirected to a writable profile directory, never into the
game installation:

- **Developer run** (a checkout is present above the app, which is the case for
  `build/Catz5Recomp.app` in this repository): the profile is
  `build/recomp/profile/`, while the emulated registry and the host's state files
  (`registry.json`, `game-path.txt`, `mod-settings.json`) are written directly in
  `build/recomp/`.
- **Packaged app** (no checkout beside it): everything, profile and state files,
  is under `~/Library/Application Support/Catz5Recomp/`.

Adopted pets are written under `<profile>/Adopted Petz/*.pet`. Set
`RECOMP_PROFILE_DIR` to use a different profile directory.

Deleting the profile clears adopted pets and settings, but in a developer run it
does not reset the emulated registry, which lives in `build/recomp/registry.json`.
Catz 5 stores the play-window rectangle it computed in the registry as
`DownloadArea's AreaRect` and reuses it for as long as it is valid, so after a
change to the reported desktop a stale value keeps the old window size. Remove
that key (or the whole registry file) to let the game recompute 1024×768; a fresh
checkout starts with no registry at all.

## Layout

| Path | What it holds |
|---|---|
| `kit/` | [recomp-kit](https://github.com/syleront/recomp-kit), the translator and native runtime (submodule) |
| `game.toml` | Game identity, pinned executable SHA-256, addresses and translator inputs |
| `globals.toml` | Curated symbols copied into the translation |
| `discovered.txt` | Addresses reached at run time that the translation did not carry (empty by design for this port) |
| `native/` | Native replacements for translated functions |
| `tools/` | Game-repository wrappers around the kit's build/test/setup tools, content rebasing and the Ghidra export script |
| `original/`, `analysis/`, `build/` | Local only (git-ignored): your installation link, Ghidra listings, build products |

## Credits

- [recomp-kit](https://github.com/veritr1x/recomp-kit) by veritr1x (MIT), the
  static recompilation kit that makes this port possible.
- The kit's third-party components: SDL3 (zlib), FFmpeg (LGPL-2.1-or-later,
  linked dynamically as replaceable dylibs), Lua, minimp3, stb, Open Sans
  (Apache-2.0), GeneralUser GS, miniz, volk and the Vulkan headers. The full
  notices and license texts are in `kit/NOTICE` and the components' own
  directories.
- Catz 5 was developed by Studio Mythos and published by Ubi Soft in 2002.
  This is an independent interoperability project; all game content remains the
  property of its owners.

## Legal

This project is **not affiliated with or endorsed by Ubisoft, Ubi Soft, Studio
Mythos, or the rights holders of the Petz/Catz series**. It distributes no game
executables, data, artwork, audio, levels or other assets. You must own a legal
copy of Catz 5 to use it; the build links your own installation and the
resulting app contains content derived from it. Catz, Petz and all related names
and marks are trademarks of their respective owners.

This source repository contains no translated game code. Release binaries are
built from it and contain translated code derived from the game's executable,
but no game data files. Building the port yourself produces the same kind of
derivative work from your own copy.

## License

The project's own source (tools, native overrides, configuration and
documentation) is released under the [MIT License](LICENSE). The `kit/`
submodule is MIT-licensed by its authors; bundled third-party components keep
their own licenses, listed in [NOTICE](NOTICE) and `kit/NOTICE`.
