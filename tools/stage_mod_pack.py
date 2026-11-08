"""Stage a native Minecraft Rust Mods candidate from a pinned base release and mod build."""

import argparse
import json
import pathlib
import re
import shutil
import subprocess
import sys
import tempfile
import time
import unicodedata

from inspect_mods import content_file
from native_build import DECISIONS, INPUT_LOCK, ROOT, build_recipe_sha, load, module_at, sha, write


CONTROLS_PRESET = "launcher/controls-preset.txt"
OPTIONS_TEMPLATE = "configureddefaults/options.txt"
ENVIRONMENT_DEFAULTS = "launcher/environment-defaults.json"
REQUIRED_ENVIRONMENT_DEFAULTS = {"DISABLE_VULKAN_OW_OVERLAY_LAYER": "1"}


def validate_environment_defaults(value):
    if not isinstance(value, dict) or len(value) > 16:
        raise ValueError("Presentation environment defaults must be an object of at most 16 entries")
    names, total = set(), 0
    for key, text in value.items():
        if (not isinstance(key, str) or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,63}", key) or
                key.casefold() in names or not isinstance(text, str) or
                any(unicodedata.category(char) == "Cc" for char in text)):
            raise ValueError("Invalid or case-duplicate presentation environment default")
        encoded = text.encode("utf-8")
        if len(encoded) > 1024:
            raise ValueError("A presentation environment default exceeds 1024 UTF-8 bytes")
        names.add(key.casefold())
        total += len(key.encode("ascii")) + len(encoded)
    if total > 8192:
        raise ValueError("Presentation environment defaults exceed 8192 bytes in total")
    return value


def environment_defaults():
    def pairs(rows):
        result, names = {}, set()
        for key, value in rows:
            if key.casefold() in names:
                raise ValueError("Case-duplicate presentation environment default in source JSON")
            names.add(key.casefold())
            result[key] = value
        return result

    path = ROOT / ENVIRONMENT_DEFAULTS
    if path.is_symlink() or path.is_junction():
        raise ValueError("Use an ordinary pack-owned environment defaults source file")
    value = validate_environment_defaults(json.loads(path.read_bytes(), object_pairs_hook=pairs))
    if value != REQUIRED_ENVIRONMENT_DEFAULTS:
        raise ValueError("The mandatory pack Vulkan overlay opt-out policy is missing or changed")
    return value


def verify_environment_evidence(report, presentation_sha, applications):
    defaults = environment_defaults()

    def source_matches(value):
        return (isinstance(value, dict) and value.get("environmentPolicy") == "pack-presentation-defaults-v1" and
                value.get("environmentDefaults") == defaults and value.get("presentationSha256") == presentation_sha)

    entries = report.get("environmentApplications", []) if isinstance(report, dict) else []
    if (not source_matches(report) or not isinstance(entries, list) or len(entries) < len(applications) or
            any(not isinstance(entry, dict) for entry in entries) or
            [entry.get("environmentApplication") for entry in entries[:len(applications)]] != list(applications)):
        raise ValueError("Run exact-client QA from the staged mandatory presentation environment policy")
    for entry in entries:
        if not isinstance(entry, dict) or not source_matches(entry):
            raise ValueError("A QA launch has another presentation environment policy")
        if entry.get("environmentApplication") == "direct-client":
            if (entry.get("effectiveEnvironmentDefaults") != defaults or
                    entry.get("environmentDefaultOrigins") != {name: "default" for name in defaults}):
                raise ValueError("Final clean QA must apply pack defaults rather than inherit a diagnostic environment mask")
        elif entry.get("environmentApplication") == "bootstrap":
            if "effectiveEnvironmentDefaults" in entry or "environmentDefaultOrigins" in entry:
                raise ValueError("Bootstrap QA must not claim an unobserved child environment or pre-apply its defaults")
        else:
            raise ValueError("Unknown QA environment application path")


def presentation():
    return {"name": load(ROOT / "library/content.json")["name"],
            "instanceConfig": load(ROOT / "launcher/instance-config.json"),
            "environmentDefaults": environment_defaults()}


def checked_base(repo, candidate):
    pin = load(ROOT / "tools/base-lock.json")
    command = ["git", "-C", str(repo)]
    inputs = ("portable-pack.json", "library", "integration/steam", "tools/distribution-lock.json",
              "tools/generate_manifest.py", "tools/stage_runtime.py", "tools/smoke_runtime.py", "tools/upstream-lock.json",
              "tools/steam-lock.json", "tools/overlay-receipt.json", "tools/upstream", CONTROLS_PRESET, OPTIONS_TEMPLATE,
              "launcher/standalone", "launcher/environment-defaults.json", "tools/bootstrap-lock.json",
              "tools/bootstrap_contract.py")
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
            or build["buildRecipeSha256"] != build_recipe_sha()
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
    from native_notice_acceptance import verify_bootstrap_source
    verify_bootstrap_source(args.base_repo, args.bootstrap)
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
            expected = content_file(row)
            if expected is None:
                continue
            name = row["id"] + ".hmod"
            content = native / "horizon-mods"
            content.mkdir(exist_ok=True)
            shutil.copyfile(args.build_work / "content" / name, content / name)
            generator.verify_file(content / name, {"sizeBytes": expected["size"], "sha256": expected["sha256"]})
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
        # Windows scanners can briefly hold a newly written PE file. Keep the
        # candidate atomic while allowing that transient handle to close.
        for attempt in range(21):
            try:
                staged.rename(args.candidate)
                break
            except PermissionError as error:
                if getattr(error, "winerror", None) not in (5, 32) or attempt == 20:
                    raise
                time.sleep(0.25)


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
