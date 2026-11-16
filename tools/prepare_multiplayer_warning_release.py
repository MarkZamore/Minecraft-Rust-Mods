"""Prepare the explicitly authorized file-only Rust Mods warning release."""

import argparse
import io
import json
from pathlib import Path
import struct
import subprocess
import zipfile

from generate_manifest import accepted_build, file_info, zip_payload
from multiplayer_warning import PACK_ACTION_SHA256
from native_build import DECISIONS, INPUT_LOCK, ROOT, TOOLS, build_recipe_sha, load, sha, write
from verify_public_delivery import digest, fetch, members, require


PREVIOUS = TOOLS / "qa/public-delivery-58cdea8.json"
CLIENT = "native/horizon-client.exe"
INVENTORY = ROOT / "library/loaded-mods.json"
LOCK = TOOLS / "distribution-lock.json"
BUILD_LOCK = TOOLS / "build-lock.json"
BUILD_RECORD = TOOLS / "qa/final-build.json"


def previous_manifest():
    expected = load(PREVIOUS)["publicManifest"]
    raw = fetch("pack-manifest.json", expected["sizeBytes"])
    require(digest(raw) == expected["sha256"], "The public baseline changed")
    return raw, json.loads(raw)


def files(manifest):
    records = manifest["files"]
    result = {row["path"]: {k: row[k] for k in ("sha256", "sizeBytes")} for row in records}
    require(len(result) == len(records), "Duplicate managed path")
    return result


def compiled_inputs(candidate):
    receipt = candidate / "build-receipt.json"
    build = load(receipt)
    executable = candidate / CLIENT
    with executable.open("rb") as stream:
        header = stream.read(64)
        require(len(header) == 64 and header[:2] == b"MZ", "Expected a Windows executable")
        stream.seek(struct.unpack_from("<I", header, 60)[0])
        require(stream.read(6) == b"PE\0\0\x64\x86", "Expected a Windows x64 executable")
    client = file_info(executable)
    require(build.get("compileCheck") is True and client["sha256"] == build["clientSha256"] and
            build["buildRecipeSha256"] == build_recipe_sha() and
            build.get("multiplayerMenuActionSha256") == PACK_ACTION_SHA256 and
            build["inputLockSha256"] == sha(INPUT_LOCK) and build["conflictDecisionsSha256"] == sha(DECISIONS),
            "Build does not attest the exact checked Windows client and required warning policy")
    return build, client, receipt


def replace_json(path, value):
    pending = path.with_suffix(".pending")
    write(pending, value)
    pending.replace(path)


def pin(candidate, manifest):
    build, client, receipt = compiled_inputs(candidate)
    old_build = load(BUILD_RECORD)
    require({k: v for k, v in build.items() if k not in
             {"clientSha256", "buildRecipeSha256", "multiplayerMenuActionSha256", "mixinsSha256"}} ==
            {k: v for k, v in old_build.items() if k not in
             {"clientSha256", "buildRecipeSha256", "mixinsSha256"}},
            "The runtime build inputs changed beyond the warning recipe")
    baseline = files(manifest)
    lock = load(LOCK)
    require(lock["files"] == baseline and lock["receiptSha256"] == sha(BUILD_RECORD) and
            load(BUILD_LOCK)["receiptSha256"] == sha(BUILD_RECORD) and
            lock["library"][INVENTORY.name] == file_info(INVENTORY) and
            client["sha256"] != baseline[CLIENT]["sha256"] == old_build["clientSha256"],
            "Expected the published runtime, inventory and build locks")
    inventory = load(INVENTORY)
    require(inventory["nativeFiles"][CLIENT] == old_build["clientSha256"], "Unexpected old client in inventory")
    inventory["nativeFiles"][CLIENT] = client["sha256"]
    replace_json(INVENTORY, inventory)
    lock["files"][CLIENT] = client
    lock["library"][INVENTORY.name] = file_info(INVENTORY)
    lock["receiptSha256"] = sha(receipt)
    replace_json(LOCK, lock)
    replace_json(BUILD_LOCK, {"schemaVersion": 1, "receiptSha256": sha(receipt)})
    BUILD_RECORD.write_bytes(receipt.read_bytes())


def checked_zip(asset, baseline):
    raw = fetch(asset["name"], asset["sizeBytes"])
    require(digest(raw) == asset["sha256"], "Public archive SHA-256 changed")
    with zipfile.ZipFile(io.BytesIO(raw)) as archive:
        paths = members(archive)
        if "covers" in asset:
            require(len(asset["covers"]) == len(paths) and set(asset["covers"]) == set(paths),
                    "Archive paths differ from its manifest")
        payload = {path: archive.read(path) for path in paths}
    if "covers" in asset:
        require(all(baseline[path] == {"sizeBytes": len(data), "sha256": digest(data)}
                    for path, data in payload.items()), "An existing managed file failed SHA-256 verification")
    return raw, payload


def build_release(candidate, out, raw, manifest):
    build, client, receipt = compiled_inputs(candidate)
    accepted_build(candidate)
    require(receipt.read_bytes() == BUILD_RECORD.read_bytes(), "The source build record differs")
    git = ["git", "-C", str(ROOT)]
    require(not subprocess.check_output([*git, "status", "--porcelain"]), "Commit release locks first")
    revision = subprocess.check_output([*git, "rev-parse", "HEAD"], text=True).strip()
    baseline = files(manifest)
    expected = {**baseline, CLIENT: client}
    lock = load(LOCK)
    require(lock["files"] == expected and lock["library"][INVENTORY.name] == file_info(INVENTORY),
            "The distribution lock modifies more than the checked native client")
    inventory = load(INVENTORY)
    require(inventory["nativeFiles"] == {p: v["sha256"] for p, v in expected.items() if p.startswith("native/")} and
            len(inventory["mods"]) == len(build["ids"]) == manifest["modCount"] == 37,
            "Recorded native files or pack composition mismatch")
    assets = out / "assets"
    assets.mkdir(parents=True)
    asset_list = []
    covered = set()
    for asset in manifest["assets"]:
        data, payload = checked_zip(asset, baseline)
        require(not covered.intersection(payload), "Public delivery contains overlapping archives")
        covered.update(payload)
        if CLIENT in payload:
            # Use the same deterministic ZIP assembly as the ordinary release.
            for relative, value in payload.items():
                if relative != CLIENT:
                    target = candidate / relative
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_bytes(value)
            asset = zip_payload(assets, candidate, asset["covers"], "minecraft-rust-mods-windows-x64")
        else:
            (assets / asset["name"]).write_bytes(data)
        asset_list.append(asset)
    require(covered == baseline.keys() and asset_list != manifest["assets"],
            "Expected complete coverage and a new Windows archive")
    _, library_payload = checked_zip(manifest["library"], baseline)
    require(set(library_payload) == {"library.json", "preview.jpg"}, "Unexpected library files")
    metadata = json.loads(library_payload["library.json"])
    original_content = load(ROOT / "library/content.json")
    require(metadata["revision"] == manifest["revision"] and
            all(metadata.get(key) == original_content.get(key)
                for key in ("id", "name", "relativePath", "summary", "description", "nativeProject")) and
            library_payload["preview.jpg"] == (ROOT / "library/preview.jpg").read_bytes(),
            "The library baseline differs")
    metadata["revision"] = revision
    library_payload["library.json"] = (json.dumps(metadata, ensure_ascii=False, indent=2) + "\n").encode()
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for name, data in sorted(library_payload.items()):
            entry = zipfile.ZipInfo(name, (1980, 1, 1, 0, 0, 0))
            entry.compress_type = zipfile.ZIP_DEFLATED
            archive.writestr(entry, data)
    data = buffer.getvalue()
    library = dict(manifest["library"], name=f"library-{digest(data)}.zip",
                   sizeBytes=len(data), sha256=digest(data), revision=revision)
    (assets / library["name"]).write_bytes(data)
    updated = dict(manifest, revision=revision, assets=asset_list, library=library,
                   files=[{"path": p, **v} for p, v in expected.items()])
    require(files(updated) == expected, "Unexpected release file delta")
    write(out / "pack-manifest.json", updated)
    (out / "previous-manifest.json").write_bytes(raw)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--candidate", type=Path, required=True)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--pin", action="store_true")
    mode.add_argument("--out", type=Path)
    args = parser.parse_args()
    raw, manifest = previous_manifest()
    if args.pin:
        pin(args.candidate, manifest)
    else:
        require(not args.out.exists(), "Use a fresh output directory")
        build_release(args.candidate, args.out, raw, manifest)


if __name__ == "__main__":
    main()
