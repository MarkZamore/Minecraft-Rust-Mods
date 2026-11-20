"""Replay the exact Rust Mods package inputs for a build-pinned SDK sidecar."""

import argparse
import json
from pathlib import Path, PurePosixPath
import re
import tomllib

from inspect_mods import selected_ids
from modern_native_stage import stage_runtime_packages
from native_build import DECISIONS, INPUT_LOCK, ROOT, build_recipe_sha, load, sha, tree_hash, unpack_inputs
from negotiated_native_mods import stage_packages
from shared_sdk_pack import REVISION, ordinary
from mixins_tool import REVISION as MIXINS


def selection(input_lock, selection_path, inspection_path):
    ordinary(selection_path)
    ordinary(inspection_path)
    raw = input_lock.read_bytes()
    registry = json.loads(raw)
    selected = selected_ids(selection_path, raw)
    rows = {row["id"]: row for row in registry["projects"]}
    decisions = load(DECISIONS)
    inspection = load(inspection_path)
    if any(document["horizonRevision"] != REVISION for document in (registry, decisions, inspection)):
        raise ValueError("The author archive, conflict and inspection locks must all target the same Horizon")
    excluded = {(component["id"], component["version"]) for decision in decisions["decisions"]
                for component in [decision["excluded"], *decision.get("excludedContent", [])]}
    accepted = {(row["id"], row["version"], row["sha256"]) for row in inspection["verified"]}
    result = [rows[id] for id in sorted(selected)]
    for row in result:
        key = (row["id"], row["version"])
        if (row["eligibility"] not in {"candidate", "compile-probe"}
                or key in excluded or (*key, row["file"]["sha256"]) not in accepted):
            raise ValueError(f"Unreviewed or excluded pinned mod: {key[0]}")
    return result


def local_file(root, relative, *, directory=False):
    source = PurePosixPath(relative)
    if source.is_absolute() or source.as_posix() != relative or ".." in source.parts or "\\" in relative:
        raise ValueError(f"Invalid pack-relative path: {relative}")
    file = root.joinpath(*source.parts)
    ordinary(file, directory=directory)
    return file


def cargo_sources(directory, mod, version=None):
    path = local_file(directory, "Cargo.toml")
    manifest = tomllib.loads(path.read_text(encoding="utf-8"))
    package = manifest["package"]
    source = manifest.get("lib", {}).get("path", "src/lib.rs")
    local_file(directory, source)
    if (package.get("build") not in (None, False) or (directory / "build.rs").exists()
            or set(manifest) - {"package", "lib", "features", "dependencies", "dev-dependencies", "workspace"}
            or set(manifest.get("features", {})) - {"default"}
            or manifest.get("features", {}).get("default", [])
            or version is not None and package["version"] != version):
        raise ValueError(f"{mod}: unsupported linker inputs changed, review the exact Cargo source")
    name = package["name"].replace("-", "_")
    if not re.fullmatch(r"[a-z][a-z0-9_]*", name) or package["edition"] not in {"2021", "2024"}:
        raise ValueError(f"{mod}: unsupported Cargo crate name or edition")
    return {"crate": name, "edition": package["edition"], "entry": source,
            "dependencies": {name.replace("-", "_"): value.get("package", name).replace("-", "_")
                             if isinstance(value, dict) else name.replace("-", "_")
                             for name, value in sorted(manifest.get("dependencies", {}).items())}}


def inspect_mixins(work, rows):
    bundles = work / "mixins-input"
    packages = work / "compiled-native"
    mods = []
    names = set()
    for row in rows:
        id, version = row["id"], row["version"]
        if row["kind"] != "native-mod":
            mods.append({"id": id, "version": version, "content": f"content/{id}.hmod"})
            continue
        bundle = local_file(bundles, id, directory=True)
        document = tomllib.loads(local_file(bundle, "horizon-mixin.toml").read_text(encoding="utf-8"))
        author = document["mod"]
        if (author["id"] != id or str(author["version"]) != version
                or document["compatibility"]["horizon_revision"] != REVISION
                or set(document) - {"mod", "compatibility", "injection", "module", "crate"}):
            raise ValueError(f"{id}: unexpected staged mixin declaration change")
        injections = document.get("injection", [])
        modules = document.get("module", [])
        for item in injections:
            local_file(bundle, item["helper"])
            if "target" not in item or "hook" in item:
                raise ValueError(f"{id}: an injection is absent from the verified target graph")
        for item in modules:
            local_file(bundle, item["source"])
        support = []
        for item in document.get("crate", []):
            directory = local_file(bundle, item["path"], directory=True)
            data = cargo_sources(directory, id)
            support.append({"id": item["id"], "source": directory.relative_to(work).as_posix(), **data})
        package = packages / id
        registrar = None
        if package.exists():
            metadata = tomllib.loads(local_file(package, "horizon-mod.toml").read_text(encoding="utf-8"))
            if (metadata["id"] != id or str(metadata["version"]) != version
                    or metadata["native"]["horizon"] != REVISION or metadata["native"]["mixins"] != MIXINS):
                raise ValueError(f"{id}: registrar does not match the verified mod or its source generation")
            registrar = {"source": package.relative_to(work).as_posix(), "side": metadata["side"],
                         **cargo_sources(package, id, version)}
        for library in support + ([registrar] if registrar else []):
            if library["crate"] in names:
                raise ValueError(f"{id}: repeated Rust library name {library['crate']}")
            names.add(library["crate"])
        mod = {"id": id, "version": version, "source": bundle.relative_to(work).as_posix(),
               "legacy": row.get("nativePackage") is None, "header": author,
               "injections": injections, "modules": modules, "supportCrates": support,
               "registrar": registrar}
        if (work / "content" / (id + ".hmod")).is_file():
            mod["content"] = f"content/{id}.hmod"
        mods.append(mod)
    return mods


def prepare(work, archives, rows):
    work.mkdir()
    unpack_inputs(rows, archives, work, REVISION)
    stage_packages(work, rows, MIXINS)
    stage_runtime_packages(work, rows)
    mod_inputs = inspect_mixins(work, rows)
    return {"schemaVersion": 1, "horizon": REVISION, "mixins": MIXINS,
            "buildRecipeSha256": build_recipe_sha(), "inputLockSha256": sha(INPUT_LOCK),
            "conflictDecisionsSha256": sha(DECISIONS),
            "treeSha256": {name: tree_hash(work / name) for name in ("content", "compiled-native", "mixins-input")},
            "mods": mod_inputs}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("out", "archives", "inspection"):
        parser.add_argument(f"--{name}", required=True, type=Path)
    parser.add_argument("--selection", type=Path, default=ROOT / "tools/mod-selection.json")
    args = parser.parse_args()
    out = args.out.absolute()
    root = out.parent
    ordinary(root, directory=True)
    ordinary(args.archives, directory=True)
    if (out.exists() or out.is_symlink() or root.parent.name != "agent-runs"
            or root.parent.parent.name != "artifacts" or load(root / "RUN.json")["status"] != "active"):
        raise ValueError("Use a new direct output directory in an active registered run")
    rows = selection(INPUT_LOCK, args.selection, args.inspection)
    proof = prepare(out, args.archives, rows)
    record = out / "mods.json"
    record.write_text(json.dumps(proof, indent=2) + "\n", encoding="utf-8", newline="\n")
    print(record)


if __name__ == "__main__":
    main()
