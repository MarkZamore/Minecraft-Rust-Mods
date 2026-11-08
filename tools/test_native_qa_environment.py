"""Pure environment-policy regression checks; no game, GPU or OS settings changes."""

import argparse
import hashlib
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from native_qa_environment import (
    direct_client_environment, environment_policy, prepare_client_environment, validate_environment_defaults,
)


WORK = None


class EnvironmentDefaultsTests(unittest.TestCase):
    def candidate(self, defaults=None, *, missing=False):
        temporary = tempfile.TemporaryDirectory(prefix="qa-environment-", dir=WORK)
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        (root / "native").mkdir()
        presentation = {"name": "QA only"}
        if not missing:
            presentation["environmentDefaults"] = defaults
        (root / "native/presentation.json").write_text(json.dumps(presentation), encoding="utf-8")
        return root

    def test_missing_field_keeps_legacy_environment(self):
        candidate = self.candidate(missing=True)
        original = {"WGPU_BACKEND": "dx12", "SteamOverlayGameId": "123", "PERSONAL": ""}
        environment, evidence = prepare_client_environment(candidate, original)
        self.assertEqual(environment, original)
        self.assertIsNot(environment, original)
        self.assertEqual(evidence["environmentDefaults"], {})
        self.assertEqual(evidence["effectiveEnvironmentDefaults"], {})

    def test_default_adds_only_missing_declared_key(self):
        candidate = self.candidate({"QA_PROCESS_FLAG": "on"})
        original = {"WGPU_BACKEND": "dx12", "SteamOverlayGameId": "123"}
        environment, evidence = prepare_client_environment(candidate, original)
        self.assertEqual(environment, {**original, "QA_PROCESS_FLAG": "on"})
        self.assertEqual(original, {"WGPU_BACKEND": "dx12", "SteamOverlayGameId": "123"})
        self.assertEqual(evidence["environmentDefaultOrigins"], {"QA_PROCESS_FLAG": "default"})

    def test_windows_case_and_inherited_empty_or_override_win(self):
        candidate = self.candidate({"QA_PROCESS_FLAG": "on"})
        for value in ("", "off"):
            with self.subTest(value=value):
                original = {"qa_process_flag": value}
                environment, evidence = prepare_client_environment(candidate, original)
                self.assertEqual(environment, original)
                self.assertEqual(evidence["effectiveEnvironmentDefaults"], {"QA_PROCESS_FLAG": value})
                self.assertEqual(evidence["environmentDefaultOrigins"], {"QA_PROCESS_FLAG": "inherited"})

    def test_context_restores_absent_key_even_after_failure(self):
        name = "NATIVE_QA_CONTEXT_TEST"
        candidate = self.candidate({name: "on"})
        with patch.dict(os.environ):
            os.environ.pop(name, None)
            with self.assertRaisesRegex(RuntimeError, "fixture failure"):
                with direct_client_environment(candidate) as evidence:
                    self.assertEqual(os.environ[name], "on")
                    self.assertEqual(evidence["environmentDefaultOrigins"][name], "default")
                    raise RuntimeError("fixture failure")
            self.assertNotIn(name, os.environ)

    def test_context_never_changes_inherited_empty_key(self):
        name = "NATIVE_QA_CONTEXT_TEST"
        candidate = self.candidate({name: "on"})
        with patch.dict(os.environ, {name: ""}):
            with direct_client_environment(candidate) as evidence:
                self.assertEqual(os.environ[name], "")
                self.assertEqual(evidence["environmentDefaultOrigins"][name], "inherited")
            self.assertEqual(os.environ[name], "")

    def test_invalid_defaults_rejected_before_spawn(self):
        invalid = [None, [], {"": "x"}, {"A=B": "x"}, {"QA_FLAG": True}, {"QA_FLAG": "x\x00"},
                   {"QA_FLAG": "\u0085"}, {"Q" * 65: "x"}, {"QA_FLAG": "x" * 1025},
                   {"QA_FLAG": "on", "qa_flag": "off"},
                   {f"QA_{i}": "" for i in range(17)}, {f"QA_{i}": "x" * 1024 for i in range(9)}]
        for value in invalid:
            with self.subTest(value_type=type(value).__name__):
                with self.assertRaises(ValueError):
                    validate_environment_defaults(value)

    def test_presentation_hash_identifies_actual_staged_bytes(self):
        candidate = self.candidate({"QA_PROCESS_FLAG": "on"})
        file = candidate / "native/presentation.json"
        policy = environment_policy(candidate)
        self.assertEqual(policy["presentationSha256"], hashlib.sha256(file.read_bytes()).hexdigest())
        file.write_bytes(file.read_bytes() + b"\n")
        changed = environment_policy(candidate)
        self.assertEqual(changed["environmentDefaults"], policy["environmentDefaults"])
        self.assertNotEqual(changed["presentationSha256"], policy["presentationSha256"])

    def test_duplicate_json_properties_fail_before_spawn(self):
        candidate = self.candidate(missing=True)
        file = candidate / "native/presentation.json"
        for raw in (b'{"environmentDefaults":{"QA_FLAG":"on","QA_FLAG":"off"}}',
                    b'{"environmentDefaults":{},"environmentDefaults":{"QA_FLAG":"on"}}'):
            with self.subTest(raw=raw):
                file.write_bytes(raw)
                with self.assertRaises(ValueError):
                    environment_policy(candidate)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--work", required=True, type=lambda value: Path(value).resolve())
    args = parser.parse_args()
    if (args.work.parent.name != "agent-runs" or args.work.parent.parent.name != "artifacts"
            or json.loads((args.work / "RUN.json").read_bytes()).get("status") != "active"):
        parser.error("Use the existing active registered task run; tiny fixtures are removed after each case")
    WORK = args.work
    unittest.main(argv=[__file__], verbosity=2)
