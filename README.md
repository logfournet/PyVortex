# PyVortex — Minecraft Launcher (Python)

A clean-room Python rewrite inspired by the open-source
[Vortex Minecraft Launcher](https://github.com/Kron4ek/minecraft-vortex-launcher)
by Kron4ek. It downloads Minecraft versions, libraries and assets straight
from Mojang and launches the game either with a **local profile** (offline,
single-player — no sign-in) or with **your own Microsoft account** via the
official device-code OAuth flow.

**Nothing to install**: no external Java, no pip packages — only the Python
standard library (uses Tkinter, built into CPython on Windows). Playing on
online servers requires a Microsoft account that owns Minecraft: Java Edition.

## Features

- **Auto-installs Java** — if no Java is found on the system, the launcher
  downloads and bundles a Temurin (Adoptium) JRE into `./java_runtime/`.
- **Downloads versions** from Mojang's version manifest (releases & snapshots).
- **Downloads libraries, assets and natives**, validates SHA-1, extracts
  natives, and skips files that already exist.
- **Supports both version formats**:
  - Old `minecraftArguments` (pre-1.13), e.g. 1.12.2
  - Modern `arguments` game/jvm (1.13+)
  - `inheritsFrom` chains (forks/snapshots)
  - OS `rules` filtering for Windows
- **Sets RAM** (MB), player name, and remembers your settings in `config.json`.
- **Multi-account manager** — add Microsoft accounts or local offline profiles
  and switch between them. The selected profile's in-game avatar is fetched
  from the public mc-heads.net avatar API (by username) and shown in the GUI.
- **Microsoft login** (device-code flow) — sign in with your own Microsoft
  account to get an access token / UUID so the game authenticates on online
  servers. Clear error messages (e.g. "account does not own Minecraft").
- Launches through `javaw.exe` with the correct classpath / natives / args.
- A modern dark, professional Tkinter interface.

## How to run

Double-click `PyVortex.exe` from the
[Releases page](https://github.com/logfournet/PyVortex/releases) — or, if you
clone the source, double-click `run.bat` (or run `python main.py`):

1. Enter a **Player name** (≥ 3 characters, no spaces).
2. Under **Memory (MB)** set how much RAM to give Minecraft (min 350).
3. Click **Refresh** to list installed versions, or **Install Version** to
   pick one from Mojang to download.
4. If the status says Java is **not installed**, click **Install Java** once.
5. Click **Play**.

The first launch of a version downloads everything needed (client jar,
assets, libraries, natives) automatically. Subsequent launches reuse the
already-downloaded files.

## Project layout

```
main.py               — entry point, launches the Tkinter GUI
launcher/
  core.py             — Mojang API, version manifest, assets/libraries,
                        natives extraction, argument building, offline UUID
  java.py             — Java detection + bundled JRE download (Adoptium)
  game.py             — high-level download & launch orchestration
  auth.py             — Microsoft OAuth device-code login
  avatar.py           — fetch skin + decode/crop the in-game face avatar
  gui.py              — Tkinter interface (dark theme, multi-account, avatars)
  assets/pyvortex.ico — window / EXE icon
.github/workflows/build.yml — CI: builds PyVortex.exe and attaches it to a tag's Release
requirements-build.txt      — build-only deps (Nuitka); the app itself is stdlib-only
run.bat                     — runs from source on Windows
config.json           — created at runtime (name / ram / accounts / settings)
launch_command.txt    — created at runtime (last launched JVM command)
```

## Building the EXE

The executable is **never committed** to this repository — it is built by
GitHub Actions and attached to a Release:

1. Push a version tag, e.g. `git tag v1.0.4 && git push origin v1.0.4`.
2. The workflow compiles `main.py` with Nuitka (`--onefile`) on
   `windows-latest` and uploads `PyVortex.exe` to that tag's Release.
3. Download it from the [Releases page](https://github.com/logfournet/PyVortex/releases).
   Runs triggered by a normal branch push (or manually via
   `workflow_dispatch`) only produce a build artifact, no Release.

To build locally instead:

```bat
pip install -r requirements-build.txt
python -m nuitka --assume-yes-for-downloads --standalone --onefile ^
    --enable-plugin=tk-inter --windows-console-mode=disable ^
    --windows-icon-from-ico=launcher/assets/pyvortex.ico ^
    --company-name=PyVortex --product-name=PyVortex ^
    --file-description="PyVortex Minecraft Launcher" ^
    --output-filename=PyVortex.exe --output-dir=dist_nuitka main.py
```

## Notes

- **Microsoft accounts** use the official device-code flow. If the login
  reports "This Microsoft account does not own Minecraft: Java Edition", that
  account only has Bedrock (or is child-managed) — sign in with an account that
  owns the Java Edition.
- **Local profiles** are for offline / single-player use: they use the standard
  offline UUID (`OfflinePlayer:<name>`), so the game runs without signing in.
  Online servers that require authentication will reject them.
- The bundled Java is a Temurin JRE ~50 MB, stored under `java_runtime/`.
  You can delete that folder anytime; it's re-downloaded when needed.

## Disclaimer

This is an unofficial, community project — not affiliated with, endorsed by or
supported by Mojang Studios or Microsoft. It only talks to Mojang's public
version/download endpoints; it does not modify or redistribute Minecraft client
files. Minecraft is a trademark of Mojang Studios. Please own the game and
respect the [Minecraft EULA](https://www.minecraft.net/eula) and the
[Minecraft Usage Guidelines](https://www.minecraft.net/usage-guidelines).

## Requirements

- **EXE build (recommended):** Windows 10/11 only — Python is not needed.
- **From source:** Windows 10/11 with Python 3.9+ (Tkinter included by default).
- Internet connection for downloading versions / Java.
