"""Describe the accepted Minecraft Rust Mods runtime, library and immutable release assets."""

import argparse
import json
import pathlib
import re
import shutil
import subprocess
import sys
import tempfile
import zipfile

from native_build import DECISIONS, INPUT_LOCK, RECIPE, ROOT, load, module_at, sha, write
from stage_mod_pack import CONTROLS_PRESET, OPTIONS_TEMPLATE, presentation


FEATURES = ["native-runtime-v1", "native-steam-v1", "minecraft-26-world-layout-v1"]
LIBRARY_FILES = ("content.json", "mod-pages.json", "loaded-mods.json", "preview.jpg")
FILE_SEGMENT = re.compile(r"[A-Za-z0-9_.$-]+")


def file_info(path):
    return {"sizeBytes": path.stat().st_size, "sha256": sha(path)}


def inventory(candidate):
    payload = candidate / "native"
    if not payload.is_dir() or payload.is_symlink() or payload.is_junction():
        raise ValueError("Expected a regular native payload directory")
    files = {"portable-pack.json": file_info(candidate / "portable-pack.json")}
    for path in sorted(payload.rglob("*"), key=lambda entry: entry.relative_to(candidate).as_posix()):
        if path.is_symlink() or path.is_junction():
            raise ValueError(f"Distribution contains a link: {path.name}")
        if not path.is_file():
            continue
        relative = path.relative_to(candidate).as_posix()
        if not all(FILE_SEGMENT.fullmatch(part) and not part.endswith(".")
                   and not pathlib.PureWindowsPath(part).is_reserved() for part in relative.split("/")):
            raise ValueError(f"Invalid Windows payload path: {relative}")
        files[relative] = file_info(path)
    if len(files) != len({name.casefold() for name in files}):
        raise ValueError("Distribution has duplicate Windows file paths")
    return dict(sorted(files.items()))


def describe(candidate):
    runtime = load(ROOT / "portable-pack.json")
    content = load(ROOT / "library/content.json")
    build = load(candidate / "build-receipt.json")
    approval = load(ROOT / "tools/build-lock.json")
    if (approval["receiptSha256"] != sha(candidate / "build-receipt.json") or
            build["inputLockSha256"] != sha(INPUT_LOCK) or build["conflictDecisionsSha256"] != sha(DECISIONS) or
            build["buildRecipeSha256"] != sha(RECIPE)):
        raise ValueError("Build receipt does not match the accepted mod inputs and build")
    files = inventory(candidate)
    descriptor = runtime["nativeRuntime"]
    if (load(candidate / "portable-pack.json") != runtime or descriptor["executable"] not in files or
            "native/steam_api64.dll" not in files or files["native/horizon-client.exe"]["sha256"] != build["clientSha256"]):
        raise ValueError("Candidate differs from the accepted runtime")
    if (content["nativeProject"]["revision"] != build["horizonRevision"] or
            load(candidate / "native/presentation.json") != presentation()):
        raise ValueError("Candidate presentation does not match the published library content")
    bootstrap = load(ROOT / "tools/bootstrap-lock.json")
    if files[descriptor["executable"]] != {"sizeBytes": bootstrap["sizeBytes"], "sha256": bootstrap["sha256"]}:
        raise ValueError("The staged bootstrap differs from the verified isolated native wrapper")
    for name in LIBRARY_FILES:
        if file_info(candidate / "library" / name) != file_info(ROOT / "library" / name):
            raise ValueError(f"The staged library file differs from source: {name}")
    with (candidate / "library/preview.jpg").open("rb") as image:
        if image.read(3) != b"\xff\xd8\xff":
            raise ValueError("The accepted game preview must be JPEG")
    loaded = load(candidate / "library/loaded-mods.json")
    projects = {row["id"]: row for row in load(INPUT_LOCK)["projects"]}
    expected = {mod: projects[mod]["version"] for mod in build["ids"]}
    actual = {mod["id"]: mod["version"] for mod in loaded["mods"]}
    if (loaded["schemaVersion"] != 1 or loaded["minecraftVersion"] != runtime["minecraftVersion"] or
            loaded["nativeRuntime"] != descriptor or actual != expected or len(actual) != len(loaded["mods"]) or
            loaded["nativeFiles"] != {path: item["sha256"] for path, item in files.items() if path.startswith("native/")}):
        raise ValueError("Actual mod inventory does not cover the accepted build")
    for mod in loaded["mods"]:
        project = projects[mod["id"]]
        path = "native/horizon-client.exe" if project["kind"] == "native-mod" else f"native/horizon-mods/{mod['id']}.hmod"
        if mod["source"] != path or path not in files:
            raise ValueError(f"Wrong payload origin for loaded mod {mod['id']}")
        if project["kind"] == "mod" and files[path]["sha256"] != project["file"]["sha256"]:
            raise ValueError(f"Content mod byte mismatch: {mod['id']}")
    for relative in (CONTROLS_PRESET, OPTIONS_TEMPLATE):
        path = candidate / relative
        if (path.parent.is_symlink() or path.parent.is_junction() or path.is_symlink() or
                path.is_junction() or not path.is_file() or file_info(path) != file_info(ROOT / relative)):
            raise ValueError(f"The staged controls file differs from its approved source: {relative}")
        files[relative] = file_info(path)
    files = dict(sorted(files.items()))
    return runtime, content, len(actual), {
        "schemaVersion": 1, "receiptSha256": approval["receiptSha256"], "files": files,
        "bootstrapLockSha256": sha(ROOT / "tools/bootstrap-lock.json"),
        "library": {name: file_info(candidate / "library" / name) for name in LIBRARY_FILES},
    }


def propose_inventory(candidate, report_path, probe_path):
    report = load(report_path)
    build = load(candidate / "build-receipt.json")
    files = inventory(candidate)
    runtime = load(candidate / "portable-pack.json")
    client = files["native/horizon-client.exe"]["sha256"]
    bootstrap = files[runtime["nativeRuntime"]["executable"]]["sha256"]
    if (build["clientSha256"] != client or build["inputLockSha256"] != sha(INPUT_LOCK) or
            report.get("schemaVersion") != 1 or report["clientSha256"] != client or
            report["bootstrapSha256"] != bootstrap or report["windowTitle"] != load(candidate / "native/presentation.json")["name"]):
        raise ValueError("Inventory needs a real world smoke report from this exact client and bootstrap")
    projects = {row["id"]: row for row in load(INPUT_LOCK)["projects"]}
    selected = [projects[mod] for mod in build["ids"]]
    expected_content = {(mod["id"], mod["version"]) for mod in selected if mod["kind"] == "mod"}
    expected_native = {mod["id"] for mod in selected if mod["kind"] == "native-mod"}
    if set(map(tuple, report["loadedContent"])) != expected_content:
        raise ValueError("The runtime did not accept the selected content mods")
    observed = set(report["registeredNative"])
    missing = expected_native - observed
    if observed - expected_native or set(report["nativeNeedingDirectProbes"]) != missing:
        raise ValueError("Native runtime observations differ from the expected set")
    if missing:
        if probe_path is None:
            raise ValueError(f"Add direct runtime evidence for native mods without a Mods row: {sorted(missing)}")
        probes = load(probe_path)
        proven = {probe["id"] for probe in probes["results"] if probe["status"] == "PASS" and probe["observation"].strip()}
        if probes.get("schemaVersion") != 1 or probes["clientSha256"] != client or not missing <= proven:
            raise ValueError(f"Direct runtime evidence is incomplete: {sorted(missing - proven)}")
    return {
        "schemaVersion": 1,
        "minecraftVersion": runtime["minecraftVersion"],
        "nativeRuntime": runtime["nativeRuntime"],
        "nativeFiles": {path: item["sha256"] for path, item in files.items() if path.startswith("native/")},
        "mods": [{"id": mod["id"], "name": mod["name"], "version": mod["version"],
                  "source": "native/horizon-client.exe" if mod["kind"] == "native-mod" else f"native/horizon-mods/{mod['id']}.hmod"}
                 for mod in selected],
    }


def zip_payload(output, candidate, paths, prefix):
    temporary = output / f"{prefix}.tmp"
    with zipfile.ZipFile(temporary, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for relative in paths:
            entry = zipfile.ZipInfo(relative, (1980, 1, 1, 0, 0, 0))
            entry.create_system = 3
            entry.external_attr = 0o100644 << 16
            entry.compress_type = zipfile.ZIP_DEFLATED
            archive.writestr(entry, (candidate / relative).read_bytes(), compresslevel=9)
    details = file_info(temporary)
    name = f"{prefix}-{details['sha256']}.zip"
    temporary.rename(output / name)
    return {"name": name, "kind": "zipRoot", **details, "covers": paths}


def export(args, output, runtime, content, count, distribution):
    assets = output / "assets"
    assets.mkdir(parents=True)
    files = distribution["files"]
    runtime_paths = [path for path in files if path != CONTROLS_PRESET]
    payload = zip_payload(assets, args.candidate, runtime_paths, "minecraft-rust-mods-windows-x64")
    launcher = zip_payload(assets, args.candidate, [CONTROLS_PRESET], "minecraft-rust-mods-launcher")
    sys.path.insert(0, str(args.library_tool.parent))
    try:
        library_tool = module_at(args.library_tool)
        library = library_tool.stage_library(args.candidate, output, args.revision)
        library_tool.verify_library(output, library)
    finally:
        sys.path.pop(0)
    write(output / "pack-manifest.json", {
        "schemaVersion": 2, "revision": args.revision, "packId": content["id"], "name": content["name"],
        "relativePath": content["relativePath"], "minecraftVersion": runtime["minecraftVersion"],
        "nativeRuntime": runtime["nativeRuntime"], "requiredLauncherFeatures": FEATURES, "modCount": count,
        "files": [{"path": name, **details} for name, details in files.items()], "assets": [payload, launcher], "library": library,
    })


def output_files(root):
    return {path.relative_to(root).as_posix(): file_info(path) for path in root.rglob("*") if path.is_file()}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--candidate", required=True, type=pathlib.Path)
    parser.add_argument("--out", type=pathlib.Path)
    parser.add_argument("--library-tool", type=pathlib.Path)
    parser.add_argument("--revision")
    action = parser.add_mutually_exclusive_group()
    action.add_argument("--verify-only", action="store_true")
    action.add_argument("--propose-lock", action="store_true")
    action.add_argument("--propose-inventory", type=pathlib.Path, metavar="SMOKE_JSON")
    parser.add_argument("--probe-evidence", type=pathlib.Path)
    args = parser.parse_args()
    if not args.propose_lock and args.propose_inventory is None and (args.out is None or args.library_tool is None or args.revision is None):
        parser.error("Generating or verifying assets requires output, library tool and exact revision")
    try:
        if args.propose_inventory is not None:
            inventory_document = propose_inventory(args.candidate, args.propose_inventory, args.probe_evidence)
            print(json.dumps(inventory_document, ensure_ascii=False, indent=2))
            return
        runtime, content, count, distribution = describe(args.candidate)
        if args.propose_lock:
            print(json.dumps(distribution, ensure_ascii=False, indent=2))
            return
        def git(*command):
            return subprocess.check_output(["git", "-C", str(ROOT), *command], text=True).strip()
        if git("rev-parse", "HEAD") != args.revision or git("status", "--porcelain", "--untracked-files=all"):
            raise ValueError("Publish from a clean checkout of the exact pack commit")
        if distribution != load(ROOT / "tools/distribution-lock.json"):
            raise ValueError("Candidate differs from the approved distribution lock")
        with tempfile.TemporaryDirectory(dir=args.out.parent) as temporary:
            staged = pathlib.Path(temporary) / "dist"
            export(args, staged, runtime, content, count, distribution)
            if args.verify_only:
                if not args.out.is_dir() or output_files(staged) != output_files(args.out):
                    raise ValueError("Output does not match the verified release")
            else:
                if args.out.exists():
                    raise ValueError("Use a fresh output directory")
                shutil.move(staged, args.out)
    except (KeyError, OSError, ValueError, subprocess.CalledProcessError) as error:
        parser.exit(1, f"Release manifest: {error}\n")


if __name__ == "__main__":
    main()
