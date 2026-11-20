import hashlib
import io
import json
import os
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
import zipfile

import stage_shared_pack as staging
from native_build import ROOT

sys.path.append(str(ROOT.parents[1] / "tools"))
import native_runtime_release as release


RUNTIME = {"executable": "native/launch-rust-edition.exe", "platform": "windows-x64",
           "adapter": "portable-native", "version": "steam-mods-variant"}
HORIZON, ABI = "a" * 40, "b" * 40
VERSIONS = {"legacy": "1", "registrar": "2", "zoom": "3"}
WASM = "native/horizon-mods/zoom.hmod"
COMPANION = "native/horizon-mods/registrar.hmod"
SHADER = "assets/registrar/shaders/post/example.wgsl"
NATIVE_SHADER = "native/horizon-native-mods/registrar/" + SHADER
REGISTRAR = "native/registrar.dll"
PATCH = "native/horizon_patch.dll"
PACKAGE = "native/horizon-native-mods/registrar/horizon-mod.toml"
LICENSE = "native/third_party/mods/legacy/LICENSE"


def populate(root, files):
    for name, content in files.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(content, dict):
            release.write(path, content)
        else:
            path.write_bytes(content)
    return {name: release.identity(root / name) for name in files}


def package(abi):
    return f'id = "registrar"\nversion = "2"\n[native]\nhorizon = "{abi}"\n'.encode()


def fixture(root):
    runs = root / "artifacts/agent-runs"
    shared, static = runs / "shared", runs / "accepted"
    for run in (shared, static):
        populate(run, {"RUN.json": {"owner": "shared-staging-test", "status": "active"}})
    common_core = {"native/steam_api64.dll": b"steam", "native/horizon-prepare.exe": b"prepare",
                   "native/research-lock.json": b"research"}
    spec = {"schemaVersion": 1, "id": "family", "version": "engine-exact", "platform": "windows-x64"}
    common = shared / "common"
    common_files = populate(common, {**common_core, staging.CLIENT: b"shared client", RUNTIME["executable"]: b"shared bootstrap",
                                     release.FAMILY: {**spec, "minecraftVersion": "26.3"}})
    runtime = shared / "runtime-dist"
    release.stage_runtime(common, {**spec, "source": {"owner": "owner", "repo": "repo", "tag": "runtime-exact"},
                                   "files": release.rows(common_files)}, runtime)
    candidate = static / "candidate"
    files = populate(candidate, {**common_core, staging.CLIENT: b"old client", RUNTIME["executable"]: b"old bootstrap",
                                 staging.DESCRIPTOR: {"schemaVersion": 2, "minecraftVersion": "26.3", "nativeRuntime": RUNTIME},
                                 staging.PROFILE: {"mods": []}, staging.PRESENTATION: {"name": "Mods fixture"},
                                 "configureddefaults/options.txt": b"personal template", "launcher/controls-preset.txt": b"keys",
                                 PACKAGE: package(HORIZON), WASM: b"wasm payload", LICENSE: b"author license"})
    library = populate(candidate / "library", {"content.json": {"name": "Mods fixture"},
           "mod-pages.json": {}, "preview.jpg": b"preview", "loaded-mods.json": {"mods": [
               {"id": mod, "version": version} for mod, version in VERSIONS.items()]}})
    build = populate(candidate, {"build-receipt.json": {"schemaVersion": 1, "horizonRevision": HORIZON,
           "clientSha256": files[staging.CLIENT]["sha256"], "inputLockSha256": "c" * 64, "ids": list(VERSIONS)}})
    static_lock = static / "lock.json"
    release.write(static_lock, {"schemaVersion": 1, "receiptSha256": build["build-receipt.json"]["sha256"],
                                "files": files, "library": library})
    sidecars = shared / "sidecars"
    payload = sidecars / "pack"
    source_files = populate(payload, {PACKAGE: package(ABI), WASM: b"wasm payload", REGISTRAR: b"registrar", PATCH: b"patch"})
    identity = {"abiBuildId": ABI, "sdkIdentitySha256": "d" * 64, "clientSha256": common_files[staging.CLIENT]["sha256"]}
    profile = populate(payload, {staging.PROFILE: {**identity, "legacyModIds": ["legacy", "registrar"], "libraries": [
        {"file": Path(name).name, "sha256": source_files[name]["sha256"], "kind": kind,
         "mod": mod, "identity": f"{ABI} {mod} {version} 1.99.1"}
        for name, kind, mod, version in ((REGISTRAR, "mod", "registrar", "2"), (PATCH, "patch", "horizon_patch", "0"))]}})
    hashes = {name.removeprefix("native/"): record["sha256"] for name, record in (source_files | profile).items()}
    release.write(sidecars / "mod-sidecar-receipt.json", {"schemaVersion": 1, **identity,
          "nativeProfileSha256": profile[staging.PROFILE]["sha256"], "packInputsSha256": "e" * 64,
          "nativeTreeSha256": hashlib.sha256(json.dumps(hashes, sort_keys=True).encode("ascii")).hexdigest(),
          "compiledMods": [{"id": mod, "version": VERSIONS[mod], "source": name, "sha256": source_files[name]["sha256"]}
                           for mod, name in (("legacy", PATCH), ("registrar", REGISTRAR), ("zoom", WASM))]})
    return SimpleNamespace(static_candidate=candidate, static_lock=static_lock, sidecars=sidecars, runtime=runtime,
                           overlay=shared / "overlay", qa_view=shared / "qa-view", common_candidate=common)


def add_companion(args, *, native_shader=b"verified shader"):
    data = io.BytesIO()
    with zipfile.ZipFile(data, "w") as archive:
        archive.writestr("horizon-mod.toml", 'id = "registrar"\nversion = "2"\n')
        archive.writestr(SHADER, b"verified shader")
    contents = {COMPANION: data.getvalue(), NATIVE_SHADER: native_shader}
    lock = release.read(args.static_lock)
    lock["files"].update(populate(args.static_candidate, contents))
    release.write(args.static_lock, lock)
    payload = args.sidecars / "pack"
    records = populate(payload, contents)
    receipt_path = args.sidecars / "mod-sidecar-receipt.json"
    receipt = release.read(receipt_path)
    hashes = {name.removeprefix("native/"): row["sha256"]
              for name, row in staging.collect(payload, payload / "native", release)}
    receipt["nativeTreeSha256"] = hashlib.sha256(json.dumps(hashes, sort_keys=True).encode("ascii")).hexdigest()
    release.write(receipt_path, receipt)
    return records[COMPANION]


class StageSharedPackTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.args = fixture(Path(temporary.name).resolve())

    def test_preserves_overlay_and_only_links_immutable_qa_files(self):
        args = self.args
        proof = release.read(staging.stage(args, release))
        self.assertFalse((args.overlay / staging.CLIENT).exists())
        self.assertEqual((args.overlay / LICENSE).read_bytes(), b"author license")
        self.assertEqual((args.overlay / PACKAGE).read_bytes(), package(ABI))
        local = release.read(args.overlay / staging.DESCRIPTOR)
        self.assertEqual(local["nativeRuntime"]["version"], RUNTIME["version"])
        self.assertEqual(release.read(args.overlay / staging.PRESENTATION)["name"], "Mods fixture")
        self.assertTrue((args.qa_view / staging.CLIENT).samefile(args.common_candidate / staging.CLIENT))
        self.assertTrue((args.qa_view / PATCH).samefile(args.overlay / PATCH))
        for name in (staging.DESCRIPTOR, staging.PROFILE, staging.PRESENTATION, "configureddefaults/options.txt",
                     "library/content.json", staging.COMPOSITION):
            self.assertFalse((args.qa_view / name).samefile(args.overlay / name))
            self.assertEqual((args.qa_view / name).read_bytes(), (args.overlay / name).read_bytes())
        self.assertEqual(set(proof["files"]), set(proof["overlayFiles"]) | set(release.read_runtime(args.runtime)[1]))

    def test_rejects_mutated_sidecar_without_publishing_an_overlay(self):
        (self.args.sidecars / "pack" / REGISTRAR).write_bytes(b"modified")
        with self.assertRaisesRegex(ValueError, "sidecar bytes differ"):
            staging.stage(self.args, release)
        self.assertFalse(self.args.overlay.exists())
        self.assertFalse(self.args.qa_view.exists())

    def test_folds_same_id_companion_when_every_resource_matches(self):
        args = self.args
        original = add_companion(args)
        sidecar_receipt = release.identity(args.sidecars / "mod-sidecar-receipt.json")
        proof = release.read(staging.stage(args, release))
        files = proof["overlayFiles"]
        self.assertNotIn(COMPANION, files)
        self.assertFalse((args.qa_view / COMPANION).exists())
        self.assertEqual((args.overlay / WASM).read_bytes(), b"wasm payload")
        self.assertEqual((args.qa_view / NATIVE_SHADER).read_bytes(), b"verified shader")
        self.assertEqual(release.identity(args.sidecars / "pack" / COMPANION), original)
        self.assertEqual(release.identity(args.sidecars / "mod-sidecar-receipt.json"), sidecar_receipt)
        hashes = {SHADER: hashlib.sha256(b"verified shader").hexdigest()}
        self.assertEqual(proof["foldedContentCompanions"], [{
            "id": "registrar", "version": "2", "archive": COMPANION,
            "archiveSha256": original["sha256"], "archiveSizeBytes": original["sizeBytes"],
            "nativePackage": "native/horizon-native-mods/registrar", "resourceCount": 1,
            "resourcesTreeSha256": hashlib.sha256(json.dumps(hashes, sort_keys=True).encode("ascii")).hexdigest(),
        }])

    def test_rejects_different_native_shader_in_a_same_id_companion(self):
        add_companion(self.args, native_shader=b"different shader")
        with self.assertRaisesRegex(ValueError, "Missing or changed native companion resource"):
            staging.stage(self.args, release)
        self.assertFalse(self.args.overlay.exists())
        self.assertFalse(self.args.qa_view.exists())

    def test_preserves_bound_receipt_when_qa_view_source_was_altered(self):
        (self.args.common_candidate / staging.CLIENT).write_bytes(b"modified")
        with self.assertRaisesRegex(ValueError, "Payload bytes changed"):
            staging.stage(self.args, release)
        self.assertFalse(self.args.overlay.exists())
        self.assertFalse(self.args.qa_view.exists())

    def test_existing_hardlink_is_copied_without_extending_its_ownership(self):
        source = self.args.common_candidate / staging.CLIENT
        alias = self.args.static_candidate.parent / "preexisting-engine-alias.exe"
        os.link(source, alias)
        staging.stage(self.args, release)
        observed = self.args.qa_view / staging.CLIENT
        self.assertFalse(observed.samefile(alias))
        self.assertEqual(observed.read_bytes(), alias.read_bytes())

    @unittest.skipUnless(os.name == "nt", "Windows path namespace")
    def test_checks_and_links_extended_windows_paths(self):
        name = "native/" + ("long-component-" * 9) + "/" + ("nested-name-" * 9) + "/engine.dll"
        source = self.args.common_candidate / name
        target = self.args.overlay / name
        self.assertGreaterEqual(len(str(source)), 260)
        self.assertGreaterEqual(len(str(target)), 260)
        access = release.file_access_path(source)
        access.parent.mkdir(parents=True)
        access.write_bytes(b"long-path fixture")
        expected = release.identity(source)
        try:
            staging.copy_exact(source, target, expected, release, linked=True)
            self.assertEqual(dict(staging.collect(self.args.overlay, self.args.overlay / "native", release)),
                             {name: expected})
            self.assertTrue(access.samefile(release.file_access_path(target)))
        finally:
            for file in (target, source):
                release.file_access_path(file).unlink(missing_ok=True)
                for parent in (file.parent, file.parent.parent):
                    directory = release.file_access_path(parent)
                    if directory.is_dir():
                        directory.rmdir()


if __name__ == "__main__":
    unittest.main()
