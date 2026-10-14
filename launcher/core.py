"""
Core logic for PyVortex: Mojang version manifest parsing, asset/library
downloading, native extraction and offline (cracked) game launching.
Uses only the Python standard library.
"""

import os
import json
import hashlib
import shutil
import zipfile
import platform
import threading
import urllib.request
import uuid as uuidlib
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

APP_NAME = "PyVortex"
LAUNCHER_VERSION = "1.0.0"

VERSION_MANIFEST_URL = "https://launchermeta.mojang.com/mc/game/version_manifest.json"
PISTON_MANIFEST_URL = "https://piston-meta.mojang.com/mc/game/version_manifest.json"
ASSET_BASE_URL = "https://resources.download.minecraft.net/"

DEFAULT_RAM_MB = 2500
MIN_RAM_MB = 350

DEFAULT_JVM_ARGS = [
    "-Xss1M",
    "-XX:+UnlockExperimentalVMOptions",
    "-XX:+UseG1GC",
    "-XX:G1NewSizePercent=20",
    "-XX:G1ReservePercent=20",
    "-XX:MaxGCPauseMillis=50",
    "-XX:G1HeapRegionSize=32M",
]


def user_agent():
    return f"{APP_NAME}/{LAUNCHER_VERSION}"


def http_get(url, binary=False, timeout=60):
    req = urllib.request.Request(url, headers={"User-Agent": user_agent()})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        data = resp.read()
    return data if binary else data.decode("utf-8")


def http_get_json(url):
    return json.loads(http_get(url))


def http_download(url, dest: Path):
    req = urllib.request.Request(url, headers={"User-Agent": user_agent()})
    dest.parent.mkdir(parents=True, exist_ok=True)
    with urllib.request.urlopen(req, timeout=120) as resp, open(dest, "wb") as f:
        shutil.copyfileobj(resp, f)
    return True


# --- multi-threaded download pool ----------------------------------------- #
# Number of simultaneous file downloads (like Vortex "Download threads").
DOWNLOAD_WORKERS = 8
_workers_lock = threading.Lock()


def set_download_workers(n):
    """Set the number of concurrent download threads (applies to next download)."""
    global DOWNLOAD_WORKERS
    with _workers_lock:
        DOWNLOAD_WORKERS = max(1, int(n))


def download_tasks_parallel(tasks, progress=None, label="Files"):
    """Download `tasks` (list of (url, dest, expected_sha1)) in parallel.

    Returns (ok, failed): ok = number succeeded, failed = number that raised.
    Files that already exist (and match sha1) are skipped quickly.
    """
    # Pre-filter already-present files so the pool only does real work.
    pending = []
    skipped = 0
    for url, dest, expected_sha1 in tasks:
        if dest.exists():
            # expected_sha1 may be a sha1 hex string, an int (min size), or None
            if isinstance(expected_sha1, str):
                matches = sha1_file(dest) == expected_sha1
            elif isinstance(expected_sha1, int):
                try:
                    matches = dest.stat().st_size >= expected_sha1
                except Exception:
                    matches = False
            else:
                matches = True
            if matches:
                skipped += 1
                continue
            dest.unlink(missing_ok=True)
        pending.append((url, dest, expected_sha1))

    total = len(pending)
    if total == 0:
        if progress:
            progress(f"{label} done", 1.0)
        return skipped, 0

    done = 0
    failed = 0
    lock = threading.Lock()

    def work(item):
        nonlocal done, failed
        url, dest, expected_sha1 = item
        ok = True
        if url:
            try:
                http_download(url, dest)
                if isinstance(expected_sha1, str) and sha1_file(dest) != expected_sha1:
                    dest.unlink(missing_ok=True)
                    ok = False
            except Exception:
                ok = False
        with lock:
            done += 1
            cur_done = done
            if not ok:
                failed += 1
        if progress:
            progress(f"{label} {cur_done}/{total}", cur_done / total)

    workers = DOWNLOAD_WORKERS
    with ThreadPoolExecutor(max_workers=workers) as ex:
        list(ex.map(work, pending))

    if progress:
        progress(f"{label} done", 1.0)
    return skipped + total - failed, failed


def sha1_file(path: Path):
    h = hashlib.sha1()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def detect_arch():
    m = platform.machine().lower()
    if m in ("amd64", "x86_64", "aarch64", "arm64"):
        return "64"
    return "32"


def offline_uuid(name: str) -> str:
    """Deterministic offline UUID (UUID v3, MD5) as used by Minecraft."""
    return str(uuidlib.uuid3(uuidlib.NAMESPACE_DNS, "OfflinePlayer:" + name))


# --------------------------------------------------------------------------- #
#  Version manifest (list of versions)
# --------------------------------------------------------------------------- #
class VersionManifest:
    def __init__(self, json_data):
        self.json = json_data
        self.versions = json_data.get("versions", [])

    @classmethod
    def fetch(cls):
        for url in (PISTON_MANIFEST_URL, VERSION_MANIFEST_URL):
            try:
                return cls(http_get_json(url))
            except Exception:
                continue
        raise RuntimeError("Failed to fetch version manifest")

    def list_versions(self, all_versions=False):
        result = []
        for v in self.versions:
            if not all_versions and v.get("type") != "release":
                continue
            result.append(v.get("id"))
        return result

    def get_version_meta_url(self, version_id):
        for v in self.versions:
            if v.get("id") == version_id:
                return v.get("url")
        return None


# --------------------------------------------------------------------------- #
#  Version metadata (single version json)
# --------------------------------------------------------------------------- #
class MinecraftVersion:
    """Parses a single version JSON, supporting inheritance."""
    def __init__(self, root: Path, version_id: str, meta: dict):
        self.root = root
        self.version_id = version_id
        self.meta = meta
        self.inherits_from = meta.get("inheritsFrom")

    @classmethod
    def load(cls, root: Path, version_id: str, url=None):
        ver_dir = root / "versions" / version_id
        ver_dir.mkdir(parents=True, exist_ok=True)
        json_path = ver_dir / f"{version_id}.json"
        if not json_path.exists() and url:
            http_download(url, json_path)
        if not json_path.exists():
            raise FileNotFoundError(f"Version JSON missing: {json_path}")
        return cls(root, version_id, json.loads(json_path.read_text("utf-8")))

    def effective(self):
        """Resolve the full inheritance chain into a dict of all fields."""
        merged = dict(self.meta)
        seen = set()
        node = self
        chain = []
        while node is not None and node.version_id not in seen:
            chain.append(node)
            seen.add(node.version_id)
            if node.inherits_from:
                node = MinecraftVersion.load(self.root, node.inherits_from)
            else:
                node = None

        # Apply from base (last) to top (self), so topmost wins.
        for n in reversed(chain):
            for key, val in n.meta.items():
                if key not in ("libraries", "arguments", "mainClass", "inheritsFrom"):
                    merged[key] = val
        # Merge libraries & arguments explicitly
        merged["libraries"] = []
        arg_present = any("arguments" in n.meta for n in chain)
        if arg_present:
            merged["arguments"] = {"game": [], "jvm": []}
        for n in reversed(chain):
            merged["libraries"] += n.meta.get("libraries", [])
            if "arguments" in n.meta and arg_present:
                for side in ("game", "jvm"):
                    if side in n.meta["arguments"]:
                        merged["arguments"][side] += n.meta["arguments"][side]
            if "minecraftArguments" in n.meta and "minecraftArguments" not in merged:
                merged["minecraftArguments"] = n.meta["minecraftArguments"]
            if "mainClass" in n.meta:
                merged["mainClass"] = n.meta["mainClass"]
        return merged

    def client_jar_path(self):
        ef = self.effective()
        inherits = ef.get("inheritsFrom")
        dirname = inherits or self.version_id
        return self.root / "versions" / dirname / f"{dirname}.jar"


# --------------------------------------------------------------------------- #
#  Library rules
# --------------------------------------------------------------------------- #
def os_name():
    return "windows"


def library_allowed(lib):
    """Apply OS rules to decide whether a library is included on Windows."""
    rules = lib.get("rules")
    if not rules:
        return True
    allowed = False
    for rule in rules:
        action = rule.get("action")
        os_rule = rule.get("os", {}).get("name")
        if action == "allow":
            if os_rule is None or os_rule == os_name():
                allowed = True
        elif action == "disallow":
            if os_rule == os_name():
                allowed = False
    return allowed


def library_path(lib_coords: str, natives=False) -> list:
    """Coords like group:artifact:version[:classifier] -> relative path parts."""
    parts = lib_coords.split(":")
    group = parts[0].replace(".", "/")
    artifact = parts[1]
    version = parts[2]
    classifier = parts[3] if len(parts) > 3 else None
    base = f"{artifact}-{version}"
    if natives:
        base += "-natives-windows"
    elif classifier:
        base += f"-{classifier}"
    return [group, artifact, version, f"{base}.jar"]


# --------------------------------------------------------------------------- #
#  Download orchestration
# --------------------------------------------------------------------------- #
def download_missing_libraries(root: Path, version_meta: dict, progress=None):
    """Download all libraries (incl. natives) needed on Windows."""
    libs = version_meta.get("libraries", [])
    tasks = []
    for lib in libs:
        if not library_allowed(lib):
            continue
        downloads = lib.get("downloads", {})
        coords = lib.get("name", "")
        # Artifact jar
        artifact = downloads.get("artifact")
        if artifact:
            # Prefer the official path from the manifest when available.
            apath = artifact.get("path")
            if apath:
                local = root / "libraries" / Path(*apath.split("/"))
            else:
                rel = library_path(coords)
                local = root / "libraries" / Path(*rel)
            tasks.append((artifact.get("url"), local, artifact.get("sha1")))
        else:
            # Legacy library with only a `url` (no downloads block)
            rel = library_path(coords)
            local = root / "libraries" / Path(*rel)
            if "url" in lib:
                rel_url = "/".join(rel)
                tasks.append((lib["url"].rstrip("/") + "/" + rel_url, local, None))
            else:
                tasks.append(("https://libraries.minecraft.net/" + "/".join(rel), local, None))
        # Natives classifier
        classifiers = downloads.get("classifiers", {})
        natives = classifiers.get("natives-windows") or classifiers.get("natives-windows-64")
        if natives:
            npath = natives.get("path")
            if npath:
                local = root / "libraries" / Path(*npath.split("/"))
            else:
                rel = library_path(coords, natives=True)
                local = root / "libraries" / Path(*rel)
            tasks.append((natives.get("url"), local, natives.get("sha1")))

    total = len(tasks)
    # Send all tasks through the multi-threaded pool.
    ok, failed = download_tasks_parallel(tasks, progress=progress, label="Libraries")
    if progress:
        progress(f"Libraries done ({ok - failed} ok)", 1.0)


def download_assets(root: Path, asset_index: dict, progress=None):
    assets = asset_index.get("objects", {})
    tasks = []
    for name, info in assets.items():
        h = info.get("hash")
        if not h:
            continue
        size = info.get("size")
        if not isinstance(size, int):
            size = 0
        local = root / "assets" / "objects" / h[:2] / h
        url = ASSET_BASE_URL + h[:2] + "/" + h
        tasks.append((url, local, size))  # int -> size check for skip
    download_tasks_parallel(tasks, progress=progress, label="Assets")
    if progress:
        progress("Assets done", 1.0)


def extract_natives(root: Path, version_meta: dict, version_id: str):
    """Extract natives-windows classifiers into versions/<id>/natives."""
    natives_dir = root / "versions" / version_id / "natives"
    natives_dir.mkdir(parents=True, exist_ok=True)
    for lib in version_meta.get("libraries", []):
        if not library_allowed(lib):
            continue
        classifiers = lib.get("downloads", {}).get("classifiers", {})
        natives = classifiers.get("natives-windows") or classifiers.get("natives-windows-64")
        if not natives:
            continue
        coords = lib.get("name", "")
        npath = natives.get("path")
        if npath:
            jar = root / "libraries" / Path(*npath.split("/"))
        else:
            rel = library_path(coords, natives=True)
            jar = root / "libraries" / Path(*rel)
        if not jar.exists():
            continue
        try:
            with zipfile.ZipFile(jar) as zf:
                for member in zf.infolist():
                    if member.is_dir():
                        continue
                    # Skip metadata files that can cause issues
                    target = natives_dir / member.filename
                    target.parent.mkdir(parents=True, exist_ok=True)
                    if not target.exists():
                        with zf.open(member) as src, open(target, "wb") as dst:
                            shutil.copyfileobj(src, dst)
        except Exception as e:
            log(f"Native extract failed for {jar.name}: {e}")


def build_classpath(root: Path, version_meta: dict, version_id: str) -> list:
    cp = []
    for lib in version_meta.get("libraries", []):
        if not library_allowed(lib):
            continue
        # Only include non-native libraries on the classpath
        classifiers = lib.get("downloads", {}).get("classifiers", {})
        if lib.get("natives") or classifiers.get("natives-windows"):
            continue
        coords = lib.get("name", "")
        apath = (lib.get("downloads") or {}).get("artifact", {}).get("path")
        if apath:
            local = root / "libraries" / Path(*apath.split("/"))
        else:
            rel = library_path(coords)
            local = root / "libraries" / Path(*rel)
        if local.exists():
            cp.append(str(local))
    client_jar = root / "versions" / version_id / f"{version_id}.jar"
    if client_jar.exists():
        cp.append(str(client_jar))
    return cp


def build_arguments(root: Path, version_meta: dict, version_id: str,
                    game_dir: str, asset_index: str, player_name: str,
                    uuid: str, natives_dir: str, assets_root: str,
                    access_token: str | None = None, user_type: str = "legacy",
                    account: dict | None = None):
    """Return (game_args, jvm_args, main_class) with placeholders substituted.

    `account` (optional) may carry premium auth values:
      {"access_token", "uuid", "name"} etc.
    `access_token`/`user_type` override the legacy placeholders used for
    offline (cracked) play.
    """
    game_args = []
    jvm_args = []
    args = version_meta.get("arguments") if "arguments" in version_meta else None
    if args:
        game_args = [a for a in args.get("game", []) if isinstance(a, str)]
        jvm_args = [a for a in args.get("jvm", []) if isinstance(a, str)]
    else:
        # Old format: single minecraftArguments string (pre-1.13)
        game_args = version_meta.get("minecraftArguments", "").split(" ")

    main_class = version_meta.get("mainClass", "net.minecraft.client.main.Main")

    # Classpath & natives (use the effective client jar / lib dir)
    libs_root = root / "libraries"
    cp = []
    for lib in version_meta.get("libraries", []):
        if not library_allowed(lib):
            continue
        classifiers = (lib.get("downloads") or {}).get("classifiers", {})
        if lib.get("natives") or classifiers.get("natives-windows"):
            continue
        coords = lib.get("name", "")
        apath = (lib.get("downloads") or {}).get("artifact", {}).get("path")
        if apath:
            local = libs_root / Path(*apath.split("/"))
        else:
            rel = library_path(coords)
            local = libs_root / Path(*rel)
        if local.exists():
            cp.append(str(local))
    client_dir = root / "versions" / version_id
    client_jar = client_dir / f"{version_id}.jar"
    if client_jar.exists():
        cp.append(str(client_jar))
    classpath = os.pathsep.join(cp)

    # Resolve auth values: prefer the passed account, else access_token/user_type
    # params, else fall back to offline placeholders.
    token = access_token
    utype = user_type
    client_id = "00000000000000000000000000000000"
    xuid = "0"
    auth_session = "0" * 32
    auth_uuid = uuid
    if account:
        token = account.get("access_token") or token
        utype = account.get("user_type", utype)
        client_id = account.get("client_id") or client_id
        xuid = account.get("xuid") or xuid
        auth_session = account.get("access_token") or auth_session
        auth_uuid = account.get("uuid") or auth_uuid
    if token is None:
        token = "0" * 32
    if account:
        player_name = account.get("name") or player_name

    replacements = {
        "${auth_player_name}": player_name,
        "${version_name}": version_id,
        "${game_directory}": game_dir,
        "${assets_root}": assets_root,
        "${assets_index_name}": asset_index,
        "${auth_uuid}": auth_uuid,
        "${auth_access_token}": token,
        "${clientid}": client_id,
        "${auth_xuid}": xuid,
        "${user_type}": utype,
        "${version_type}": "release",
        "${natives_directory}": natives_dir,
        "${launcher_name}": APP_NAME,
        "${launcher_version}": LAUNCHER_VERSION,
        "${classpath}": classpath,
        "${classpath_separator}": os.pathsep,
        "${library_directory}": str(libs_root),
        "${user_properties}": "{}",
        "${auth_session}": auth_session,
        "${game_assets}": "resources",
    }

    def subst(items):
        out = []
        for it in items:
            for k, v in replacements.items():
                it = it.replace(k, v)
            out.append(it)
        return out

    jvm_args = subst(jvm_args)
    game_args = subst(game_args)
    if not any(a.startswith("-Djava.library.path") for a in jvm_args):
        jvm_args = [("-Djava.library.path=" + natives_dir).replace("\\", "/")] + jvm_args
    game_args = [g for g in game_args if g.strip()]
    return game_args, jvm_args, main_class


def log(msg):
    print(f"[PyVortex] {msg}")
