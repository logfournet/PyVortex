# PyVortex — Minecraft Launcher (Python)

A clean-room Python rewrite inspired by the open-source
[Vortex Minecraft Launcher](https://github.com/Kron4ek/minecraft-vortex-launcher)
by Kron4ek. It downloads Minecraft versions, libraries and assets straight
from Mojang and launches the game both in **offline (cracked) mode** and as a
**premium (Microsoft) account** via the official device-code OAuth flow.

**No needs**: no external Java, no pip packages — only the Python standard
library (uses Tkinter, built into CPython on Windows). For premium accounts a
free Microsoft account that owns Minecraft: Java Edition is required.

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
- **Multi-account manager** — add multiple Microsoft (premium) or offline
  (cracked) accounts and switch between them. The selected account's in-game
  avatar is fetched from the public mc-heads.net avatar API (by username) and
  shown in the GUI.
- **Microsoft login** (device-code flow) — sign in with a real Microsoft
  account to get a premium access token / UUID so the game authenticates on
  real servers. Clear error messages (e.g. "account does not own Minecraft").
- Launches through `javaw.exe` with the correct classpath / natives / args.
- A modern dark, professional Tkinter interface.

## How to run

Double-click `PyVortex.bat` (or run `python main.py`):

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
  auth.py             — Microsoft OAuth device-code login (premium accounts)
  avatar.py           — fetch skin + decode/crop the in-game face avatar
  gui.py              — Tkinter interface (dark theme, multi-account, avatars)
config.json           — created at runtime (name / ram / accounts / settings)
launch_command.txt    — created at runtime (last launched JVM command)
```

## Notes

- **Premium (Microsoft)** accounts use the official device-code flow. If the
  login reports "This Microsoft account does not own Minecraft: Java Edition",
  that account only has Bedrock (or is child-managed) — sign in with an account
  that owns the Java Edition.
- **Offline mode** uses the standard offline UUID (`OfflinePlayer:<name>`),
  so the game runs without an account. Real servers that require
  authentication will reject this — this is for single-player / offline use.
- The bundled Java is a Temurin JRE ~50 MB, stored under `java_runtime/`.
  You can delete that folder anytime; it's re-downloaded when needed.

## Requirements

- Windows 10/11 with Python 3.9+ installed (Tkinter included by default).
- Internet connection for downloading versions / Java.
