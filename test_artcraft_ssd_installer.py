#!/usr/bin/env python3
"""Unit tests for decisions that protect an existing destination."""

import unittest

from artcraft_ssd_installer import compare_versions, destination_decision, same_release


class DestinationDecisionTests(unittest.TestCase):
    def test_new_destination_is_installable(self) -> None:
        self.assertEqual(destination_decision(False, None, False, "1.0.0"), "install")

    def test_verified_same_version_is_rerun_skip(self) -> None:
        self.assertEqual(
            destination_decision(True, "1.0.0", True, "1.0.0"),
            "skip_verified",
        )

    def test_verification_failure_never_overwrites(self) -> None:
        self.assertEqual(
            destination_decision(True, "1.0.0", False, "1.0.0"),
            "hold_existing_destination",
        )

    def test_different_version_never_overwrites(self) -> None:
        self.assertEqual(
            destination_decision(True, "1.1.0", True, "1.0.0"),
            "hold_existing_destination",
        )

    def test_newer_internal_version_is_detected(self) -> None:
        self.assertEqual(compare_versions("1.1.0", "1.0.9"), 1)

    def test_release_match_uses_numeric_triplet(self) -> None:
        self.assertTrue(same_release("1.2.3", "1.2.3-rc.1"))
        self.assertFalse(same_release("1.2.4", "1.2.3"))


if __name__ == "__main__":
    unittest.main()
