"""Export neutral Rust Mods host seams for the reviewed shared Horizon SDK."""

import argparse
import importlib.util
import json
from pathlib import Path
import re
import tempfile
from types import SimpleNamespace

import jei_compat
from legacy_native_hooks import BRIDGE, REVISION, prepare_source as prepare_legacy, prepare_wheel, replace_once
from native_build import INPUT_LOCK, build_recipe_sha, load, sha, verify_base
from negotiated_native_mods import EXTRAS, GAME, SOURCE_SHA256, TRANSACTIONS, TRANSPORT, add_orbital_delivery
from multiplayer_warning import TITLE, TITLE_TESTS, SET_MODE_ACTION, SET_MODE_EDITION_ACTION
import multiplayer_warning
import orbital_compat
from orbital_compat.payload_ingress import SOURCE_SHA256 as PAYLOAD_INGRESS_SOURCES
import orbital_controls
import orbital_world_guard
from orbital_controls import SETTINGS
from orbital_world_guard import BACKUP_SOURCE, WORLD_SOURCE
import physics_title_compat
import server_seed_compat
from shared_sdk_client_lib import PATCH_EXPORTS as CLIENT_LIB_EXPORTS
from shared_sdk_game_exports import PATCH_EXPORTS as GAME_EXPORTS
from shared_sdk_render import PATCH_EXPORTS as RENDER_EXPORTS
import spider_compat
import tectonic_compat


PREPARED_SEAMS = frozenset("""
crates/horizon-client-render/src/camera.rs
crates/horizon-client-render/src/chat.rs
crates/horizon-client-render/src/gfx.rs
crates/horizon-client-render/src/gfx/native_material.rs
crates/horizon-client-render/src/options_ui.rs
crates/horizon-client-render/src/particles.rs
crates/horizon-client-render/src/render_slots.rs
crates/horizon-client-render/src/settings.rs
crates/horizon-client-render/src/streaming.rs
crates/horizon-client/src/game.rs
crates/horizon-client/src/game/host/mod.rs
crates/horizon-client/src/game/interface/chat.rs
crates/horizon-client/src/game/interface/creative_screen.rs
crates/horizon-client/src/game/interface/extras.rs
crates/horizon-client/src/game/interface/options.rs
crates/horizon-client/src/game/interface/pad_menu.rs
crates/horizon-client/src/game/interface/play_input.rs
crates/horizon-client/src/game/interface/play_keys.rs
crates/horizon-client/src/game/interface/screen_backgrounds.rs
crates/horizon-client/src/game/modding/native_panel.rs
crates/horizon-client/src/game/modding/native_panel_recipes.rs
crates/horizon-client/src/game/native_hooks/base.rs
crates/horizon-client/src/game/native_hooks/lanminecraft/legacy.rs
crates/horizon-client/src/game/native_hooks/mod.rs
crates/horizon-client/src/game/native_hooks/shared.rs
crates/horizon-client/src/game/network/lan_actions/mod.rs
crates/horizon-client/src/game/network/lan_quiet.rs
crates/horizon-client/src/game/network/remote/mod.rs
crates/horizon-client/src/game/world/levels.rs
crates/horizon-client/src/game/world/world_loading.rs
crates/horizon-client/src/motion.rs
crates/horizon-client/src/title.rs
crates/horizon-client/src/vanillagen.rs
crates/horizon-mod-package/src/mod_list.rs
crates/horizon-official-servers/src/lib.rs
""".split()) | frozenset(PAYLOAD_INGRESS_SOURCES)

# Only the installer-verified, pack-local native record can enable old hook
# settings. Profiles with a shared client, but no native mods, keep base behavior.
PROFILE = r'''
pub fn legacy_mod_active(id: &str) -> bool {
    #[derive(serde::Deserialize)]
    struct Profile {
        #[serde(rename = "legacyModIds", default)]
        ids: Vec<String>,
    }
    static IDS: OnceLock<Vec<String>> = OnceLock::new();
    IDS.get_or_init(|| {
        let Some(root) = pack_native_root().ok().flatten() else { return Vec::new(); };
        let path = root.join("native-mods.json");
        let Ok(metadata) = fs::symlink_metadata(&path) else { return Vec::new(); };
        if !metadata.is_file() || metadata.file_type().is_symlink() || metadata.len() > 1024 * 1024 {
            return Vec::new();
        }
        #[cfg(windows)]
        {
            use std::os::windows::fs::MetadataExt;
            if metadata.file_attributes() & 0x400 != 0 { return Vec::new(); }
        }
        let Ok(bytes) = fs::read(path) else { return Vec::new(); };
        serde_json::from_slice::<Profile>(&bytes).map_or_else(|_| Vec::new(), |profile| profile.ids)
    }).iter().any(|owner| owner == id)
}
'''
PROFILE_CHECK = "horizon_mod_package::runtime::native_paths::legacy_mod_active"
DELIVERY = EXTRAS + "/legacy_payload_commit.rs"
NATIVE_HOOKS = GAME + "/native_hooks/mod.rs"
PATCH_EXPORTS = {
    "crates/horizon-world/src/worldgen/block_feature.rs": (
        "b04711c08b73355b240e3d96bc73f4c3d4b547ea872d5409325815ca9e82545d",
        (("    pub(crate) fn tree_blocks(&self) -> Result<Arc<super::tree_blocks::TreeBlocks>, DecodeError> {",
          "    pub fn tree_blocks(&self) -> Result<Arc<super::tree_blocks::TreeBlocks>, DecodeError> {"),),
    ),
}


def ordinary(path, *, directory=False):
    expected = path.is_dir() if directory else path.is_file()
    if not expected or path.is_symlink() or path.is_junction() or path.resolve() != path.absolute():
        raise ValueError(f"Expected an ordinary {'directory' if directory else 'file'}: {path}")


def edition_recipe(edition):
    path = edition / "integration/shared-native/apply.py"
    ordinary(path)
    spec = importlib.util.spec_from_file_location("shared_edition", path)
    recipe = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(recipe)
    if recipe.REVISION != REVISION:
        raise ValueError("Shared Edition and Mods target different Horizon revisions")
    return recipe


def replay_host(base, work):
    """Reproduce pre-mixin seams on a sparse copy, without building either pack."""
    for name in sorted(PREPARED_SEAMS | {TITLE_TESTS, BACKUP_SOURCE} | jei_compat.F00125F_HOST_SHA256.keys()):
        original = base / name
        if name == BRIDGE:
            continue  # The reviewed legacy bridge recipe creates it.
        ordinary(original)
        output = work / name
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_bytes(original.read_bytes())
    (work / ".horizon-revision").write_text(REVISION + "\n", encoding="ascii", newline="\n")
    server_seed_compat.prepare_source(work)
    multiplayer_warning.prepare_source(work)
    # Membership will come from the selected pack's verified native record.
    prepare_legacy(work, [])
    physics_title_compat.prepare_source(work)
    spider_compat.prepare_source(work)
    tectonic_compat.prepare_source(work)
    jei_compat.prepare_source(work)
    orbital_compat.prepare_source(work, revision=REVISION)
    orbital_compat.prepare_latest_source(work)
    orbital_controls.apply(work)
    orbital_world_guard.apply(work)
    prepare_wheel(work)


def gated_settings(text):
    text, found = re.subn(r"(?m)^const LEGACY_SETTINGS_OWNERS: &\[&str\] = &\[[^\r\n]*\];\n", "", text)
    if found != 1:
        raise ValueError("The legacy settings owners anchor changed")
    return replace_once(replace_once(text, "LEGACY_SETTINGS_OWNERS.binary_search(&mod_id).is_err()",
                                     f"!{PROFILE_CHECK}(mod_id)"),
                        "LEGACY_SETTINGS_OWNERS.binary_search(&id).is_ok()", f"{PROFILE_CHECK}(id)")


def neutral_delivery(game, base, version):
    originals = {}
    for name in (TRANSPORT, TRANSACTIONS):
        original = base / name
        ordinary(original)
        if sha(original) != SOURCE_SHA256[name]:
            raise ValueError(f"Orbital's negotiated commit seam changed: {name}")
        originals[name] = original.read_text(encoding="utf-8")
    # Reuse the existing exact commit seam, including its live player and
    # epoch checks. Leave the registrar entirely to the selected pack DLL.
    old_marker = "pub(crate) mod lanminecraft_builtin_mods;"
    changed = {GAME + ".rs": replace_once(game, "mod native_hooks;", "mod native_hooks;\n" + old_marker)}
    add_orbital_delivery(changed, originals, version)
    marker = old_marker + '\n#[path = "lanminecraft_ext/orbital_payloads.rs"]\nmod orbital_payloads;\n'
    changed[GAME + ".rs"] = replace_once(changed[GAME + ".rs"], marker, "")
    payload_path = EXTRAS + "/orbital_payloads.rs"
    consts = re.findall(r"(?m)^pub const (?:WORK|ANSWER|VISUAL|MAX_BYTES): [^\n]+$", changed.pop(payload_path))
    if len(consts) != 4:
        raise ValueError("The negotiated Orbital payload constants changed")
    constants = "\n".join(line.removeprefix("pub ") for line in consts)
    delivery = replace_once(changed[DELIVERY], "use super::{Game, orbital_payloads};", "use super::Game;")
    changed[DELIVERY] = delivery.replace("orbital_payloads::", "") + "\n" + constants + "\n"
    return changed


def patch_exports(base, prepared):
    changed = {}
    for exports in (PATCH_EXPORTS, CLIENT_LIB_EXPORTS, RENDER_EXPORTS, GAME_EXPORTS):
        for name, (expected, declarations) in exports.items():
            file = base / name
            ordinary(file)
            if sha(file) != expected:
                raise ValueError(f"Shared SDK patch dependency changed: {name}")
            text = changed.get(name, prepared.get(name))
            if text is None:
                text = file.read_text(encoding="utf-8")
            for anchor, public in declarations:
                text = replace_once(text, anchor, public)
            changed[name] = text
    return changed


def plan_seams(base, prepared, edition, version):
    ordinary(base, directory=True)
    ordinary(prepared, directory=True)
    if (prepared / ".horizon-revision").read_text(encoding="ascii").strip() != REVISION:
        raise ValueError("Unexpected prepared Mods source revision")
    changed = {}
    for name in sorted(PREPARED_SEAMS):
        file = prepared / name
        ordinary(file)
        changed[name] = file.read_text(encoding="utf-8")
    changed[BRIDGE] = gated_settings(changed[BRIDGE])
    changed[BRIDGE] = replace_once(changed[BRIDGE], "pub(in crate::game) struct NativeMod {", "pub struct NativeMod {")
    changed[NATIVE_HOOKS] = replace_once(changed[NATIVE_HOOKS], "pub(in crate::game) use legacy::NativeMod;",
                                        "pub use legacy::NativeMod;")
    # These old play settings and the one-time Orbital world migration are
    # observable. Reuse the exact implementation only in selecting profiles.
    changed[SETTINGS] = replace_once(changed[SETTINGS], "    for (prefix, id, action, _) in PACK_MOD_BINDINGS {",
        "    for (prefix, id, action, _) in PACK_MOD_BINDINGS {\n"
        f"        if !{PROFILE_CHECK}(id) {{ continue; }}")
    changed[SETTINGS] = replace_once(changed[SETTINGS], "    for (prefix, id, action, fallback) in PACK_MOD_BINDINGS {",
        "    for (prefix, id, action, fallback) in PACK_MOD_BINDINGS {\n"
        f"        if !{PROFILE_CHECK}(id) {{ continue; }}")
    changed[WORLD_SOURCE] = replace_once(changed[WORLD_SOURCE],
        "        if options.execution_role\n            != execution_role::ExecutionRole::DedicatedServer",
        f'        if {PROFILE_CHECK}("orbital")\n'
        "            && options.execution_role != execution_role::ExecutionRole::DedicatedServer")
    # Both packs already have the same verified menu behavior. Preserve the
    # Edition source when the two recipes differ only by its comment.
    changed[TITLE] = replace_once(changed[TITLE], SET_MODE_ACTION, SET_MODE_EDITION_ACTION.decode("utf-8"))
    changed.update(neutral_delivery(changed[GAME + ".rs"], base, version))
    changed.update(patch_exports(base, changed))
    recipe = edition_recipe(edition)
    originals = {name: (base / name).read_bytes() for name in recipe.SOURCE_SHA256}
    scoped = recipe.changes(originals)
    changed[recipe.PATHS] = scoped[recipe.PATHS].decode("utf-8") + PROFILE
    files = {}
    for name, text in changed.items():
        original = base / name
        if name in scoped:
            before = scoped[name]
        elif original.exists():
            ordinary(original)
            before = original.read_bytes()
        else:
            before = None
        after = text.encode("utf-8")
        if before != after:
            files[name] = (None if before is None else recipe.sha(before), after)
    return files


def write_seams(out, files, recipe_sha):
    out.mkdir()
    records = {}
    for name, (before, data) in sorted(files.items()):
        destination = out / "files" / name
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(data)
        records[name] = {"beforeSha256": before, "sha256": sha(destination)}
    record = {"schemaVersion": 1, "horizon": REVISION, "modsRecipeSha256": recipe_sha, "files": records}
    (out / "seams.json").write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8", newline="\n")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("base-pack", "base-source", "source-archive", "steam-transport", "out"):
        parser.add_argument(f"--{name}", required=True, type=Path)
    args = parser.parse_args()
    args.out = args.out.absolute()
    root = args.out.parent
    ordinary(root, directory=True)
    if (args.out.exists() or args.out.is_symlink() or root.parent.name != "agent-runs"
            or root.parent.parent.name != "artifacts" or load(root / "RUN.json")["status"] != "active"):
        raise ValueError("Use a new direct output directory in an active registered run")
    for name in ("base_pack", "base_source", "steam_transport"):
        ordinary(getattr(args, name), directory=True)
    ordinary(args.source_archive)
    verify_base(SimpleNamespace(**vars(args)), REVISION)
    orbital = next(row for row in load(INPUT_LOCK)["projects"] if row["id"] == "orbital")
    with tempfile.TemporaryDirectory(prefix="seam-proof-", dir=root) as scratch:
        work = Path(scratch)
        replay_host(args.base_source, work)
        seams = plan_seams(args.base_source, work, args.base_pack, orbital["version"])
    write_seams(args.out, seams, build_recipe_sha())
    print(args.out / "seams.json")


if __name__ == "__main__":
    main()
