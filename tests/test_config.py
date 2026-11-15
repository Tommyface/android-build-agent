#!/usr/bin/env python3
"""配置与安全边界单测（不依赖 pytest，直接 python tests/test_config.py）。"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from android_build_agent.builder import agp_hint, github_zip_url  # noqa: E402
from android_build_agent.config import (  # noqa: E402
    ConfigError,
    check_task,
    default_root,
    load_profiles,
    resolve,
    validate_project_name,
)


class TaskWhitelistTest(unittest.TestCase):
    def test_allows_known_tasks(self):
        for task in ("assembleDebug", "assembleRelease", "bundleRelease", "lint", "clean"):
            self.assertEqual(check_task(task), task)

    def test_allows_module_prefix(self):
        self.assertEqual(check_task(":app:assembleDebug"), ":app:assembleDebug")

    def test_blocks_shell_injection(self):
        for bad in ("assembleDebug && whoami", "assembleDebug; ls", "assembleDebug | cat", "assembleDebug $HOME"):
            with self.assertRaises(ConfigError):
                check_task(bad)

    def test_blocks_unknown_task(self):
        with self.assertRaises(ConfigError):
            check_task("installDebug")


class ProfileResolveTest(unittest.TestCase):
    def setUp(self):
        self.data = {
            "defaults": {"sdkPlatforms": ["android-33"], "buildTools": ["33.0.2"], "gradleVersion": "7.6.3", "task": "assembleDebug"},
            "projects": {
                "demo": {"repo": "https://github.com/me/demo.git", "branch": "main"},
                "agp8": {"repo": "https://github.com/me/agp8.git", "sdkPlatforms": ["android-34"], "buildTools": ["34.0.0"], "gradleVersion": "8.7", "task": ":app:assembleDebug"},
            },
            "_exists": True,
            "_path": "/tmp/profiles.json",
        }

    def test_project_uses_defaults(self):
        p = resolve("demo", self.data, root="/tmp/root")
        self.assertEqual(p.sdk_platforms, ["android-33"])
        self.assertEqual(p.gradle_version, "7.6.3")
        self.assertEqual(p.project_dir, Path("/tmp/root/projects/demo"))

    def test_project_overrides_defaults(self):
        p = resolve("agp8", self.data, root="/tmp/root")
        self.assertEqual(p.sdk_platforms, ["android-34"])
        self.assertEqual(p.task, ":app:assembleDebug")

    def test_cli_overrides_win(self):
        p = resolve("demo", self.data, root="/tmp/root", gradle_version="8.7", sdk_platforms=["android-35"])
        self.assertEqual(p.gradle_version, "8.7")
        self.assertEqual(p.sdk_platforms, ["android-35"])

    def test_unknown_profile_raises(self):
        with self.assertRaises(ConfigError):
            resolve("nope", self.data)

    def test_derived_paths_share_root(self):
        p = resolve("demo", self.data, root="/tmp/root")
        self.assertTrue(str(p.gradle_home).startswith("/tmp/root"))
        self.assertTrue(str(p.sdk_dir).startswith("/tmp/root"))
        self.assertTrue(str(p.out_dir).startswith("/tmp/root"))

    def test_project_name_validation(self):
        self.assertEqual(validate_project_name("shiye-2.x"), "shiye-2.x")
        with self.assertRaises(ConfigError):
            validate_project_name("../etc")


class HelperTest(unittest.TestCase):
    def test_github_zip_url(self):
        self.assertEqual(
            github_zip_url("https://github.com/me/demo.git", "main"),
            "https://codeload.github.com/me/demo/zip/refs/heads/main",
        )
        self.assertIsNone(github_zip_url("https://gitlab.com/me/demo.git", "main"))

    def test_agp_hint(self):
        self.assertIsNone(agp_hint("7.4.2", "7.6.3"))
        self.assertIn("建议 Gradle", agp_hint("8.2.0", "7.6.3") or "")
        self.assertIsNone(agp_hint(None, "7.6.3"))

    def test_default_root_is_absolute(self):
        self.assertTrue(Path(default_root()).is_absolute())


class ProfilesFileTest(unittest.TestCase):
    def test_bundled_profiles_parse(self):
        data = load_profiles()
        self.assertIn("projects", data)
        self.assertIsInstance(data["projects"], dict)


if __name__ == "__main__":
    unittest.main(verbosity=2)
