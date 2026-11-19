"""Install the reviewed f00125f payload registration and commit seam in the composed source."""

from hashlib import sha256
from pathlib import Path
import json
import shutil
import tomllib
import zipfile

from inspect_mods import validate_members
from legacy_native_hooks import REVISION, replace_once


TOOLS = Path(__file__).resolve().parent
GAME = "crates/horizon-client/src/game"
EXTRAS = "crates/horizon-client/src/lanminecraft_ext"
ROOT_ENTRY = "crates/horizon-client/src/entry.rs"
HOST = "crates/horizon-game/src/lib.rs"
TRANSPORT = GAME + "/modding/payload_transport.rs"
TRANSACTIONS = GAME + "/runtime/transactions.rs"
SOURCE_SHA256 = {
    HOST: "c0a3271fec5b61dda8eda713484cba336ddda3cfd69804008fcbd752f7755ca9",
    TRANSPORT: "dd88a73e7f5bdb28142a479ce1a8671e2cc5c103fa1f37202951c7643ca77a2e",
    TRANSACTIONS: "8e61e8761e143eb3964576ff118c12a4bfc7ae2ca4a1a302fdab2085136ecf7a",
}
OWNERS = {
    "elytratrails": "official_elytratrails_net::ElytraHooks",
    "orbital": "orbital_payloads::OrbitalPayloads",
}


def install_source(stage: Path, selected: list[dict]) -> None:
    records = [(row["id"], row["version"]) for row in selected if row["id"] in OWNERS]
    if not records:
        return
    originals = {}
    for name, expected in SOURCE_SHA256.items():
        raw = (stage / name).read_bytes()
        if sha256(raw).hexdigest() != expected:
            raise ValueError(f"Negotiated payload source changed: {name}")
        originals[name] = raw.decode("utf-8")

    game = (stage / (GAME + ".rs")).read_text(encoding="utf-8")
    original = "mod native_hooks;"
    game = replace_once(game, original, original + '\n#[path = "lanminecraft_ext/builtin_mods.rs"]'
                        '\npub(crate) mod lanminecraft_builtin_mods;')
    entry = (stage / ROOT_ENTRY).read_text(encoding="utf-8")
    entry += """
pub fn lanminecraft_builtin_mods() -> (Vec<mod_api::ModEntry>, &'static [mod_api::__private::native::BuiltMod]) {
    crate::game::lanminecraft_builtin_mods::entries()
}
"""
    host = replace_once(originals[HOST], "    horizon_mod_host::main_with(Vec::new(), &[], &[], startup);",
        "    let (mods, built) = horizon_client::entry::lanminecraft_builtin_mods();\n"
        "    horizon_mod_host::main_with(mods, &[], built, startup);")
    declarations = "".join(
        f"        BuiltMod {{ id: {json.dumps(id)}, version: {json.dumps(version)}, side: mod_api::Side::Both }},\n"
        for id, version in records)
    hooks = "".join(
        f"        mod_api::ModEntry::new({json.dumps(id)}, Box::new(super::{OWNERS[id]})),\n"
        for id, _ in records)
    registry = f'''use mod_api::__private::native::BuiltMod;

pub(crate) fn entries() -> (Vec<mod_api::ModEntry>, &'static [BuiltMod]) {{
    const BUILT: &[BuiltMod] = &[
{declarations}    ];
    (vec![
{hooks}    ], BUILT)
}}
'''
    changed = {GAME + ".rs": game, ROOT_ENTRY: entry, HOST: host,
               EXTRAS + "/builtin_mods.rs": registry}
    if any(id == "orbital" for id, _ in records):
        add_orbital_delivery(changed, originals)
    for name, text in changed.items():
        path = stage / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8", newline="\n")


def add_orbital_delivery(changed, originals) -> None:
    game = GAME + ".rs"
    changed[game] = replace_once(changed[game], "pub(crate) mod lanminecraft_builtin_mods;",
        'pub(crate) mod lanminecraft_builtin_mods;\n#[path = "lanminecraft_ext/orbital_payloads.rs"]'
        '\nmod orbital_payloads;\n#[path = "lanminecraft_ext/legacy_payload_commit.rs"]\nmod legacy_payload_commit;')
    changed[EXTRAS + "/orbital_payloads.rs"] = (TOOLS / "orbital_compat/declared_payloads.rs").read_text(encoding="utf-8")
    changed[EXTRAS + "/legacy_payload_commit.rs"] = (TOOLS / "legacy_payload_commit.rs").read_text(encoding="utf-8")
    transport = replace_once(originals[TRANSPORT], "    fn queue_payload(", "    pub(in crate::game) fn queue_payload(")
    changed[TRANSPORT] = replace_once(transport,
        "                incoming.receiver.handle(bytes, &mut context)",
        '''                let legacy = super::legacy_payload_commit::Delivery::capture(incoming, &context, &bytes.0);
                let result = incoming.receiver.handle(bytes, &mut context);
                if result.is_ok() && context.disconnect.is_none() && context.failure.is_none()
                    && let Some(legacy) = legacy
                {
                    context.game.after_commit(super::transactions::After::LegacyPayload(legacy));
                }
                result''')
    transactions = replace_once(originals[TRANSACTIONS], "    Payload(super::payload_transport::Deferred),",
        "    Payload(super::payload_transport::Deferred),\n"
        "    LegacyPayload(super::legacy_payload_commit::Delivery),")
    changed[TRANSACTIONS] = replace_once(transactions, "            After::Payload(payload) => payload.publish(),",
        "            After::Payload(payload) => payload.publish(),\n"
        "            After::LegacyPayload(delivery) => delivery.dispatch(self),")


def stage_packages(work: Path, selected: list[dict], mixins_revision: str) -> None:
    from elytra_compat import package_source

    root = work / "compiled-native"
    root.mkdir()
    for row in selected:
        id = row["id"]
        if id not in OWNERS:
            continue
        path = root / id
        (path / "src").mkdir(parents=True)
        if id == "orbital":
            manifest = copy_orbital_assets(work / "content/orbital.hmod", path, row)
            code = (TOOLS / "orbital_compat/declared_payloads.rs").read_text(encoding="utf-8")
            code += "\npub fn hooks() -> Box<dyn mod_api::Hooks> { Box::new(OrbitalPayloads) }\n"
        else:
            manifest = "\n".join((
                f"id = {json.dumps(id)}", f"name = {json.dumps(row['name'])}",
                f"version = {json.dumps(row['version'])}", 'api = "0.2"', 'side = "both"',
                f"license = {json.dumps(row['license'])}", 'capabilities = ["registries"]',
            ))
            code = package_source(work / "mixins-input" / id)
        native = f'\n\n[native]\ngame = "26.3"\nhorizon = "{REVISION}"\nmixins = "{mixins_revision}"\n'
        (path / "horizon-mod.toml").write_text(manifest.rstrip() + native, encoding="utf-8", newline="\n")
        (path / "src/lib.rs").write_text(code, encoding="utf-8", newline="\n")
        (path / "Cargo.toml").write_text(f'''[package]
name = "{id}"
version = "{row['version']}"
edition = "2024"

[dependencies]
mod_api = {{ package = "horizon-mod-api", git = "https://github.com/horizon-rust/horizon", rev = "{REVISION}" }}
''', encoding="utf-8", newline="\n")


def copy_orbital_assets(companion: Path, destination: Path, row: dict) -> str:
    with zipfile.ZipFile(companion) as archive:
        validate_members(archive)
        manifest = archive.read("horizon-mod.toml").decode("utf-8")
        metadata = tomllib.loads(manifest)
        if metadata.get("id") != row["id"] or metadata.get("version") != row["version"] or metadata.get("side") != "both":
            raise ValueError("Orbital assets differ from the registered compiled mod identity")
        for entry in archive.infolist():
            if entry.is_dir() or not entry.filename.startswith(("assets/", "data/")):
                continue
            target = destination / entry.filename
            target.parent.mkdir(parents=True, exist_ok=True)
            with archive.open(entry) as source, target.open("xb") as output:
                shutil.copyfileobj(source, output)
    return manifest
