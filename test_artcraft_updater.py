#!/usr/bin/env python3
"""Unit tests for ArtCraft Differential Auto-Updater."""

import unittest
from pathlib import Path
import artcraft_updater as updater
import artcraft_ssd_installer as installer


class TestArtCraftUpdater(unittest.TestCase):
    def test_version_comparisons(self):
        # Equal versions
        self.assertEqual(installer.compare_versions("0.9.0", "0.9.0"), 0)
        # Newer remote
        self.assertLess(installer.compare_versions("0.9.0", "0.9.1"), 0)
        self.assertLess(installer.compare_versions("0.9.0", "0.10.0"), 0)
        self.assertLess(installer.compare_versions("0.9.0", "1.0.0"), 0)
        # Older remote (downgrade prevention)
        self.assertGreater(installer.compare_versions("0.9.1", "0.9.0"), 0)

    def test_clean_version(self):
        self.assertEqual(updater.clean_version("v1.2.3"), "1.2.3")
        self.assertEqual(updater.clean_version("V0.9.0"), "0.9.0")
        self.assertEqual(updater.clean_version("0.7.0"), "0.7.0")

    def test_local_version_detection(self):
        # 12 apps should all be detected on external SSD
        for app_def in updater.APP_DEFINITIONS:
            ver = updater.get_local_version(app_def["name"])
            self.assertIsNotNone(ver, f"{app_def['name']} のローカルバージョンが検出できません")

    def test_notification(self):
        # Test macOS notification delivery
        ok = updater.notify_macos(
            "ArtCraft Test",
            "ユニットテスト",
            "通知システムのテスト完了",
        )
    def test_schedule_management(self):
        # 1. Test test-trigger (force due)
        state = updater.test_trigger_schedule()
        self.assertTrue(updater.is_schedule_due())

        # 2. Test completion record (+14 days)
        new_state = updater.record_check_completed()
        self.assertFalse(updater.is_schedule_due())
        self.assertEqual(new_state["interval_days"], 14)

        # 3. Test snooze
        snooze_state = updater.snooze_schedule(1)
        self.assertTrue(snooze_state["snoozed"])
        self.assertFalse(updater.is_schedule_due())


if __name__ == "__main__":
    unittest.main()
