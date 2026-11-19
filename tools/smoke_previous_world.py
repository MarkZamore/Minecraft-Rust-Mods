"""Verify the Orbital update's first-open backup against a prior public smoke world."""

import hashlib
import time
from zipfile import ZipFile

from native_build import load, sha


WORLD = "RustModsBeforeOrbital"
MARKER = ".lanminecraft-rust-mods-orbital-v1"
PREVIOUS_RELEASE = "580f83cc07700d3e1cd38152bcdba99cd92d5488"
PREVIOUS_CLIENT = "bc6d6378636f5737b3d3efea5802ce78d22fe30a391563f2d4c1b4e459eec068"
BLOCKS = ["deepbore_quarry:quarry", "wayfarer_stones:wayfarer_stone",
          "wayfarer_stones:wayfarer_stone_top", "minecraft:gold_block"]


def world_files(world):
    result = {}
    for path in sorted(world.rglob("*")):
        if path.is_symlink() or path.is_junction():
            raise ValueError(f"The prior smoke world contains a link: {path}")
        if path.is_file() and path.name != "session.lock":
            result[path.relative_to(world).as_posix()] = {
                "sizeBytes": path.stat().st_size, "sha256": sha(path),
            }
    return result


def check_backup(backup, expected):
    with ZipFile(backup) as archive:
        entries = {f"{WORLD}/{name}": item for name, item in expected.items()}
        if sorted(archive.namelist()) != sorted(entries):
            raise ValueError("The backup does not contain exactly the prior world's files")
        for name, item in entries.items():
            with archive.open(name) as stream:
                if archive.getinfo(name).file_size != item["sizeBytes"] or hashlib.file_digest(
                    stream, "sha256"
                ).hexdigest() != item["sha256"]:
                    raise ValueError(f"The backup changed the prior world's {name}")


def wait_for_blocks(control, points, required, timeout=90):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        blocks = []
        for point in points:
            coordinates = tuple(map(str, point))
            observed = control("block", *coordinates, timeout=max(1, deadline - time.monotonic()))
            if observed["loaded"]:
                blocks.append(observed["block"])
                continue
            # An omitted all-air section can remain unloaded on the client
            # even after its column and the authoritative world are ready.
            check = control("command", f"execute if block {' '.join(coordinates)} minecraft:air",
                            timeout=max(1, deadline - time.monotonic()))
            blocks.append("minecraft:air" if "Test passed" in check["chat"] else None)
        if None not in blocks and all(expected is None or actual == expected
                                      for actual, expected in zip(blocks, required, strict=True)):
            return blocks
        time.sleep(0.25)
    raise ValueError(f"The prior world's block samples did not settle: required {required!r}")


def verify(run_client, client, controller, resources, qa, standalone, title):
    game = qa / "game"
    world = game / "saves" / WORLD
    backup_dir = game / "backups"
    marker = world / MARKER
    before = load(qa / "old-save-evidence.json")
    expected = before["oldFiles"]
    points = before["points"]
    if (before["oldManifestRevision"] != PREVIOUS_RELEASE or before["oldClient"]["sha256"] != PREVIOUS_CLIENT
            or before["world"] != WORLD or before["oldSaveReopened"] != "PASS" or before["blocks"] != BLOCKS
            or len(points) != len(BLOCKS) or "level.dat" not in expected or not any("/region/" in p for p in expected)
            or marker.exists() or any(backup_dir.glob("*.zip")) or world_files(world) != expected):
        raise ValueError("Use the unchanged prior-release technical world with a verified pre-update inventory")

    def first(control):
        if marker.exists() or any(backup_dir.glob("*.zip")):
            raise ValueError("The prior world was backed up before it was first opened")
        control("open", WORLD)
        control("wait-for", "loaded", "--timeout", "90")
        blocks = wait_for_blocks(control, points, [None] * (len(BLOCKS) - 1) + BLOCKS[-1:])
        backups = list(backup_dir.glob("*.zip"))
        if len(backups) != 1 or not marker.is_file():
            raise ValueError("First open must create one backup and its migration marker")
        backup = backups[0]
        check_backup(backup, expected)
        control("save")
        return blocks, backup, sha(backup)

    after, backup, digest = run_client(client, controller, resources, qa, 5, first, standalone, title)

    def second(control):
        control("open", WORLD)
        control("wait-for", "loaded", "--timeout", "90")
        wait_for_blocks(control, points, after)
        if list(backup_dir.glob("*.zip")) != [backup] or sha(backup) != digest:
            raise ValueError("Reopening the migrated world changed or duplicated its backup")

    run_client(client, controller, resources, qa, 6, second, standalone, title)
    return {"clientSha256": sha(client), "oldManifestRevision": PREVIOUS_RELEASE, "oldClientSha256": PREVIOUS_CLIENT,
            "oldBlocks": BLOCKS, "newBlocks": after, "points": points,
            "backupMembersVerified": len(expected), "backupSha256": digest,
            "backupSizeBytes": backup.stat().st_size, "backupsAfterReopen": 1}
