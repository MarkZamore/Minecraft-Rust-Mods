"""Read back public Mods archives before promoting the exact prepared manifest.

Use a fresh --out path. Downloads stay in memory; only the final PASS proof is
written. The manifest phase reuses a matching assets proof without redownloading.
"""

import argparse
from datetime import datetime, timezone
import hashlib
import io
import json
from pathlib import Path, PurePosixPath, PureWindowsPath
import re
import stat
import time
from urllib.parse import quote
from urllib.request import Request, urlopen
import zipfile


REPOSITORY = "MarkZamore/Minecraft-Rust-Mods"
TAG = "pack-latest"
BASE = f"https://github.com/{REPOSITORY}/releases/download/{TAG}/"


def require(condition, message):
    if not condition:
        raise ValueError(message)


def digest(data):
    return hashlib.sha256(data).hexdigest()


def safe_path(name):
    require(isinstance(name, str) and bool(name) and "\\" not in name, "Invalid ZIP path")
    path = PurePosixPath(name)
    require(not path.is_absolute() and path.as_posix() == name and ".." not in path.parts,
            "Non-canonical ZIP path: " + name)
    require(all(part not in (".", "..") and not part.endswith((".", " "))
                and ":" not in part and not PureWindowsPath(part).is_reserved()
                and not any(ord(c) < 32 for c in part) for part in path.parts),
            "Unsafe Windows ZIP path: " + name)
    return path


def identity(asset):
    name = asset["name"]
    require(safe_path(name).name == name, "Invalid asset name")
    require(type(asset["sizeBytes"]) is int and 0 < asset["sizeBytes"] <= 256 * 1024 * 1024,
            "Invalid/beyond-budget asset size")
    require(re.fullmatch(r"[0-9a-f]{64}", asset["sha256"]) is not None, "Invalid asset SHA-256")
    return {key: asset[key] for key in ("name", "sizeBytes", "sha256")}


def fetch(name, size):
    request = Request(BASE + quote(name, safe="") + "?readback=" + str(time.time_ns()),
                      headers={"User-Agent": "LANMinecraft-Public-Delivery-QA",
                               "Cache-Control": "no-cache, no-store", "Pragma": "no-cache"})
    with urlopen(request, timeout=60) as response:
        raw = response.read(size + 1)
    require(len(raw) == size, "Public size mismatch: " + name)
    return raw


def members(archive):
    entries = archive.infolist()
    names = [entry.filename for entry in entries]
    require(len(names) == len({name.casefold() for name in names}), "Duplicate ZIP member")
    for entry in entries:
        safe_path(entry.filename)
        require(not entry.is_dir() and not stat.S_ISLNK(entry.external_attr >> 16),
                "Directory or linked ZIP member")
    return {entry.filename: entry for entry in entries}


def verify(args):
    require(not args.out.exists(), "Use a fresh proof output path")
    raw = (args.dist / "pack-manifest.json").read_bytes()
    manifest = json.loads(raw)
    require(manifest.get("packId") == "minecraft-10" and manifest.get("modCount") == 37,
            "Expected the selected 37-mod Minecraft Rust Mods manifest")
    require(re.fullmatch(r"[0-9a-f]{40}", manifest["revision"]) is not None, "Invalid revision")
    files = {row["path"]: {key: row[key] for key in ("sizeBytes", "sha256")}
             for row in manifest["files"]}
    require(len(files) == len(manifest["files"]) == len({p.casefold() for p in files}),
            "Duplicate managed path")
    for name in files:
        safe_path(name)
    assets = [identity(asset) for asset in [*manifest["assets"], manifest["library"]]]
    require(len(assets) == 4 and len({a["name"].casefold() for a in assets}) == 4,
            "Expected four distinct delivery archives")
    local = {"sha256": digest(raw), "sizeBytes": len(raw)}
    proof = {"schemaVersion": 1, "status": "PASS", "phase": args.phase,
             "repository": REPOSITORY, "tag": TAG, "revision": manifest["revision"],
             "modCount": manifest["modCount"], "localManifest": local, "assets": assets,
             "managedFiles": len(files), "checkedAtUtc": datetime.now(timezone.utc).isoformat()}
    if args.phase == "assets":
        covered, maps = set(), []
        for asset, expected in zip([*manifest["assets"], manifest["library"]], assets, strict=True):
            path = args.dist / "assets" / expected["name"]
            with path.open("rb") as source:
                local_sha = hashlib.file_digest(source, "sha256").hexdigest()
            require(path.stat().st_size == expected["sizeBytes"] and local_sha == expected["sha256"],
                    "Prepared archive changed: " + expected["name"])
            data = fetch(expected["name"], expected["sizeBytes"])
            require(digest(data) == expected["sha256"], "Public archive SHA mismatch: " + expected["name"])
            with zipfile.ZipFile(io.BytesIO(data)) as archive:
                actual = members(archive)
                if "covers" in asset:
                    wanted = set(asset["covers"])
                    require(len(wanted) == len(asset["covers"]) and set(actual) == wanted
                            and wanted <= files.keys() and not covered.intersection(wanted),
                            "ZIP coverage differs, overlaps or names unknown files")
                    for name, entry in actual.items():
                        require(entry.file_size == files[name]["sizeBytes"], "ZIP member size mismatch: " + name)
                        with archive.open(entry) as source:
                            require(hashlib.file_digest(source, "sha256").hexdigest() == files[name]["sha256"],
                                    "ZIP member SHA mismatch: " + name)
                    covered.update(wanted)
                else:
                    with zipfile.ZipFile(path) as prepared:
                        require(set(members(prepared)) == set(actual), "Public library member map differs")
                maps.append({"name": expected["name"], "members": len(actual),
                             "managedMembersVerified": len(asset.get("covers", [])), "sha256Verified": True})
        require(covered == set(files), "Archives do not cover every managed file")
        proof.update(archiveMaps=maps, allManagedMemberHashesVerified=True,
                     archivesStoredOnDisk=False, coveredFiles=len(covered))
    else:
        require(args.assets_proof is not None, "Manifest verification requires --assets-proof")
        prior_raw = args.assets_proof.read_bytes()
        prior = json.loads(prior_raw)
        require(all(prior.get(key) == proof[key] for key in
                    ("schemaVersion", "repository", "tag", "revision", "modCount", "localManifest", "assets", "managedFiles"))
                and prior.get("status") == "PASS" and prior.get("phase") == "assets"
                and prior.get("allManagedMemberHashesVerified") is True and prior.get("coveredFiles") == len(files),
                "Assets proof does not bind this exact prepared delivery")
        public = fetch("pack-manifest.json", len(raw))
        require(public == raw and digest(public) == local["sha256"], "Public manifest bytes differ")
        parsed = json.loads(public)
        require(parsed["revision"] == manifest["revision"] and parsed["modCount"] == manifest["modCount"],
                "Public revision/count differ")
        proof.update(publicManifest={"url": BASE + "pack-manifest.json", **local},
                     assetsProofSha256=digest(prior_raw), archivesRedownloaded=False)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    with args.out.open("x", encoding="utf-8", newline="\n") as output:
        output.write(json.dumps(proof, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps({"status": "PASS", "phase": args.phase, "revision": manifest["revision"],
                      "proof": str(args.out)}, ensure_ascii=False))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dist", required=True, type=Path)
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--phase", required=True, choices=("assets", "manifest"))
    parser.add_argument("--assets-proof", type=Path)
    args = parser.parse_args()
    try:
        verify(args)
    except (OSError, ValueError, KeyError, TypeError, RuntimeError, zipfile.BadZipFile) as error:
        parser.exit(1, "Public delivery verification: " + str(error) + "\n")


if __name__ == "__main__":
    main()
