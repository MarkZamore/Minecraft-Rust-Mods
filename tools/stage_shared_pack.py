"""Compose Rust Mods' pack overlay from an accepted static pack and exact SDK sidecars."""

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import stat
import sys
import tempfile
import tomllib
import zipfile

from inspect_mods import validate_members
from native_build import ROOT, module_at, write


DESCRIPTOR = "portable-pack.json"
CLIENT = "native/horizon-client.exe"
PRESENTATION = "native/presentation.json"
PROFILE = "native/native-mods.json"
COMPOSITION = "shared-composition.json"
CODE_PACKAGE = "native/horizon-native-mods/"
CONTENT_PACKAGE = "native/horizon-mods/"
LIBRARY = ("content.json", "mod-pages.json", "loaded-mods.json", "preview.jpg")
MANAGED_DIRECTORIES = ("native", "launcher", "configureddefaults")
LINK_FORMATS = {".dll", ".exe", ".hmod", ".jar", ".ogg", ".png", ".wasm"}


def require(condition, reason):
    if not condition:
        raise ValueError(reason)


def normalized(path):
    text = str(path)
    if os.name == "nt":
        if text.startswith("\\\\?\\UNC\\"):
            return Path("\\\\" + text[8:])
        if text.startswith("\\\\?\\"):
            return Path(text[4:])
    return path


def ordinary(path, release=None, *, directory=False):
    access = path if release is None else release.file_access_path(path)
    info = access.lstat()
    check = stat.S_ISDIR if directory else stat.S_ISREG
    require(check(info.st_mode) and not getattr(info, "st_file_attributes", 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT
            and normalized(access.resolve(strict=True)) == normalized(path),
            f"Expected an ordinary {'directory' if directory else 'file'}: {path}")
    return path


def registered(path, release):
    for root in (path, *path.parents):
        if root.parent.name == "agent-runs" and root.parent.parent.name == "artifacts":
            ordinary(root, release, directory=True)
            run = read(root / "RUN.json", release)
            require(run.get("status") == "active" and run.get("owner"), f"Inactive registered run: {root}")
            return root
    raise ValueError(f"Use a registered LANMinecraft run: {path}")


def read(path, release):
    return json.loads(release.file_access_path(ordinary(path, release)).read_bytes())


def collect(root, folder, release):
    for entry in release.file_access_path(ordinary(folder, release, directory=True)).iterdir():
        child = folder / entry.name
        access = release.file_access_path(child)
        if access.is_dir() and not access.is_symlink() and not access.is_junction():
            yield from collect(root, child, release)
        else:
            relative = release.safe_path(ordinary(child, release).relative_to(root).as_posix()).as_posix()
            yield relative, release.identity(child)


def exact_files(mapping, expected, label):
    require(mapping == expected, f"{label} differs from its approved files: "
            f"{sorted(name for name in mapping.keys() | expected.keys() if mapping.get(name) != expected.get(name))[:8]}")


def static_inputs(candidate, lock_file, release):
    ordinary(candidate, release, directory=True)
    lock = read(lock_file, release)
    build_file = ordinary(candidate / "build-receipt.json", release)
    build = read(build_file, release)
    require(lock["schemaVersion"] == 1 and build["schemaVersion"] == 1
            and release.identity(build_file)["sha256"] == lock["receiptSha256"],
            "The static candidate must match its accepted build receipt")
    files = release.file_map(release.rows(lock["files"]))
    managed = {DESCRIPTOR: release.identity(ordinary(candidate / DESCRIPTOR, release))}
    for folder in MANAGED_DIRECTORIES:
        managed.update(collect(candidate, candidate / folder, release))
    exact_files(managed, files, "Static payload")
    require(build["clientSha256"] == files[CLIENT]["sha256"], "The static client differs from its receipt")
    library = dict(collect(candidate / "library", candidate / "library", release))
    require(set(library) == set(LIBRARY), "Expected the four approved Mods library files")
    exact_files(library, lock["library"], "Static library")
    loaded = read(candidate / "library/loaded-mods.json", release)
    versions = {row["id"]: row["version"] for row in loaded["mods"]}
    require(len(versions) == len(loaded["mods"]) == len(build["ids"]) and set(versions) == set(build["ids"]),
            "Static receipt and approved mod membership disagree")
    return lock, build, files, versions


def sidecar_inputs(sidecars, common, versions, release):
    receipt_file = sidecars / "mod-sidecar-receipt.json"
    receipt = read(receipt_file, release)
    payload = sidecars / "pack"
    files = release.file_map(release.rows(dict(collect(payload, payload / "native", release))))
    profile = read(payload / PROFILE, release)
    hashes = {path.removeprefix("native/"): row["sha256"] for path, row in files.items()}
    tree_hash = hashlib.sha256(json.dumps(hashes, sort_keys=True).encode("ascii")).hexdigest()
    require(receipt["schemaVersion"] == 1 and files[PROFILE]["sha256"] == receipt["nativeProfileSha256"]
            and tree_hash == receipt["nativeTreeSha256"], "The native profile or staged sidecar bytes differ from the SDK receipt")
    require(receipt["clientSha256"] == common[CLIENT]["sha256"]
            and all(receipt[key] == profile[key] for key in ("abiBuildId", "sdkIdentitySha256", "clientSha256"))
            and re.fullmatch(r"[0-9a-f]{40}", receipt["abiBuildId"])
            and all(re.fullmatch(r"[0-9a-f]{64}", receipt[key]) for key in ("sdkIdentitySha256", "packInputsSha256")),
            "The SDK sidecars are bound to a different shared executable or ABI")
    compiled = {row["id"]: row for row in receipt["compiledMods"]}
    require(len(compiled) == len(receipt["compiledMods"]) and {key: row["version"] for key, row in compiled.items()} == versions,
            "The sidecar build differs from the accepted pack's exact mod membership")
    libraries = {}
    for row in profile["libraries"]:
        name = release.safe_name(row["file"])
        path = f"native/{name}"
        parts = row["identity"].split()
        require(name.lower().endswith(".dll") and path in files and files[path]["sha256"] == row["sha256"]
                and row["mod"] not in libraries and len(parts) == 4 and parts[:2] == [profile["abiBuildId"], row["mod"]]
                and ((row["kind"] == "patch" and row["mod"] == "horizon_patch")
                     or (row["kind"] == "mod" and versions.get(row["mod"]) == parts[2]
                         and compiled[row["mod"]]["source"] == path)), f"Invalid ABI library in profile: {name}")
        libraries[row["mod"]] = path
    require(len(set(libraries.values())) == len(libraries), "A profile library path is used more than once")
    legacy = profile["legacyModIds"]
    patch = libraries.get("horizon_patch")
    require((not legacy or patch) and len(set(legacy)) == len(legacy)
            and all(mod in compiled and compiled[mod]["source"] in {patch, libraries.get(mod)} for mod in legacy),
            "Legacy mod profile does not match the generated patch and registrar DLLs")
    for mod, row in compiled.items():
        path = release.safe_path(row["source"]).as_posix()
        require(path in files and files[path]["sha256"] == row["sha256"]
                and path in {libraries.get(mod), patch, f"{CONTENT_PACKAGE}{mod}.hmod"},
                f"Wrong exact SDK payload origin for {mod}")
    assets = {f"{CONTENT_PACKAGE}{mod}.hmod" for mod in compiled}
    packages = tuple(f"{CODE_PACKAGE}{mod}/" for mod in libraries if mod in compiled)
    require(all(path == PROFILE or path in libraries.values() or path in assets or path.startswith(packages)
                for path in files), "The SDK sidecars include a file outside their pack-owned scope")
    return payload, receipt, files, libraries


def abi_manifest_only(original, replacement, before, after, release):
    expected = tomllib.loads(release.file_access_path(ordinary(original, release)).read_text(encoding="utf-8"))
    descriptor = tomllib.loads(release.file_access_path(ordinary(replacement, release)).read_text(encoding="utf-8"))
    if expected.get("native", {}).get("horizon") != before:
        return False
    expected["native"]["horizon"] = after
    return expected == descriptor


def overlay_sources(candidate, static, payload, sidecars, common, bootstrap, horizon, abi, release):
    allowed = {CLIENT, bootstrap}
    require(all(name in allowed or static[name] == common[name] for name in static.keys() & common.keys()),
            "The shared runtime would replace unrelated static bytes")
    sources = {name: (candidate / name, row) for name, row in static.items()
               if name not in common and name not in {DESCRIPTOR, PROFILE}}
    for name, row in sidecars.items():
        require(name not in common, f"Sidecar collides with the shared runtime: {name}")
        if name in sources and sources[name][1] != row:
            require(name.startswith(CODE_PACKAGE) and name.endswith("/horizon-mod.toml")
                    and abi_manifest_only(sources[name][0], payload / name, horizon, abi, release),
                    f"Sidecar would change already approved pack data: {name}")
        sources[name] = (payload / name, row)
    return sources


def folded_content_companions(sources, compiled_mods, libraries, abi, release):
    folded = []
    for mod in compiled_mods:
        id, version = mod["id"], mod["version"]
        archive_name = f"{CONTENT_PACKAGE}{id}.hmod"
        if id not in libraries or archive_name not in sources:
            continue
        package = f"{CODE_PACKAGE}{id}"
        manifest_source, manifest_identity = sources[f"{package}/horizon-mod.toml"]
        manifest = release.file_access_path(ordinary(manifest_source, release))
        require(release.identity(manifest) == manifest_identity, f"Native manifest changed: {id}")
        native = tomllib.loads(manifest.read_text(encoding="utf-8"))
        binding = native.pop("native", None)
        require(isinstance(binding, dict) and binding.get("horizon") == abi, f"Wrong native package ABI: {id}")
        archive_source, archive_identity = sources[archive_name]
        path = release.file_access_path(ordinary(archive_source, release))
        hashes = {}
        with path.open("rb") as stream:
            require({"sizeBytes": os.fstat(stream.fileno()).st_size,
                     "sha256": hashlib.file_digest(stream, "sha256").hexdigest()} == archive_identity,
                    f"Content companion changed: {id}")
            stream.seek(0)
            with zipfile.ZipFile(stream) as archive:
                validate_members(archive)
                info = archive.getinfo("horizon-mod.toml")
                require(info.file_size <= (32 << 20), f"Content companion manifest is too large: {id}")
                authored = tomllib.loads(archive.read(info).decode("utf-8"))
                require(authored == native and authored.get("id") == id and authored.get("version") == version
                        and "wasm" not in authored and "native" not in authored,
                        f"Content companion differs from its native manifest: {id}")
                for member in archive.infolist():
                    if member.is_dir() or member.filename == info.filename:
                        continue
                    relative = release.safe_path(member.filename).as_posix()
                    require(relative.startswith(("assets/", "data/")), f"Content companion contains a non-resource: {relative}")
                    with archive.open(member) as resource:
                        digest = hashlib.file_digest(resource, "sha256").hexdigest()
                    expected = {"sizeBytes": member.file_size, "sha256": digest}
                    actual = sources.get(f"{package}/{relative}")
                    require(actual is not None and actual[1] == expected, f"Missing or changed native companion resource: {id}/{relative}")
                    hashes[relative] = digest
        del sources[archive_name]
        folded.append({"id": id, "version": version, "archive": archive_name,
                       "archiveSha256": archive_identity["sha256"], "archiveSizeBytes": archive_identity["sizeBytes"],
                       "nativePackage": package, "resourceCount": len(hashes),
                       "resourcesTreeSha256": hashlib.sha256(json.dumps(hashes, sort_keys=True).encode("ascii")).hexdigest()})
    return folded


def copy_exact(source, destination, row, release, *, linked=False):
    source = release.file_access_path(ordinary(source, release))
    destination = release.file_access_path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    # A pre-existing alias may have owners outside the two registered runs.
    if linked and source.stat().st_nlink == 1:
        os.link(source, destination)
    else:
        shutil.copyfile(source, destination)
    require(release.identity(destination) == row, f"Payload bytes changed while staging: {source}")


def stage(args, release):
    inputs = (args.static_candidate, args.sidecars, args.runtime, args.common_candidate)
    for directory in (args.overlay, args.qa_view):
        if directory is not None:
            ordinary(directory.parent, release, directory=True)
            target = release.file_access_path(directory)
            require(not target.exists() and not target.is_symlink() and not target.is_junction(),
                    f"Use a fresh output: {directory}")
            require(not any(source is not None and directory.is_relative_to(source) for source in inputs),
                    f"An output cannot modify an input tree: {directory}")
    run = registered(args.overlay, release)
    require((args.qa_view is None) == (args.common_candidate is None), "Supply --qa-view and --common-candidate together")
    if args.qa_view is not None:
        require(args.qa_view != args.overlay and registered(args.qa_view, release) == registered(args.common_candidate, release) == run,
                "The QA view, common candidate and overlay must belong to the same active run")
        require(registered(args.static_candidate, release).parent == run.parent and registered(args.sidecars, release) == run,
                "QA sources must come from the two registered project runs")
    lock, build, static, versions = static_inputs(args.static_candidate, args.static_lock, release)
    ordinary(args.runtime, release, directory=True)
    spec, common, minecraft = release.read_runtime(args.runtime)
    descriptor = release.bind_descriptor(read(args.static_candidate / DESCRIPTOR, release), spec, common, minecraft)
    bootstrap = descriptor["nativeRuntime"]["executable"]
    require({CLIENT, "native/horizon-prepare.exe", "native/research-lock.json"} <= common.keys(),
            "The shared archive must supply the game client and pinned resource preparer")
    require({PRESENTATION, PROFILE, CLIENT, bootstrap} <= static.keys(), "Incomplete accepted static native payload")
    payload, receipt, sidecars, libraries = sidecar_inputs(args.sidecars, common, versions, release)
    sources = overlay_sources(args.static_candidate, static, payload, sidecars, common, bootstrap,
                              build["horizonRevision"], receipt["abiBuildId"], release)
    folded = folded_content_companions(sources, receipt["compiledMods"], libraries, receipt["abiBuildId"], release)

    with tempfile.TemporaryDirectory(prefix="stage-mod-overlay-", dir=run) as temporary:
        staged = Path(temporary) / "overlay"
        staged.mkdir()
        for name, (source, row) in sources.items():
            copy_exact(source, staged / name, row, release)
        for name, row in lock["library"].items():
            copy_exact(args.static_candidate / "library" / name, staged / "library" / name, row, release)
        write(release.file_access_path(staged / DESCRIPTOR), descriptor)
        files = {**{name: row for name, (_, row) in sources.items()}, DESCRIPTOR: release.identity(staged / DESCRIPTOR)}
        release.assert_disjoint(common, files)
        report = {"schemaVersion": 1, "staticBuildReceiptSha256": lock["receiptSha256"],
                  "staticFileLockStatus": "provisional-file-verification" if lock.get("provisional") else "approved",
                  "staticDistributionLockSha256": release.identity(args.static_lock)["sha256"],
                  "sidecarReceiptSha256": release.identity(args.sidecars / "mod-sidecar-receipt.json")["sha256"],
                  "sharedRuntimeArchiveSha256": spec["archive"]["sha256"], "abiBuildId": receipt["abiBuildId"],
                  "bootstrapSha256": common[bootstrap]["sha256"], "clientSha256": receipt["clientSha256"],
                  "compiledMods": receipt["compiledMods"], "foldedContentCompanions": folded,
                  "overlayFiles": dict(sorted(files.items())),
                  "files": dict(sorted((common | files).items()))}
        write(release.file_access_path(staged / COMPOSITION), report)
        if args.qa_view is not None:
            view = Path(temporary) / "qa-view"
            view.mkdir()
            for inventory, source_root in ((common, args.common_candidate), (files, staged)):
                for name, row in inventory.items():
                    link = name.startswith("native/") and Path(name).suffix.lower() in LINK_FORMATS
                    copy_exact(source_root / name, view / name, row, release, linked=link)
            for name, row in lock["library"].items():
                copy_exact(staged / "library" / name, view / "library" / name, row, release)
            copy_exact(staged / COMPOSITION, view / COMPOSITION, release.identity(staged / COMPOSITION), release)
            write(release.file_access_path(view / "build-receipt.json"), {"schemaVersion": 1, "clientSha256": receipt["clientSha256"],
                  "horizonRevision": build["horizonRevision"], "inputLockSha256": build["inputLockSha256"],
                  "ids": build["ids"], "sharedCompositionSha256": release.identity(view / COMPOSITION)["sha256"]})
            release.file_access_path(view).rename(release.file_access_path(args.qa_view))
        release.file_access_path(staged).rename(release.file_access_path(args.overlay))
    return args.overlay / COMPOSITION


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("static-candidate", "static-lock", "sidecars", "runtime", "overlay", "common-candidate", "qa-view"):
        parser.add_argument(f"--{name}", required=name not in {"common-candidate", "qa-view"},
                            type=lambda value: Path(os.path.abspath(value)))
    parser.add_argument("--release-tool", type=Path, default=ROOT.parents[1] / "tools/native_runtime_release.py")
    args = parser.parse_args()
    release_tool = ordinary(Path(os.path.abspath(args.release_tool)))
    sys.path.insert(0, str(release_tool.parent))
    try:
        print(stage(args, module_at(release_tool)))
    except (KeyError, OSError, ValueError, zipfile.BadZipFile) as error:
        parser.exit(1, f"Shared Mods staging: {error}\n")
    finally:
        sys.path.pop(0)


if __name__ == "__main__":
    main()
