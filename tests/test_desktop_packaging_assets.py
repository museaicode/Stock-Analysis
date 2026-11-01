# -*- coding: utf-8 -*-
"""Static contracts for third-party data bundled in desktop backends."""

from __future__ import annotations

import unittest
from pathlib import Path


class DesktopPackagingAssetsTestCase(unittest.TestCase):
    """Keep Windows and macOS PyInstaller package-data rules aligned."""

    repo_root = Path(__file__).resolve().parent.parent

    def test_orjson_is_declared_bundled_and_probed(self) -> None:
        requirements = (self.repo_root / "requirements.txt").read_text(encoding="utf-8")
        main = (self.repo_root / "main.py").read_text(encoding="utf-8")
        macos_script = (self.repo_root / "scripts" / "build-backend-macos.sh").read_text(
            encoding="utf-8"
        )
        windows_script = (self.repo_root / "scripts" / "build-backend.ps1").read_text(
            encoding="utf-8"
        )

        self.assertIn("orjson>=3.10,<4", requirements)
        self.assertIn('"orjson"', macos_script)
        self.assertIn("'orjson'", windows_script)
        self.assertIn('DSA_PACKAGED_IMPORT_PROBE="${module}"', macos_script)
        self.assertIn("$env:DSA_PACKAGED_IMPORT_PROBE = $module", windows_script)
        self.assertIn('importlib.import_module(_packaged_import_probe)', main)

    def test_scripts_collect_and_verify_akshare_calendar_data(self) -> None:
        macos_script = (self.repo_root / "scripts" / "build-backend-macos.sh").read_text(
            encoding="utf-8"
        )
        windows_script = (self.repo_root / "scripts" / "build-backend.ps1").read_text(
            encoding="utf-8"
        )

        self.assertIn("--collect-data akshare", macos_script)
        self.assertIn("'--collect-data', 'akshare'", windows_script)
        # AkShare >= 1.20.0 moved calendar.json to akshare/data/; keep accepting the legacy file_fold/ layout.
        for layout in ("data", "file_fold"):
            self.assertIn(f"_internal/akshare/{layout}/calendar.json", macos_script)
            self.assertIn(f"_internal\\akshare\\{layout}\\calendar.json", windows_script)


if __name__ == "__main__":
    unittest.main()
