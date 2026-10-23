"""Unit tests for launcher.core — pure helpers only (no network, no GUI).

Run with::

    python -m unittest discover -s tests -v
"""

import hashlib
import sys
import tempfile
import threading
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from launcher import core  # noqa: E402


class OfflineUuidTests(unittest.TestCase):
    def test_deterministic(self):
        self.assertEqual(core.offline_uuid("Notch"), core.offline_uuid("Notch"))

    def test_known_value(self):
        # Must stay compatible with every other Minecraft launcher: UUID v3 of
        # "OfflinePlayer:<name>" in the DNS namespace.
        self.assertEqual(core.offline_uuid("Notch"),
                         "dbada1bb-c3b6-3839-a50d-543d675f92fa")

    def test_differs_per_name(self):
        self.assertNotEqual(core.offline_uuid("Alex"), core.offline_uuid("Steve"))


class LibraryPathTests(unittest.TestCase):
    def test_basic_coords(self):
        self.assertEqual(
            core.library_path("net.minecraft:launchwrapper:1.12"),
            ["net/minecraft", "launchwrapper", "1.12", "launchwrapper-1.12.jar"],
        )

    def test_classifier_is_appended(self):
        self.assertEqual(
            core.library_path("org.lwjgl:lwjgl:3.3.1:natives-linux"),
            ["org/lwjgl", "lwjgl", "3.3.1", "lwjgl-3.3.1-natives-linux.jar"],
        )

    def test_natives_flag_forces_windows(self):
        self.assertEqual(
            core.library_path("org.lwjgl:lwjgl:3.3.1", natives=True),
            ["org/lwjgl", "lwjgl", "3.3.1", "lwjgl-3.3.1-natives-windows.jar"],
        )


class LibraryRulesTests(unittest.TestCase):
    def test_no_rules_is_allowed(self):
        self.assertTrue(core.library_allowed({}))

    def test_allow_windows(self):
        lib = {"rules": [{"action": "allow", "os": {"name": "windows"}}]}
        self.assertTrue(core.library_allowed(lib))

    def test_allow_osx_only_is_rejected(self):
        lib = {"rules": [{"action": "allow", "os": {"name": "osx"}}]}
        self.assertFalse(core.library_allowed(lib))

    def test_disallow_windows_wins(self):
        lib = {"rules": [
            {"action": "allow"},
            {"action": "disallow", "os": {"name": "windows"}},
        ]}
        self.assertFalse(core.library_allowed(lib))

    def test_disallow_other_os_keeps_windows(self):
        lib = {"rules": [
            {"action": "allow"},
            {"action": "disallow", "os": {"name": "linux"}},
        ]}
        self.assertTrue(core.library_allowed(lib))


class Sha1Tests(unittest.TestCase):
    def test_sha1_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "blob.bin"
            payload = b"pyvortex" * 1000
            p.write_bytes(payload)
            self.assertEqual(core.sha1_file(p),
                             hashlib.sha1(payload).hexdigest())


class DownloadSkipTests(unittest.TestCase):
    """The pre-filter must not touch the network for files already on disk."""

    def test_matching_file_is_skipped(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "cached.bin"
            payload = b"already here"
            p.write_bytes(payload)
            sha1 = hashlib.sha1(payload).hexdigest()

            calls = []

            def progress(msg, frac):
                calls.append((msg, frac))

            ok, failed = core.download_tasks_parallel(
                [("https://example.invalid/cached.bin", p, sha1)],
                progress=progress,
            )
            self.assertEqual((ok, failed), (1, 0))
            self.assertEqual(p.read_bytes(), payload)
            self.assertTrue(calls)

    def test_min_size_int_expected(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "big.bin"
            p.write_bytes(b"x" * 10)
            ok, failed = core.download_tasks_parallel([(None, p, 5)])
            self.assertEqual((ok, failed), (1, 0))

    def test_cancel_event_raises(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "never.bin"
            cancel = threading.Event()
            cancel.set()
            with self.assertRaises(core.DownloadCancelled):
                core.download_tasks_parallel(
                    [("https://example.invalid/never.bin", p, None)],
                    cancel_event=cancel,
                )


class BuildArgumentsTests(unittest.TestCase):
    """Placeholder substitution for both the legacy and the modern format."""

    LEGACY_META = {
        "mainClass": "net.minecraft.client.main.Main",
        "minecraftArguments":
            "--username ${auth_player_name} --uuid ${auth_uuid} "
            "--accessToken ${auth_access_token} --gameDir ${game_directory}",
        "libraries": [],
    }

    def build(self, meta, tmp):
        return core.build_arguments(
            Path(tmp), meta, "1.12.2", "G:/mc", "legacy", "Player1",
            core.offline_uuid("Player1"), "G:/mc/natives", "G:/mc/assets",
        )

    def test_legacy_placeholders(self):
        with tempfile.TemporaryDirectory() as tmp:
            game_args, jvm_args, main_class = self.build(self.LEGACY_META, tmp)
            self.assertEqual(main_class, "net.minecraft.client.main.Main")
            self.assertIn("--username", game_args)
            self.assertIn("Player1", game_args)
            self.assertNotIn("${auth_player_name}", " ".join(game_args))
            # natives path is always injected
            self.assertTrue(any(a.startswith("-Djava.library.path=")
                                for a in jvm_args))

    def test_modern_arguments_format(self):
        meta = {
            "mainClass": "net.minecraft.client.main.Main",
            "arguments": {
                "game": ["--username", "${auth_player_name}", "--version",
                         "${version_name}"],
                "jvm": ["-cp", "${classpath}"],
            },
            "libraries": [],
        }
        with tempfile.TemporaryDirectory() as tmp:
            game_args, jvm_args, _ = self.build(meta, tmp)
            self.assertEqual(game_args, ["--username", "Player1",
                                         "--version", "1.12.2"])
            self.assertIn("-cp", jvm_args)

    def test_account_overrides_player_name_and_token(self):
        with tempfile.TemporaryDirectory() as tmp:
            account = {"name": "Premium", "uuid": "0" * 32,
                       "access_token": "tok", "user_type": "msa",
                       "xuid": "42"}
            game_args, jvm_args, _ = core.build_arguments(
                Path(tmp), self.LEGACY_META, "1.12.2", "G:/mc", "legacy",
                "Ignored", "offline-uuid", "G:/mc/natives", "G:/mc/assets",
                account=account,
            )
            joined = " ".join(game_args)
            self.assertIn("Premium", joined)
            self.assertIn("tok", joined)
            self.assertNotIn("Ignored", joined)

    def test_disallowed_library_is_not_on_classpath(self):
        meta = {
            "mainClass": "net.minecraft.client.main.Main",
            "arguments": {
                "game": ["--username", "${auth_player_name}"],
                "jvm": ["-cp", "${classpath}"],
            },
            "libraries": [
                {"name": "allowed:lib:1.0",
                 "downloads": {"artifact": {"path": "allowed/lib/1.0/lib-1.0.jar"}}},
                {"name": "blocked:lib:1.0",
                 "rules": [{"action": "allow", "os": {"name": "osx"}}],
                 "downloads": {"artifact":
                               {"path": "blocked/lib/1.0/lib-1.0.jar"}}},
            ],
        }
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for rel in ("allowed/lib/1.0/lib-1.0.jar",
                        "blocked/lib/1.0/lib-1.0.jar"):
                p = root / "libraries" / Path(*rel.split("/"))
                p.parent.mkdir(parents=True, exist_ok=True)
                p.write_bytes(b"jar")

            game_args, jvm_args, _ = core.build_arguments(
                root, meta, "1.12.2", "G:/mc", "legacy", "Player1",
                "offline-uuid", "G:/mc/natives", "G:/mc/assets",
            )
            classpath = " ".join(jvm_args)
            self.assertIn("lib-1.0.jar", classpath)
            self.assertNotIn("blocked", classpath)


if __name__ == "__main__":
    unittest.main(verbosity=2)