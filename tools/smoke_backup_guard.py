"""Exercise first-open world protection using the current client's disposable QA save.

This does not prove migration of historical mod blocks. Run after the full native
smoke test, with the same client, controller, prepared resources and QA directory.
"""

import argparse
import hashlib
import json
import os
from pathlib import Path
import stat
import sys
from zipfile import ZipFile

from native_build import load, module_at, sha, write
from native_qa_environment import direct_client_environment, environment_policy


WORLD = "RustModsSmoke"
MARKER = ".lanminecraft-rust-mods-orbital-v1"
DESIGNATOR = "orbital:laser_designator"


def ordinary(path, *, directory=None):
    metadata = path.lstat()
    if stat.S_ISLNK(metadata.st_mode) or getattr(metadata, "st_file_attributes", 0) & 0x400:
        raise ValueError(f"The QA path is linked or reparsed: {path}")
    if directory is True and not stat.S_ISDIR(metadata.st_mode):
        raise ValueError(f"Expected an ordinary QA directory: {path}")
    if directory is False and not stat.S_ISREG(metadata.st_mode):
        raise ValueError(f"Expected an ordinary QA file: {path}")
    return metadata


def validate_run(qa, owner):
    # Do not resolve a link before checking it. Only a direct child QA folder
    # of an owned, registered project run is eligible for this destructive probe.
    qa = Path(os.path.abspath(qa))
    run = qa.parent
    if run.parent.name != "agent-runs" or run.parent.parent.name != "artifacts":
        raise ValueError("Use the existing QA directory directly under a registered agent run")
    workspace = run.parent.parent.parent
    for path in (workspace, workspace / "artifacts", run.parent, run, qa):
        ordinary(path, directory=True)
    ordinary(run / "RUN.json", directory=False)
    registration = load(run / "RUN.json")
    if not owner or registration.get("owner") != owner or registration.get("status") != "active":
        raise ValueError("The backup probe requires the matching owner of an active RUN.json")
    return qa, workspace


def tree_files(root, *, skip_world_metadata=False):
    """Return hashes only after refusing every reparse point in this tree."""
    ordinary(root, directory=True)
    result = {}
    for folder, directories, files in os.walk(root, topdown=True, followlinks=False):
        directories.sort()
        # Check before os.walk can descend, including Windows junctions that
        # are directories rather than symbolic links in older Python versions.
        for name in directories:
            ordinary(Path(folder) / name, directory=True)
        for name in sorted(files):
            path = Path(folder) / name
            metadata = ordinary(path, directory=False)
            relative = path.relative_to(root).as_posix()
            if skip_world_metadata and (name == "session.lock" or relative == MARKER):
                continue
            result[relative] = {"sizeBytes": metadata.st_size, "sha256": sha(path)}
    return result


def inventory_hash(files):
    return hashlib.sha256(json.dumps(files, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()


def settings_files(game):
    result = {}
    for name in ("options.txt", "horizon-graphics.txt", "horizon-mods.txt",
                 "horizon-mods/.horizon-mod-data/orbital/settings"):
        path = game / name
        if path.exists():
            current = game
            for component in Path(name).parts[:-1]:
                current /= component
                ordinary(current, directory=True)
            ordinary(path, directory=False)
            result[name] = {"sizeBytes": path.stat().st_size, "sha256": sha(path)}
    config = game / "config"
    if config.exists():
        result.update({f"config/{name}": details for name, details in tree_files(config).items()})
    if "options.txt" not in result:
        raise ValueError("The full-smoke game's personal options are missing")
    return result


def zip_matches(backup, expected):
    ordinary(backup, directory=False)
    with ZipFile(backup) as archive:
        entries = {f"{WORLD}/{name}": details for name, details in expected.items()}
        names = archive.namelist()
        if len(names) != len(set(names)) or sorted(names) != sorted(entries):
            raise ValueError("The native backup does not contain exactly the snapshotted world files")
        for name, details in entries.items():
            info = archive.getinfo(name)
            if info.file_size != details["sizeBytes"]:
                raise ValueError(f"World backup size mismatch: {name}")
            with archive.open(info) as stream:
                if hashlib.file_digest(stream, "sha256").hexdigest() != details["sha256"]:
                    raise ValueError(f"World backup bytes changed: {name}")


def marker_ready(marker):
    return marker.is_file() and ordinary(marker, directory=False).st_size == 0


def verify(run_client, client, controller, resources, qa, *, run_owner, first_index=7):
    qa, _ = validate_run(qa, run_owner)
    if first_index < 1:
        raise ValueError("Use positive unused control/log indices")
    for index in (first_index, first_index + 1):
        if any((qa / name).exists() for name in (f"control-{index}.json", f"client-{index}.log")):
            raise ValueError("Use two unused control/log indices for the backup probe")
    ordinary(client, directory=False)
    ordinary(controller, directory=False)
    ordinary(resources, directory=True)
    ordinary(qa / "smoke.json", directory=False)
    previous = load(qa / "smoke.json")
    client_hash = sha(client)
    candidate = client.parent.parent
    environment = environment_policy(candidate)
    environment_applications = []
    if (previous.get("clientSha256") != client_hash or previous.get("mode") != "gameplay"
            or previous.get("gameplay") != "PASS" or DESIGNATOR not in previous.get("persistedModItems", [])):
        raise ValueError("Use the completed full-smoke QA save from this exact Orbital-equipped client")
    game = qa / "game"
    saves = game / "saves"
    world = saves / WORLD
    for path in (game, saves, world):
        ordinary(path, directory=True)
    marker = world / MARKER
    if not marker_ready(marker):
        raise ValueError("The current-client QA save must have its ordinary zero-byte guard marker")
    expected = tree_files(world, skip_world_metadata=True)
    if (expected.get("level.dat", {}).get("sizeBytes", 0) == 0
            or not any(name.endswith(".mca") for name in expected)):
        raise ValueError("The QA save has no saved level and region data")
    settings = settings_files(game)
    backups = game / "backups"
    if backups.exists():
        ordinary(backups, directory=True)
        existing = tree_files(backups)
    else:
        existing = {}
    removed_marker = False

    def run(index, action):
        with direct_client_environment(candidate) as direct:
            environment_applications.append({"index": index, **direct})
            return run_client(client, controller, resources, qa, index, action)

    try:
        # This is the only file removed by the probe, from this exact QA save.
        # Its absence selects the existing-world branch in the genuine client.
        marker.unlink()
        removed_marker = True

        def first(control):
            control("open", WORLD)
            control("wait-for", "loaded", "--timeout", "90")
            if not marker_ready(marker):
                raise ValueError("First open did not recreate the zero-byte world protection marker")
            after = tree_files(backups)
            added = after.keys() - existing.keys()
            if (len(added) != 1 or not all(after.get(name) == details for name, details in existing.items())):
                raise ValueError("First open must add exactly one backup and retain all earlier backups")
            name = added.pop()
            if not name.endswith(".zip") or "/" in name:
                raise ValueError("The native backup is not a ZIP directly in the game's backups directory")
            zip_matches(backups / name, expected)
            return name, after[name]

        backup_name, details = run(first_index, first)
        expected_backups = {**existing, backup_name: details}

        def second(control):
            control("open", WORLD)
            control("wait-for", "loaded", "--timeout", "90")
            if not marker_ready(marker) or tree_files(backups) != expected_backups:
                raise ValueError("Reopening the guarded world altered or duplicated a backup")

        run(first_index + 1, second)
        if tree_files(backups) != expected_backups or settings_files(game) != settings:
            raise ValueError("The backup probe changed earlier backups or the QA game's personal settings")
        return {
            "schemaVersion": 1, "status": "PASS", "clientSha256": client_hash,
            **environment, "environmentApplications": environment_applications,
            "methodology": "current-client QA save", "world": WORLD,
            "firstOpenBackup": "PASS", "reopenWithoutDuplicate": "PASS",
            "sourceFilesSha256": inventory_hash(expected), "sourceFilesVerified": len(expected),
            "sourceSizeBytes": sum(item["sizeBytes"] for item in expected.values()),
            "excludedSourceFiles": ["session.lock", MARKER],
            "backup": {"name": backup_name, **details}, "backupMembersVerified": len(expected),
            "existingBackupFilesPreserved": len(existing), "newBackupsAfterReopen": 1,
            "markerRecreatedZeroBytes": True, "personalSettingsPreserved": len(settings),
            "personalSettingsSha256": inventory_hash(settings),
            "historicalRemovedRegistryMigration": "NOT RUN",
            "scope": "First-open guard and exact backup bytes; historical removed blocks were not tested.",
        }
    finally:
        # run_client stops only its own process in its finally block before this
        # restoration runs. Keep the existing QA fixture usable after a failure.
        if removed_marker and not marker.exists():
            validate_run(qa, run_owner)
            for path in (game, saves, world):
                ordinary(path, directory=True)
            with marker.open("xb"):
                pass


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("base-repo", "candidate", "controller", "resources", "qa"):
        parser.add_argument("--" + name, required=True, type=lambda value: Path(value).absolute())
    parser.add_argument("--run-owner", required=True, help="exact owner recorded in the active RUN.json")
    parser.add_argument("--first-index", type=int, default=7, help="two unused run_client indices, default 7 and 8")
    args = parser.parse_args()
    report_path = args.qa / "backup-guard.json"
    if report_path.exists():
        parser.error("The backup guard already has a report in this QA directory")
    client = args.candidate / "native/horizon-client.exe"
    receipt = load(args.candidate / "build-receipt.json")
    if sha(client) != receipt.get("clientSha256"):
        parser.error("The exact candidate client differs from its build receipt")
    sys.path.insert(0, str(args.base_repo / "tools"))
    try:
        base = module_at(args.base_repo / "tools/smoke_runtime.py")
    finally:
        sys.path.pop(0)
    report = verify(base.run_client, client, args.controller, args.resources, args.qa,
                    run_owner=args.run_owner, first_index=args.first_index)
    write(report_path, report)
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
