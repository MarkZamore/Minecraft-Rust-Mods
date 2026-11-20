"""Bind direct Rust Mods QA to the staged shared client and its pack files."""

import hashlib
import json
import os
from pathlib import Path
import sys
from types import SimpleNamespace

from native_build import ROOT, module_at
from native_qa_environment import shared_pack_root
from stage_shared_pack import CLIENT, COMPOSITION, DESCRIPTOR, MANAGED_DIRECTORIES, PROFILE, collect, read, require


def release_tools():
    tool = ROOT.parents[1] / "tools/native_runtime_release.py"
    sys.path.insert(0, str(tool.parent))
    try:
        release = module_at(tool)
    finally:
        sys.path.pop(0)
    # The launcher checkout may predate the exporter that added Windows long
    # paths. Use its same path and hash validators with ordinary absolute paths.
    if not hasattr(release, "file_access_path"):
        def access(path):
            value = str(path)
            if os.name != "nt" or value.startswith("\\\\?\\"):
                return path
            if value.startswith("\\\\"):
                return Path("\\\\?\\UNC\\" + value[2:])
            return Path("\\\\?\\" + value)

        return SimpleNamespace(file_access_path=access, identity=lambda path: release.identity(access(path)),
                               safe_path=release.safe_path, rows=release.rows, file_map=release.file_map)
    return release


def shared_identity(candidate, client_sha):
    """Check the runnable QA view against its composition, including every sidecar."""
    if shared_pack_root(candidate) is None:
        return {}
    candidate = candidate.resolve(strict=True)
    release = release_tools()
    composition_path = candidate / COMPOSITION
    composition = read(composition_path, release)
    build = read(candidate / "build-receipt.json", release)
    digest = release.identity(composition_path)["sha256"]
    require(composition["schemaVersion"] == build["schemaVersion"] == 1
            and build["sharedCompositionSha256"] == digest
            and client_sha == composition["clientSha256"] == build["clientSha256"],
            "Shared QA build and composition do not identify the same exact client")
    expected = release.file_map(release.rows(composition["files"]))
    actual = {DESCRIPTOR: release.identity(candidate / DESCRIPTOR)}
    for folder in MANAGED_DIRECTORIES:
        actual.update(collect(candidate, candidate / folder, release))
    require(actual == expected and actual[CLIENT]["sha256"] == client_sha,
            "Shared QA payload differs from its exact composition")
    profile = read(candidate / PROFILE, release)
    runtime = read(candidate / DESCRIPTOR, release)["nativeRuntime"]["sharedRuntime"]
    require(profile["clientSha256"] == client_sha and profile["abiBuildId"] == composition["abiBuildId"]
            and runtime["archive"]["sha256"] == composition["sharedRuntimeArchiveSha256"]
            and sorted(build["ids"]) == sorted(mod["id"] for mod in composition["compiledMods"]),
            "Shared QA native profile, runtime or mod roster differs from its composition")
    return {"sharedCompositionSha256": digest,
            "sharedPayloadSha256": hashlib.sha256(json.dumps(actual, sort_keys=True).encode("ascii")).hexdigest()}
