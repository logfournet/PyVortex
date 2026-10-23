"""
High-level game launching: wires together version parsing, asset/library/native
downloading and spawning the Java process.
"""

import json
import subprocess
from pathlib import Path

from . import core
from .java import JavaManager


class GameLauncher:
    def __init__(self, root: Path):
        self.root = root
        self.java = JavaManager(root)

    # ---- version management -------------------------------------------------
    def fetch_manifest(self):
        return core.VersionManifest.fetch()

    def downloaded_versions(self):
        ver_dir = self.root / "versions"
        if not ver_dir.is_dir():
            return []
        out = []
        for d in ver_dir.iterdir():
            if d.is_dir() and (d / f"{d.name}.json").exists():
                out.append(d.name)
        return sorted(out, reverse=True)

    # ---- download one full version ------------------------------------------
    def download_version(self, version_id, progress=None, install=True,
                         cancel_event=None):
        manifest = self.fetch_manifest()
        meta_url = manifest.get_version_meta_url(version_id)
        if not meta_url:
            raise RuntimeError(f"Version '{version_id}' not found in manifest")

        if progress:
            progress(f"Fetching metadata for {version_id}", 0.0)
        ver = core.MinecraftVersion.load(self.root, version_id, meta_url)
        effective = ver.effective()

        if cancel_event and cancel_event.is_set():
            raise core.DownloadCancelled("Download cancelled")

        # 1) client jar
        client_dir = self.root / "versions" / version_id
        client_jar = client_dir / f"{version_id}.jar"
        downloads = effective.get("downloads", {})
        client = downloads.get("client")
        if client:
            if not client_jar.exists() or (
                client.get("sha1") and core.sha1_file(client_jar) != client["sha1"]
            ):
                if progress:
                    progress(f"Downloading {version_id} client", 0.1)
                core.http_download(client["url"], client_jar)

        if cancel_event and cancel_event.is_set():
            raise core.DownloadCancelled("Download cancelled")

        # 2) asset index
        asset_index = effective.get("assets", "legacy")
        asset_index_json = effective.get("assetIndex", {})
        indexes_dir = self.root / "assets" / "indexes"
        indexes_dir.mkdir(parents=True, exist_ok=True)
        asset_index_path = indexes_dir / f"{asset_index}.json"
        if asset_index_json:
            core.http_download(asset_index_json["url"], asset_index_path)
        asset_obj = json_load(asset_index_path)
        if progress:
            progress("Downloading assets...", 0.3)
        core.download_assets(self.root, asset_obj, progress,
                             cancel_event=cancel_event)

        # 3) libraries
        if progress:
            progress("Downloading libraries...", 0.6)
        core.download_missing_libraries(self.root, effective, progress,
                                        cancel_event=cancel_event)

        # 4) natives
        core.extract_natives(self.root, effective, version_id)

        if progress:
            progress("Done", 1.0)
        return True

    # ---- launch --------------------------------------------------------------
    def ensure_version_java(self, version_id, progress=None):
        """Make sure a bundled JRE matching the version is available, downloading
        it if needed. Returns the javaw.exe path for that version."""
        ver = core.MinecraftVersion.load(self.root, version_id)
        effective = ver.effective()
        feature = self.java.ensure_for_version(effective, version_id, progress=progress)
        if feature is None:
            raise RuntimeError("Failed to download a Java runtime.")
        p = self.java.bundled_javaw_for(feature)
        return str(p) if p else None

    def launch(self, version_id, player_name, ram_mb, java_path=None,
               progress=None, account=None):
        if not java_path:
            java_path = self.ensure_version_java(version_id, progress=progress)
        if not java_path:
            raise RuntimeError("No Java runtime found. Download one first.")

        ver = core.MinecraftVersion.load(self.root, version_id)
        effective = ver.effective()

        # Ensure needed files exist
        self.ensure_game_files(version_id, effective, progress)

        asset_index = effective.get("assets", "legacy")
        assets_root = str(self.root / "assets").replace("\\", "/")
        natives_dir = (self.root / "versions" / version_id / "natives").as_posix()
        game_dir = str(self.root).replace("\\", "/")

        # Resolve authentication: premium (Microsoft) vs offline (cracked).
        is_premium = bool(account) and account.get("type") == "microsoft"
        if is_premium:
            uuid = account.get("uuid") or core.offline_uuid(player_name)
            user_type = "msa"
            acc = account
        else:
            uuid = core.offline_uuid(player_name)
            user_type = "legacy"
            acc = None

        game_args, jvm_args, main_class = core.build_arguments(
            self.root, effective, version_id, game_dir, asset_index,
            player_name, uuid, natives_dir, assets_root,
            user_type=user_type, account=acc,
        )

        # Build full command
        cmd = [java_path]
        cmd += [f"-Xmx{int(ram_mb)}M"] if ram_mb else []
        # Legacy-compatible GC args
        cmd += core.DEFAULT_JVM_ARGS
        cmd += ["-Dlog4j2.formatMsgNoLookups=true"]
        # Logging config (log4j)
        logging = effective.get("logging", {})
        client_log = logging.get("client", {})
        log_file = client_log.get("file")
        if log_file:
            log4j_id = log_file.get("id")
            config_path = self.root / "assets" / "log_configs" / (log4j_id or "")
            if config_path.exists():
                cmd.append("-Dlog4j.configurationFile=" + config_path.as_posix())
        cmd += jvm_args
        cmd.append(main_class)
        cmd += game_args

        # Log the command for debugging
        with open(self.root / "launch_command.txt", "w", encoding="utf-8") as f:
            f.write(" ".join(cmd))

        # Launch with stderr captured so crashes leave a trace in the log.
        log_path = self.root / "logs" / "launcher_stderr.log"
        log_path.parent.mkdir(parents=True, exist_ok=True)
        stderr_file = open(log_path, "w", encoding="utf-8")
        subprocess.Popen(cmd, cwd=str(self.root),
                         stderr=stderr_file, stdout=subprocess.DEVNULL)
        return True

    def ensure_game_files(self, version_id, effective, progress=None):
        """Make sure client jar, libraries, assets & natives exist; download if missing."""
        client_dir = self.root / "versions" / version_id
        client_jar = client_dir / f"{version_id}.jar"
        downloads = effective.get("downloads", {})
        client = downloads.get("client")
        # For modded versions (Fabric etc.) that inherit from vanilla, the
        # client jar lives in the parent version's directory.
        inherits = effective.get("inheritsFrom")
        if not client_jar.exists() and inherits:
            parent_jar = self.root / "versions" / inherits / f"{inherits}.jar"
            if parent_jar.exists():
                client_jar = parent_jar  # already there, no need to download
        if not client_jar.exists() and client:
            if progress:
                progress("Downloading client...", 0.0)
            core.http_download(client["url"], client_jar)

        asset_index = effective.get("assets", "legacy")
        asset_index_path = self.root / "assets" / "indexes" / f"{asset_index}.json"
        if not asset_index_path.exists():
            asset_index_meta = effective.get("assetIndex", {})
            if asset_index_meta:
                try:
                    core.http_download(asset_index_meta["url"], asset_index_path)
                except Exception:
                    pass
        if asset_index_path.exists():
            asset_obj = json_load(asset_index_path)
        else:
            asset_obj = {"objects": {}}
        core.download_assets(self.root, asset_obj, progress)
        core.download_missing_libraries(self.root, effective, progress)
        core.extract_natives(self.root, effective, version_id)


def json_load(path: Path):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {"objects": {}}
