"""Port the reviewed author native source packages to Horizon f00125f."""

from copy import deepcopy
from hashlib import sha256
import tomllib


HORIZON = "f00125f61f794aaa3d4e950d301f353b35b6ee3d"
AUTHOR_HORIZON = "0b14f8acfd97fa2c750e42795249dcc981178ca4"
MIXINS = "e59da9094f0c2fdda6c627fe9eff22dec6a906f0"
MANIFESTS = {
    "skins_3d": ("2.0.0", "292e185f8a87a3fba99e0dd91670c2cb5fd78ed49e70a71494c20582b3cf69a7"),
    "spider_man": ("0.2.5", "8eafa3225a0872a432cec64840a66f608e9991945b09e30697a21b34999ac461"),
}
SKINS_GLUE = "src/glue.rs"
SKINS_GLUE_SHA256 = "b8ceb62dde4477e3652692581206f0645928d76a36793d3f319dad6aec6e16ca"
SKINS_CRATE = "src/lib.rs"
SKINS_CRATE_SHA256 = "1b23e16e31fc48b960f10d26ba2ae4671014846b61f177d290cba58a0833b71e"
SKINS_CHOICE_KEY = "reach_choice"
SKINS_FRAME = """pub(super) fn frame(game: &mut Game<'_>) {
    let reach = f64::from(mod_skins_3d::reach());"""
SKINS_MIGRATION = """fn carry_legacy_reach(game: &Game<'_>) {
    static MIGRATION: std::sync::Once = std::sync::Once::new();
    MIGRATION.call_once(|| {
        if game.setting("skins_3d", "reach_choice").is_some() {
            return;
        }
        // Keep the previous block count readable if the player returns to the old build.
        let directory = game.native_data_dir("skins_3d");
        let Some(old) = horizon_world::native_load_context::read_setting(&directory, "reach") else { return };
        let index = match old.trim() {
            "8" => "0",
            "16" => "1",
            "32" => "2",
            "64" => "3",
            _ => return,
        };
        game.set_setting("skins_3d", "reach_choice", index);
    });
}

pub(super) fn frame(game: &mut Game<'_>) {
    carry_legacy_reach(game);
    let reach = f64::from(mod_skins_3d::reach());"""
VIEW = "horizon_client_render::camera::view_matrix"


def adapt_author_native(document: dict, members: dict[str, bytes], revision: str) -> tuple[dict, dict[str, bytes]]:
    """Return compilation inputs, leaving the verified author archive and manifest bytes intact."""
    mod_id = document.get("id")
    reviewed = MANIFESTS.get(mod_id)
    original = members["horizon-mod.toml"]
    if (reviewed is None or revision != HORIZON
            or (document.get("version"), sha256(original).hexdigest()) != reviewed
            or tomllib.loads(original.decode("utf-8")) != document):
        raise ValueError(f"Unreviewed modern native package or Horizon revision: {mod_id}")

    patched = deepcopy(document)
    native = patched["native"]
    if native["horizon"] != AUTHOR_HORIZON or native["mixins"] != MIXINS:
        raise ValueError("The author package's original native toolchain changed")
    native["horizon"] = revision

    sources = dict(members)
    if mod_id == "skins_3d":
        glue = sources[SKINS_GLUE]
        text = glue.decode("utf-8")
        if sha256(glue).hexdigest() != SKINS_GLUE_SHA256 or text.count(SKINS_FRAME) != 1:
            raise ValueError("The reviewed 3D Skins registration/render seam changed")
        crate = sources[SKINS_CRATE]
        if sha256(crate).hexdigest() != SKINS_CRATE_SHA256 or crate.count(b'"reach"') != 2:
            raise ValueError("The reviewed 3D Skins typed choice changed")
        sources[SKINS_GLUE] = text.replace(SKINS_FRAME, SKINS_MIGRATION).encode("utf-8")
        sources[SKINS_CRATE] = crate.replace(b'"reach"', f'"{SKINS_CHOICE_KEY}"'.encode("ascii"))
    else:
        view = [mixin for mixin in native["mixin"] if mixin["id"] == "turned-view"]
        if len(view) != 1 or view[0]["target"] != VIEW:
            raise ValueError("The reviewed Spider-Man turned view hook changed")
        # The pack's guarded camera wrapper keeps Horizon's replaceable outer API intact.
        view[0]["target"] = VIEW + "_default"
    return patched, sources
