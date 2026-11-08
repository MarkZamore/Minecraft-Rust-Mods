"""Restore a pinned Edition candidate from verified public delivery archives."""

import argparse
import hashlib
import json
from pathlib import Path, PurePosixPath, PureWindowsPath
import shutil
import stat
import sys
import tempfile
import time
import zipfile

from inspect_mods import read_https
from native_build import ROOT, load, module_at, sha


def restore(args):
    pin = load(ROOT / "tools/base-lock.json")
    base = args.base_repo.resolve(strict=True)
    if sha(base / "tools/distribution-lock.json") != pin["distributionLockSha256"]:
        raise ValueError("Edition source distribution lock differs from the accepted pin")
    registered = next((p for p in args.out.parents if (p / "RUN.json").is_file()
                       and p.parent.name == "agent-runs" and p.parent.parent.name == "artifacts"), None)
    if args.out.exists() or registered is None:
        raise ValueError("Use a new destination inside a registered agent run")
    if not args.out.resolve().is_relative_to(registered.resolve()):
        raise ValueError("Recovery output must stay inside the workspace agent runs")
    for parent in args.out.parents:
        if parent.exists() and (parent.is_symlink() or parent.is_junction()):
            raise ValueError("Linked recovery output parent")
    source = "https://github.com/MarkZamore/Minecraft-Rust-Edition/releases/download/pack-latest/"
    raw = read_https(source + "pack-manifest.json?readback=" + str(time.time_ns()), 1024 * 1024)
    if hashlib.sha256(raw).hexdigest() != args.manifest_sha256:
        raise ValueError("Public Edition manifest differs from the accepted readback")
    manifest = json.loads(raw)
    if manifest["revision"] != pin["revision"]:
        raise ValueError("Public manifest revision differs from the pinned Edition")
    lock = load(base / "tools/distribution-lock.json")
    files = {entry["path"]: {key: entry[key] for key in ("sizeBytes", "sha256")}
             for entry in manifest["files"]}
    if files != lock["files"]:
        raise ValueError("Public managed files differ from the source distribution lock")
    with tempfile.TemporaryDirectory(dir=args.out.parent) as directory:
        staging = Path(directory)
        archives, candidate = staging / "assets", staging / "candidate"
        archives.mkdir()
        candidate.mkdir()
        verified, covered = [], set()
        for asset in [*manifest["assets"], manifest["library"]]:
            name = asset["name"]
            if PurePosixPath(name).name != name or PureWindowsPath(name).name != name:
                raise ValueError("Unsafe public asset name")
            path = archives / name
            data = read_https(source + name + "?readback=" + str(time.time_ns()), 96 * 1024 * 1024)
            if len(data) != asset["sizeBytes"] or hashlib.sha256(data).hexdigest() != asset["sha256"]:
                raise ValueError("Public archive SHA/size mismatch: " + name)
            path.write_bytes(data)
            verified.append({key: asset[key] for key in ("name", "sizeBytes", "sha256")})
            if "covers" not in asset:
                continue
            expected = set(asset["covers"])
            if covered.intersection(expected) or not expected.issubset(files):
                raise ValueError("Overlapping or unknown archive coverage")
            with zipfile.ZipFile(path) as archive:
                members = archive.infolist()
                names = [entry.filename for entry in members]
                if set(names) != expected or len(names) != len({n.casefold() for n in names}):
                    raise ValueError("Archive members differ from the manifest")
                for entry in members:
                    relative = PurePosixPath(entry.filename)
                    if (relative.is_absolute() or ".." in relative.parts or "\\" in entry.filename
                            or relative.as_posix() != entry.filename
                            or any(PureWindowsPath(p).is_reserved() or p.endswith((".", " "))
                                   or ":" in p for p in relative.parts)
                            or stat.S_ISLNK(entry.external_attr >> 16)):
                        raise ValueError("Unsafe public archive member")
                    details = files[entry.filename]
                    if entry.file_size != details["sizeBytes"]:
                        raise ValueError("Unexpected member size")
                    output = candidate.joinpath(*relative.parts)
                    output.parent.mkdir(parents=True, exist_ok=True)
                    with archive.open(entry) as incoming, output.open("xb") as outgoing:
                        shutil.copyfileobj(incoming, outgoing)
                    if sha(output) != details["sha256"]:
                        raise ValueError("Extracted managed file SHA mismatch")
            covered.update(expected)
        if covered != set(files):
            raise ValueError("Public archives do not cover the complete managed runtime")
        sys.path.insert(0, str(base / "tools"))
        try:
            generator = module_at(base / "tools/generate_manifest.py")
            from stage_runtime import LIBRARY_SOURCES
            shutil.copyfile(base / "tools/overlay-receipt.json", candidate / "overlay-receipt.json")
            (candidate / "library").mkdir()
            for name in LIBRARY_SOURCES:
                shutil.copyfile(base / "library" / name, candidate / "library" / name)
            _, _, actual = generator.describe_candidate(candidate)
            if actual != lock:
                raise ValueError("Restored candidate differs from the complete published base lock")
        finally:
            sys.path.pop(0)
        (staging / "pack-manifest.json").write_bytes(raw)
        proof = {"schemaVersion": 1, "status": "PASS", "revision": pin["revision"],
                 "manifestSha256": args.manifest_sha256, "managedFiles": len(files),
                 "assets": verified, "distributionLockSha256": pin["distributionLockSha256"]}
        (staging / "public-readback.json").write_text(json.dumps(proof, indent=2) + "\n", encoding="utf-8")
        shutil.move(staging, args.out)
    print(json.dumps(proof, indent=2))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-repo", required=True, type=Path)
    parser.add_argument("--out", required=True, type=lambda value: Path(value).resolve())
    parser.add_argument("--manifest-sha256", required=True)
    args = parser.parse_args()
    restore(args)


if __name__ == "__main__":
    main()
