"""
Java management: each download bundles its own JRE (Eclipse Temurin / Adoptium)
inside the launcher folder, keyed by major version (8 / 21 / 25). No system
Java is ever required.
"""

import os
import re
import sys
import json
import shutil
import zipfile
import platform
import subprocess
import urllib.request
from pathlib import Path

from . import core
from .core import log

ADOPTIUM_API = "https://api.adoptium.net/v3/binary/latest/{feature}/ga/windows/x64/{type}/hotspot/normal/eclipse"

# Where we bundle downloaded JREs (one subfolder per major version)
JAVA_DIR_NAME = "java_runtime"

# Preferred Java feature versions, in order. Fill 8 / 21 / 25 as requested.
JAVA_FEATURES = [25, 21, 8]

PREFERRED_DEFAULT = 21


def _norm(feature):
    """Map an arbitrary feature number to one of our supported sets."""
    try:
        f = int(feature)
    except (TypeError, ValueError):
        f = 0
    if f <= 8:
        return 8
    if f == 21:
        return 21
    if f >= 25:
        return 25
    if f <= 20:
        return 21   # 9..20 -> Java 21 compatible set
    if f <= 24:
        return 21   # 22..24 -> treat as 21
    return 25


def required_java(version_meta: dict, version_id: str = "") -> int:
    """Pick the Java feature version needed by a Minecraft version.

    Uses Mojang's `javaVersion.majorVersion` when present, otherwise falls back
    to the version id (pre-1.17 -> Java 8, 1.17+ -> Java 21).
    """
    try:
        jv = version_meta.get("javaVersion") or {}
        major = int(jv.get("majorVersion"))
        return _norm(major)
    except (TypeError, ValueError, AttributeError):
        pass
    m = re.match(r"(\d+)\.(\d+)", version_id)
    if m:
        mj, mn = int(m.group(1)), int(m.group(2))
        if mj >= 2 or (mj == 1 and mn >= 17):
            return 21
        return 8
    return PREFERRED_DEFAULT


def find_system_javaw() -> str | None:
    """Look for an existing javaw.exe on PATH or common install dirs."""
    for exe in ("javaw.exe", "java.exe"):
        found = shutil.which(exe)
        if found:
            if exe == "java.exe":
                return None
            return found
    candidates = []
    if os.environ.get("ProgramFiles"):
        candidates.append(Path(os.environ["ProgramFiles"]) / "Eclipse Adoptium")
        candidates.append(Path(os.environ["ProgramFiles"]) / "Java")
        candidates.append(Path(os.environ["ProgramFiles"]) / "Microsoft")
    if os.environ.get("ProgramW6432") and os.environ["ProgramW6432"] != os.environ.get("ProgramFiles"):
        candidates.append(Path(os.environ["ProgramW6432"]) / "Eclipse Adoptium")
        candidates.append(Path(os.environ["ProgramW6432"]) / "Java")
        candidates.append(Path(os.environ["ProgramW6432"]) / "Microsoft")
    for base in candidates:
        if base.is_dir():
            for sub in base.rglob("javaw.exe"):
                return str(sub)
    if os.environ.get("JAVA_HOME"):
        p = Path(os.environ["JAVA_HOME"]) / "bin" / "javaw.exe"
        if p.exists():
            return str(p)
    return None


class JavaManager:
    def __init__(self, root: Path):
        self.root = root
        self.bundled_base = root / JAVA_DIR_NAME

    # ---- per-version bundled locations ----------------------------------- #
    def bundled_dir(self, feature) -> Path:
        return self.bundled_base / str(_norm(feature))

    def bundled_javaw_for(self, feature) -> Path | None:
        """Return javaw.exe for a specific bundled feature version, if present."""
        d = self.bundled_dir(feature)
        if not d.is_dir():
            return None
        for exe in ("bin/javaw.exe", "bin/java.exe"):
            p = d / exe
            if p.exists():
                return p
        hits = list(d.rglob("javaw.exe"))
        return Path(hits[0]) if hits else None

    def bundled_javaw(self, feature=None) -> Path | None:
        """Return any bundled javaw, optionally restricted to a feature set.

        With feature=None returns one installed JRE, preferring 21 then 25 then 8.
        """
        if feature is not None:
            return self.bundled_javaw_for(feature)
        order = list(JAVA_FEATURES)
        # Ensure the default is checked first even if not in list.
        if PREFERRED_DEFAULT not in order:
            order.insert(0, PREFERRED_DEFAULT)
        for f in order:
            p = self.bundled_javaw_for(f)
            if p:
                return p
        return None

    def installed_features(self):
        out = []
        for f in JAVA_FEATURES:
            if self.bundled_javaw_for(f):
                out.append(f)
        return out

    # ---- generic helpers -------------------------------------------------- #
    def javaw_path(self) -> str | None:
        b = self.bundled_javaw()
        if b:
            return str(b)
        return find_system_javaw()

    def is_ready(self) -> bool:
        return self.bundled_javaw() is not None

    def java_ver(self, path=None) -> str:
        """Return a short version string for a given javaw/java binary."""
        exe = path or self.javaw_path()
        if not exe:
            return "not found"
        try:
            out = subprocess.run([exe, "-version"],
                                 capture_output=True, text=True, timeout=20)
            first = (out.stderr or out.stdout).strip().splitlines()
            for line in first:
                line = line.strip()
                if '"' in line and 'version' in line:
                    return line.split('"')[1]
            return "unknown"
        except Exception:
            return "unknown"

    # ---- download --------------------------------------------------------- #
    def download_bundled(self, feature, progress=None) -> bool:
        """Download and extract a Temurin JRE into java_runtime/<feature>/."""
        feature = _norm(feature)
        dest = self.bundled_dir(feature)
        if self.bundled_javaw_for(feature):
            return True
        dest.mkdir(parents=True, exist_ok=True)
        for jtype in ("jre", "jdk"):
            url = ADOPTIUM_API.format(feature=feature, type=jtype)
            tmp_zip = dest / f"temurin-{feature}-{jtype}.zip"
            try:
                if progress:
                    progress(f"Downloading Java {feature}...", 0.1)
                core.http_download(url, tmp_zip)
            except Exception:
                continue
            if not tmp_zip.exists():
                continue
            if progress:
                progress(f"Extracting Java {feature}...", 0.6)
            try:
                with zipfile.ZipFile(tmp_zip) as zf:
                    zf.extractall(dest)
            except Exception as e:
                log(f"Extract failed: {e}")
            tmp_zip.unlink(missing_ok=True)
            if self.bundled_javaw_for(feature):
                return True
        return False

    def ensure_java(self, features, progress=None) -> bool:
        """Ensure every requested feature version is bundled, downloading as needed."""
        all_ok = True
        for f in features:
            if not self.bundled_javaw_for(f):
                ok = self.download_bundled(f, progress=progress)
                if not ok:
                    all_ok = False
            else:
                if progress:
                    progress(f"Java {f} ready", 1.0)
        return all_ok

    def ensure_for_version(self, version_meta: dict, version_id: str,
                           progress=None) -> int | None:
        """Bundle the Java needed for a version; returns the feature number or None."""
        feature = required_java(version_meta, version_id)
        ok = self.ensure_java([feature], progress=progress)
        return feature if ok else None
