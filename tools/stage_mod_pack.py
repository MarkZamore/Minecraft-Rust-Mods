"""Stage a native Minecraft Rust Mods candidate from a pinned base release and mod build."""

import argparse
import pathlib
import shutil
import subprocess
import sys
import tempfile

from native_build import DECISIONS, INPUT_LOCK, RECIPE, ROOT, load, module_at, sha, write


CONTROLS_PRESET = "launcher/controls-preset.txt"
OPTIONS_TEMPLATE = "configureddefaults/options.txt"


def presentation():
    return {"name": load(ROOT / "library/content.json")["name"],
            "instanceConfig": load(ROOT / "launcher/instance-config.json")}


def checked_base(repo, candidate):
    pin = load(ROOT / "tools/base-lock.json")
    command = ["git", "-C", str(repo)]
    inputs = ("portable-pack.json", "library", "integration/steam", "tools/distribution-lock.json",
              "tools/generate_manifest.py", "tools/stage_runtime.py", "tools/smoke_runtime.py", "tools/upstream-lock.json",
              "tools/steam-lock.json", "tools/overlay-receipt.json", "tools/upstream", CONTROLS_PRESET, OPTIONS_TEMPLATE)
    changes = subprocess.check_output([*command, "status", "--porcelain", "--untracked-files=all", "--", *inputs], text=True)
    if (pin.get("schemaVersion") != 1 or changes or
            subprocess.run([*command, "merge-base", "--is-ancestor", pin["revision"], "HEAD"]).returncode or
            subprocess.run([*command, "diff", "--quiet", pin["revision"], "HEAD", "--", *inputs]).returncode):
        raise ValueError("Use a clean Minecraft Rust Edition checkout with the release inputs pinned in base-lock.json")
    lock = repo / "tools/distribution-lock.json"
    if sha(lock) != pin["distributionLockSha256"]:
        raise ValueError("Base distribution lock differs from the accepted build")
    sys.path.insert(0, str(repo / "tools"))
    try:
        generator = module_at(repo / "tools/generate_manifest.py")
        _, _, distribution = generator.describe_candidate(candidate)
        if distribution != load(lock):
            raise ValueError("Base runtime differs from the published base distribution lock")
    finally:
        sys.path.pop(0)
    return pin, generator, distribution["files"]


def approved_build(work):
    report_path = work / "build.json"
    approval = load(ROOT / "tools/build-lock.json")
    build = load(report_path)
    if approval.get("schemaVersion") != 1 or approval["receiptSha256"] != sha(report_path):
        raise ValueError("Build receipt differs from the explicitly approved build lock")
    lock = load(INPUT_LOCK)
    if (build.get("schemaVersion") != 1 or build["inputLockSha256"] != sha(INPUT_LOCK)
            or build["conflictDecisionsSha256"] != sha(DECISIONS)
            or build["buildRecipeSha256"] != sha(RECIPE)
            or build["compileCheck"] is not True
            or build["horizonRevision"] != lock["horizonRevision"]):
        raise ValueError("Build was made from another input lock or Horizon revision")
    selection = load(ROOT / "tools/mod-selection.json")
    if (selection.get("schemaVersion") != 1 or selection["inputLockSha256"] != sha(INPUT_LOCK) or
            build["ids"] != selection["ids"]):
        raise ValueError("Build differs from the explicitly selected mod IDs")
    rows = {row["id"]: row for row in lock["projects"]}
    selected = [rows[mod] for mod in build["ids"]]
    client = work / "runtime/horizon-client.exe"
    if not client.is_file() or client.is_symlink() or sha(client) != build["clientSha256"]:
        raise ValueError("Combined Horizon client differs from the accepted build")
    return build, selected, client


def copy_licenses(selected, cache, target):
    licenses = load(ROOT / "tools/mod-license-lock.json")
    if licenses.get("schemaVersion") != 1 or licenses["inputLockSha256"] != sha(INPUT_LOCK):
        raise ValueError("Licensing evidence belongs to another registry snapshot")
    for mod in selected:
        entries = licenses["projects"][mod["id"]]
        if not entries:
            raise ValueError(f"Missing reviewed redistribution notice for {mod['id']}")
        directory = target / mod["id"]
        directory.mkdir()
        for name, expected in entries.items():
            if pathlib.PurePath(name).name != name or name in (".", ".."):
                raise ValueError(f"Unsafe notice name for {mod['id']}")
            src = cache / mod["id"] / name
            if src.is_symlink() or sha(src) != expected:
                raise ValueError(f"License bytes differ from the reviewed notice for {mod['id']}")
            shutil.copyfile(src, directory / name)


def stage(args):
    pin, generator, files = checked_base(args.base_repo, args.base_candidate)
    build, selected, client = approved_build(args.build_work)
    if build["steamOverlayRecipeSha256"] != load(args.base_repo / "tools/overlay-receipt.json")["recipeSha256"]:
        raise ValueError("Mods and the pinned Edition runtime must use the same verified Steam overlay")
    generator.verify_pe(client)
    steam_overlay = module_at(args.base_repo / "integration/steam/apply_horizon_overlay.py")
    steam_overlay.verify_pe_icon(client, (args.base_repo / "integration/steam/rust-logo/rust-edition.ico").read_bytes())
    descriptor = load(ROOT / "portable-pack.json")
    base_descriptor = load(args.base_candidate / "portable-pack.json")
    runtime = descriptor["nativeRuntime"]
    if (descriptor["minecraftVersion"] != base_descriptor["minecraftVersion"] or
            runtime["version"] == base_descriptor["nativeRuntime"]["version"] or
            runtime["executable"] != base_descriptor["nativeRuntime"]["executable"]):
        raise ValueError("Mods need the same runtime contract and their own Steam compatibility revision")
    if args.candidate.exists():
        raise ValueError("Use a fresh candidate destination")
    wrapper = load(ROOT / "tools/bootstrap-lock.json")
    if wrapper.get("schemaVersion") != 1:
        raise ValueError("Invalid isolated bootstrap lock")
    generator.verify_pe(args.bootstrap)
    bootstrap_file = {"sizeBytes": wrapper["sizeBytes"], "sha256": wrapper["sha256"]}
    generator.verify_file(args.bootstrap, bootstrap_file)

    with tempfile.TemporaryDirectory(dir=args.candidate.parent) as temporary:
        staged = pathlib.Path(temporary) / "candidate"
        native = staged / "native"
        native.mkdir(parents=True)
        excluded = {"portable-pack.json", "native/horizon-client.exe", "native/presentation.json",
                    runtime["executable"], CONTROLS_PRESET, OPTIONS_TEMPLATE}
        for name in sorted(set(files) - excluded):
            src = args.base_candidate / name
            target = staged / name
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(src, target)
            generator.verify_file(target, files[name])
        shutil.copyfile(client, native / "horizon-client.exe")
        if sha(native / "horizon-client.exe") != build["clientSha256"]:
            raise ValueError("Combined client changed while staging")
        shutil.copyfile(ROOT / "portable-pack.json", staged / "portable-pack.json")
        shutil.copyfile(args.bootstrap, staged / runtime["executable"])
        generator.verify_file(staged / runtime["executable"], bootstrap_file)
        write(native / "presentation.json", presentation())
        for relative in (CONTROLS_PRESET, OPTIONS_TEMPLATE):
            target = staged / relative
            target.parent.mkdir(exist_ok=True)
            shutil.copyfile(ROOT / relative, target)
        write(native / "native-mods.json", {
            "base": pin["revision"], "horizon": build["horizonRevision"], "horizon_mixins": build["mixinsRevision"],
            "mods": [{"id": row["id"], "version": row["version"], "sha256": row["file"]["sha256"]}
                     for row in selected if row["kind"] == "native-mod"],
        })
        for row in selected:
            if row["kind"] != "mod":
                continue
            name = row["id"] + ".hmod"
            content = native / "horizon-mods"
            content.mkdir(exist_ok=True)
            shutil.copyfile(args.build_work / "content" / name, content / name)
            if sha(content / name) != row["file"]["sha256"]:
                raise ValueError(f"Packaged content mod changed: {row['id']}")
        licenses = native / "third_party/mods"
        licenses.mkdir(parents=True, exist_ok=True)
        copy_licenses(selected, args.license_cache, licenses)
        library = staged / "library"
        library.mkdir()
        for name in ("content.json", "mod-pages.json", "loaded-mods.json", "preview.jpg"):
            source = ROOT / "library" / name
            if source.is_file():
                shutil.copyfile(source, library / name)
        write(staged / "build-receipt.json", build)
        staged.rename(args.candidate)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for field in ("base-repo", "base-candidate", "build-work", "license-cache", "bootstrap", "candidate"):
        parser.add_argument("--" + field, required=True, type=lambda value: pathlib.Path(value).resolve())
    args = parser.parse_args()
    try:
        stage(args)
    except (KeyError, OSError, ValueError, subprocess.CalledProcessError) as error:
        parser.exit(1, f"Pack staging: {error}\n")


if __name__ == "__main__":
    main()
