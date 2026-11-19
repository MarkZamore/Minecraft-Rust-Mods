"""Stage reviewed native .hmod sources in the composed Horizon build."""

from hashlib import sha256
import io
import json
from pathlib import Path
import shutil
import tomllib
import zipfile

from modern_native_compat import AUTHOR_HORIZON, HORIZON, MIXINS, adapt_author_native


FORMAT = "source-hmod"
MANIFEST = "horizon-mod.toml"
TYPED_FOLDER = "typed"
AUTHORS = {
    "skins_3d": ("mod-skins_3d", "2.0.0", 16430, "02c7daaab772cd9e3d6a6d247b65afba379ccf0b3dc7f36f11fbe18984426a91"),
    "spider_man": ("spider_man", "0.2.5", 59041, "45840d869f7e264ad38774f85643529ded71153f30b50835b24a7aa23bd054ea"),
}
LIBRARY_FILES = {"skins_3d": ("src/lib.rs", "src/voxels.rs"), "spider_man": ("src/lib.rs",)}
DEPENDENTS = {"skins_3d": ("horizon-client",), "spider_man": ("horizon-client", "horizon-client-render")}
SDK_DEPENDENCY = 'path = ".horizon/sdk/api/crates/horizon-mod-api"'
WORKSPACE_DEPENDENCY = 'path = "../../../crates/horizon-mod-api"'


def validate_pin(row: dict) -> tuple[str, str]:
    mod_id = row["id"]
    if row.get("nativePackage") != FORMAT or row["kind"] != "native-mod" or mod_id not in AUTHORS:
        raise ValueError(f"Unreviewed modern native input: {mod_id}")
    package, version, size, digest = AUTHORS[mod_id]
    file = row["file"]
    if (row["version"] != version or row["game"] != "26.3" or row["side"] != "client"
            or row["api"] != f"native-1/{AUTHOR_HORIZON}/{MIXINS}"
            or (file["size"], file["sha256"]) != (size, digest)):
        raise ValueError(f"Modern native source pin changed: {mod_id}")
    return package, version


def toml_value(value) -> str:
    if isinstance(value, bool):
        return str(value).lower()
    if isinstance(value, int):
        return str(value)
    if isinstance(value, str):
        return json.dumps(value, ensure_ascii=False)
    if isinstance(value, list):
        return "[" + ", ".join(toml_value(item) for item in value) + "]"
    if isinstance(value, dict):
        return "{ " + ", ".join(f"{key} = {toml_value(item)}" for key, item in value.items()) + " }"
    raise ValueError(f"Unsupported legacy manifest value: {type(value).__name__}")


def mixin_manifest(document: dict) -> bytes:
    native = document["native"]
    if set(native) != {"game", "horizon", "mixins", "module", "mixin"}:
        raise ValueError("Unreviewed native mixin manifest fields")
    header = (
        "[mod]\n"
        f"id = {toml_value(document['id'])}\n"
        f"version = {toml_value(document['version'])}\n\n"
        "[compatibility]\n"
        f"horizon_revision = {toml_value(native['horizon'])}\n"
    )
    sections = []
    for source, kind in (("module", "module"), ("mixin", "injection")):
        for entry in native[source]:
            sections.append(f"\n[[{kind}]]\n" + "".join(
                f"{key} = {toml_value(value)}\n" for key, value in entry.items()))
    return (header + "".join(sections)).encode("utf-8")


def write_file(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("xb") as stream:
        stream.write(data)


def stage_bundle(row: dict, archive_path: Path, bundle: Path, revision: str) -> None:
    """Create the legacy SDK input and its verified author registrar package."""
    from inspect_mods import validate_members

    mod_id = row["id"]
    package, version = validate_pin(row)
    if archive_path.is_symlink() or archive_path.is_junction() or row["file"]["size"] != archive_path.stat().st_size:
        raise ValueError(f"Modern native archive is missing or linked: {mod_id}")
    raw = archive_path.read_bytes()
    if sha256(raw).hexdigest() != row["file"]["sha256"]:
        raise ValueError(f"Modern native archive changed after inspection: {mod_id}")
    with zipfile.ZipFile(io.BytesIO(raw)) as archive:
        validate_members(archive)
        members = {entry.filename: archive.read(entry) for entry in archive.infolist() if not entry.is_dir()}
    document = tomllib.loads(members[MANIFEST].decode("utf-8"))
    if (document["id"], document["version"], document["side"], document["api"]) != (mod_id, version, "client", "0.2"):
        raise ValueError(f"Modern native manifest identity changed: {mod_id}")
    adapted, members = adapt_author_native(document, members, revision)
    native = adapted["native"]
    cargo = members["Cargo.toml"]
    parsed = tomllib.loads(cargo.decode("utf-8"))
    expected_dependency = {"horizon-mod-api": {"path": ".horizon/sdk/api/crates/horizon-mod-api"}}
    if (parsed["package"]["name"], parsed["package"]["version"]) != (package, version) or parsed["dependencies"] != expected_dependency:
        raise ValueError(f"Modern native Cargo package changed: {mod_id}")

    game_sources = {entry["source"] for entry in native["module"]} | {entry["helper"] for entry in native["mixin"]}
    bundle.mkdir()
    write_file(bundle / "horizon-mixin.toml", mixin_manifest(adapted))
    for name in sorted(game_sources):
        write_file(bundle / name, members[name])
    typed = bundle / TYPED_FOLDER
    for name in LIBRARY_FILES[mod_id]:
        write_file(typed / name, members[name])
    write_file(typed / "Cargo.toml", cargo)
    prelude = members[MANIFEST].decode("utf-8").split("\n[native]\n", 1)[0].rstrip()
    manifest = (prelude + '\n\n[native]\ngame = "26.3"\n'
                f'horizon = "{revision}"\nmixins = "{MIXINS}"\n')
    write_file(typed / MANIFEST, manifest.encode("utf-8"))
    for name, data in members.items():
        if name.startswith(("assets/", "data/")) or name in ("icon.png", "LICENSE"):
            write_file(typed / name, data)


def selected_sources(rows: list[dict]) -> list[dict]:
    return [row for row in rows if row.get("nativePackage") == FORMAT]


def rewrite_manifest(path: Path, anchor: str, addition: str) -> None:
    original = path.read_text(encoding="utf-8")
    if original.count(anchor) != 1:
        raise ValueError(f"Modern native stage expected one Cargo anchor in {path}")
    updated = original.replace(anchor, anchor + addition)
    tomllib.loads(updated)
    path.write_text(updated, encoding="utf-8", newline="\n")


def install_compiled_crates(stage: Path, bundles: Path, rows: list[dict]) -> None:
    """Register only the pinned typed crates in the staged workspace dependency graph."""
    if (stage / ".horizon-revision").read_text(encoding="ascii").strip() != HORIZON:
        raise ValueError("Modern native crates require the exact f00125f stage")
    registrations = []
    dependencies: dict[str, list[str]] = {}
    for row in selected_sources(rows):
        mod_id = row["id"]
        package, _ = validate_pin(row)
        source = bundles / mod_id / TYPED_FOLDER
        relative = f"native-crates/{mod_id}/{TYPED_FOLDER}"
        target = stage / relative
        cargo = (source / "Cargo.toml").read_text(encoding="utf-8")
        if cargo.count(SDK_DEPENDENCY) != 1:
            raise ValueError(f"Modern native SDK dependency changed: {mod_id}")
        cargo = cargo.replace(SDK_DEPENDENCY, WORKSPACE_DEPENDENCY)
        if mod_id == "spider_man":
            workspace = "\n# Its own workspace: the repository's workspaces above it are the development setup.\n[workspace]\n"
            if not cargo.endswith(workspace):
                raise ValueError("Spider-Man's standalone Cargo workspace changed")
            cargo = cargo.removesuffix(workspace)
        tomllib.loads(cargo)
        write_file(target / "Cargo.toml", cargo.encode("utf-8"))
        for name in LIBRARY_FILES[mod_id]:
            write_file(target / name, (source / name).read_bytes())
        registrations.append(f'    "{relative}",\n')
        for dependent in DEPENDENTS[mod_id]:
            dependencies.setdefault(dependent, []).append(f'{package} = {{ path = "../../{relative}" }}\n')
    if registrations:
        rewrite_manifest(stage / "Cargo.toml", "\nmembers = [\n", "".join(registrations))
    for dependent, additions in dependencies.items():
        rewrite_manifest(stage / "crates" / dependent / "Cargo.toml", "\n[dependencies]\n", "".join(additions))


def stage_runtime_packages(work: Path, rows: list[dict]) -> None:
    """Ship identities, assets and registrar sources beside the verified client."""
    for row in selected_sources(rows):
        validate_pin(row)
        mod_id = row["id"]
        shutil.copytree(work / "mixins-input" / mod_id / TYPED_FOLDER, work / "compiled-native" / mod_id)
