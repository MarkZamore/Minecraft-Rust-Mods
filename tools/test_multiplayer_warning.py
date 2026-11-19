from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

import multiplayer_warning as policy
from native_build import restore_partial_source
from server_seed_compat import SOURCE


def write(root, name, data):
    target = root / name
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(data)


class MultiplayerWarningTests(unittest.TestCase):
    def test_exact_edition_policy_preserves_its_tests(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            write(root, policy.TITLE, policy.EDITION_ACTION)
            write(root, policy.TITLE_TESTS, policy.EDITION_TEST)
            policy.prepare_source(root)
            self.assertEqual(policy.verify_prepared_source(root), policy.PACK_ACTION_SHA256)
            self.assertEqual((root / policy.TITLE_TESTS).read_bytes(), policy.EDITION_TEST)

    def test_edition_route_with_an_unverified_preference_is_rejected(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            write(root, policy.TITLE, policy.EDITION_ACTION)
            write(root, policy.TITLE_TESTS, b'    f.click("Proceed");\n')
            with self.assertRaisesRegex(ValueError, "preserves the saved warning preference"):
                policy.prepare_source(root)
            self.assertEqual((root / policy.TITLE).read_bytes(), policy.EDITION_ACTION)

    def test_latest_edition_route_keeps_set_mode(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            write(root, policy.TITLE, policy.SET_MODE_EDITION_ACTION)
            write(root, policy.TITLE_TESTS, policy.EDITION_TEST)
            policy.prepare_source(root)
            self.assertEqual((root / policy.TITLE).read_bytes(), policy.SET_MODE_ACTION.encode())
            self.assertEqual(policy.verify_prepared_source(root), policy.expected_action_sha256(policy.SET_MODE_REVISION))
            self.assertEqual((root / policy.TITLE_TESTS).read_bytes(), policy.EDITION_TEST)
            write(root, policy.TITLE, policy.SET_MODE_WRAPPED_ACTION.encode())
            self.assertEqual(policy.verify_prepared_source(root), policy.expected_action_sha256(policy.SET_MODE_REVISION))

    def test_source_only_resume_refuses_unknown_changes_before_restoring_known_files(self):
        with TemporaryDirectory() as directory:
            base = (Path(directory) / "base").resolve()
            work = base.parent / "work"
            source = work / "source"
            for root in (base, source):
                write(root, "unchanged", b"exact")
            write(base, SOURCE, b"base seeding")
            write(source, SOURCE, b"disabled seeding")
            write(source, ".horizon-revision", b"revision\n")
            write(source, "unchanged", b"unexpected change")
            with patch("server_seed_compat.adapt", return_value="disabled seeding"):
                with self.assertRaisesRegex(ValueError, "unreviewed change"):
                    restore_partial_source(base, work, "revision")
                self.assertEqual((source / SOURCE).read_bytes(), b"disabled seeding")
                write(source, "unchanged", b"exact")
                restore_partial_source(base, work, "revision")
            self.assertEqual((source / SOURCE).read_bytes(), b"base seeding")


if __name__ == "__main__":
    unittest.main()
