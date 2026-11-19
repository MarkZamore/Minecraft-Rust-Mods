"""Keep the pack's shared legacy mixin lifecycle on the f00125f client."""

from hashlib import sha256
from pathlib import Path
import re
import tomllib

from direct_hook_relocations import route_injection, route_source
from camera_compat import route_source as route_camera
from inventory_compat import route_source as route_inventory
from placement_input_compat import route_source as route_placement_input


REVISION = "f00125f61f794aaa3d4e950d301f353b35b6ee3d"
HOOKS = "crates/horizon-client/src/game/native_hooks/"
BRIDGE = HOOKS + "lanminecraft/legacy.rs"
EXTRAS = "crates/horizon-client/src/game/interface/extras.rs"
GAME = "crates/horizon-client/src/game.rs"
INPUT = "crates/horizon-client/src/game/interface/play_input.rs"
CHAT = "crates/horizon-client/src/game/interface/chat.rs"
CHAT_RENDER = "crates/horizon-client-render/src/chat.rs"
FRAME_OPTIONS = "crates/horizon-client/src/game/interface/options.rs"
REMOTE = "crates/horizon-client/src/game/network/remote/mod.rs"
LEVELS = "crates/horizon-client/src/game/world/levels.rs"
OPTIONS = "crates/horizon-client-render/src/options_ui.rs"
MOD_LIST = "crates/horizon-mod-package/src/mod_list.rs"
RENDER = "crates/horizon-client-render/src/render_slots.rs"
TARGET = "horizon_client::game::native_hooks::"
LEGACY_ROUTES = {
    name: f"{TARGET}legacy::Game<'_>::native_{name}"
    for name in ("frame", "hud", "key", "mods", "settings", "settings_click", "tick", "wheel", "commands")
}
LEGACY_ROUTES["command"] = f"{TARGET}legacy::Game<'_>::legacy_command"
LEGACY_ROUTES["server_tick"] = f"{TARGET}legacy::Game<'_>::legacy_server_tick"
LEGACY_ROUTES["interact_entity"] = f"{TARGET}legacy::Game<'_>::native_interact_entity"
BASE_HOOKS = {
    "camera_shake", "gui_layer_post", "minimap", "render_fog", "screen_char_typed_pre",
    "screen_key_pressed_pre", "screen_mouse_button_pressed_pre", "screen_mouse_scrolled_pre",
    "screen_render_post", "zoom",
}
VOCABULARY = {
    "crate::game::native_hooks::NativeMinimap": "mod_api::Minimap",
    "crate::game::native_hooks::NativeScreenView": "mod_api::ScreenView",
    "crate::game::native_hooks::NativeShake": "mod_api::game::Shake",
    "crate::game::native_hooks::NativeZoom": "mod_api::Zoom",
    "crate::mod_api::NativeMod": "crate::game::native_hooks::NativeMod",
    "horizon_client::mod_api::NativeMod": "crate::game::native_hooks::NativeMod",
    "horizon_client::fog::FogData": "mod_api::client::fog::FogData",
    "horizon_client::fog::FogType": "mod_api::client::fog::FogType",
    "horizon_client::render_slots::": "horizon_client_render::render_slots::",
    "horizon_world::nbt::Tag": "horizon_core::nbt::Tag",
    "crate::gfx::Vertex": "crate::render_geometry::Vertex",
    "super::invisible_frames_sneak": "crate::game::invisible_frames_sneak",
}
RENDER_ROUTES = {
    "world_drawn": "horizon_client_render::render_slots::world_drawn",
    "render_needs": "horizon_client_render::render_slots::legacy_render_needs",
}
HOST_SHA256 = {
    HOOKS + "mod.rs": "459d3e03bda315a80af77c4ac0da7e88e96b4bd7e3983524b4e52b87ba788a2f",
    HOOKS + "shared.rs": "4ed5d6ca044769cc965e0022297bc9ad8d994a9f7776b36c6863a2716d199582",
    HOOKS + "base.rs": "474aba738795f5a7dba47c87a0acb4d887e6d72a58571e2634b0ea871554680b",
    EXTRAS: "eb0ca00dd278856404e3aace4e381c74f2ab6d124c9df6822d699e278d5d5167",
    REMOTE: (
        "61302806122979e67484cf5d216444ebc2d8d108f67815a6b1a5939c02a3d665",
        "f0c3f66800b550603766156e91901dd18535f0a17aca6df70ea5cf8c5e7e2e52",
    ),
    LEVELS: "90046f3711d7fbcb128a2b532517128e20b85f1fc02b0b20f450b1198f993364",
    CHAT: (
        "5a397021d79e624ad9bea2bf6978a3143d185d68be2e35e9048d14a7098b5f2a",
        "1283c77d963ae1d4885cc27ffab643ac2c6f7bb4b1eac198a426a8d8abbc6a29",
    ),
    CHAT_RENDER: "44f9c16ae033df4e85c074b96825cf09bed4bbf49268a77b04b6d228b10a5676",
    FRAME_OPTIONS: "b88a96572beae0e8bcfc7823b3902155f947df6a2e6dcce920111f741f3f6f2e",
    OPTIONS: "012b7e2e2cda62ca1667152e415be4a39857f3585a9b9493329f78532bd1f7d8",
    MOD_LIST: "e2b947ad74cbf30296adf33e35f73b3b34ea87e4562afa53162f73e2a0796417",
    RENDER: "f9f96bdce2bdfa57a79606258d344587802e91ac0b64b27a909b483e144cbc3c",
}


def replace_once(source, before, after):
    if source.count(before) != 1:
        raise ValueError(f"Legacy native host anchor changed: {before[:100]!r}")
    return source.replace(before, after)


def prepare_source(workspace: Path, setting_owners: list[str]) -> None:
    if len(setting_owners) != len(set(setting_owners)) or any(
        re.fullmatch(r"[a-z0-9_]+", owner) is None for owner in setting_owners
    ):
        raise ValueError("Invalid legacy settings owner in the verified native selection")
    originals = {}
    bridge = workspace / BRIDGE
    if bridge.exists() or bridge.is_symlink():
        raise ValueError("Legacy native hook host was already present")
    for name, expected in HOST_SHA256.items():
        path = workspace / name
        raw = path.read_bytes()
        pins = (expected,) if isinstance(expected, str) else expected
        if path.is_symlink() or path.is_junction() or sha256(raw).hexdigest() not in pins:
            raise ValueError(f"Legacy native host source changed: {name}")
        originals[name] = raw.decode("utf-8")
    originals[HOOKS + "mod.rs"] = replace_once(originals[HOOKS + "mod.rs"],
        'include!("shared.rs");',
        '#[path = "lanminecraft/legacy.rs"]\nmod legacy;\n'
        'pub(in crate::game) use legacy::NativeMod;\ninclude!("shared.rs");')
    base = originals[HOOKS + "base.rs"]
    for name, arguments, returns_claim in (
        ("command", "command, ran", True),
        ("panel_event", "mod_id, event", False),
        ("register_commands", "commands", False),
        ("run_mod_command", "source, command, ran", True),
    ):
        original = f"self.fire_{name}({arguments});"
        downstream = f"if !*ran {{\n            {original}\n        }}" if returns_claim else original
        base = replace_once(base, "        " + original,
            f"        self.legacy_{name}({arguments});\n        {downstream}")
    base = replace_once(base, "        self.fire_server_tick(context, phase);",
        "        self.legacy_server_tick();\n        self.fire_server_tick(context, phase);")
    base = replace_once(base, "        self.fire_entity_interact(dimension, hand, entity, kind, stack, result);",
        "        let mut claimed = false;\n"
        "        self.native_interact_entity(entity, kind, &mut claimed);\n"
        "        if claimed {\n"
        "            *result = Some(mod_api::InteractionResult::Success);\n"
        "        } else {\n"
        "            self.fire_entity_interact(dimension, hand, entity, kind, stack, result);\n"
        "        }")
    originals[HOOKS + "base.rs"] = base
    extras = replace_once(originals[EXTRAS], "        let (world_partial_tick, partial_tick)",
        "        self.native_frame(elapsed);\n        let (world_partial_tick, partial_tick)")
    originals[EXTRAS] = replace_once(extras, "        self.native_render_gui_post(batch, &mut corners);",
        "        self.native_hud(batch, &mut corners);\n        self.native_render_gui_post(batch, &mut corners);")
    originals[REMOTE] = replace_once(originals[REMOTE], "        // Far tiles the samplers asked for (`lod_net`).",
        "        self.native_tick();\n        // Far tiles the samplers asked for (`lod_net`).")
    originals[LEVELS] = replace_once(originals[LEVELS],
        '        span("tick_command_extras", || self.tick_command_extras());\n        self.tick_world(running)',
        '        span("tick_command_extras", || self.tick_command_extras());\n'
        '        self.native_tick();\n        self.tick_world(running)')
    originals[CHAT] = replace_once(originals[CHAT],
        '        with_studio_commands(&with_native_commands(&tree_with_mods, &["studio"])).with_profiler()',
        '        let mut legacy = vec!["studio"];\n        self.native_commands(&mut legacy);\n'
        '        with_studio_commands(&with_native_commands(&tree_with_mods, &legacy)).with_profiler()')
    originals[CHAT_RENDER] = replace_once(originals[CHAT_RENDER],
        "        paint(batch, self.layout(batch.height as i32, ticks, focused));",
        "        let draws: Vec<crate::hud_layout::GuiDraw> = self.layout(batch.height as i32, ticks, focused);\n"
        "        paint(batch, draws);")
    originals[FRAME_OPTIONS] = replace_once(originals[FRAME_OPTIONS],
        "matches!(w.fullscreen(), Some(Fullscreen::Exclusive(_)))",
        "match w.fullscreen() { Some(Fullscreen::Exclusive(_)) => true, _ => false }")
    originals[MOD_LIST] = replace_once(originals[MOD_LIST], "pub enum NativeSettingValue {",
        "pub enum NativeSettingValue {\n    /// A legacy action label already includes its state, if any.\n    LegacyAction,")
    originals[MOD_LIST] = replace_once(originals[MOD_LIST], "            Self::Toggle(on) => *on = !*on,",
        "            Self::LegacyAction => {}\n            Self::Toggle(on) => *on = !*on,")
    originals[OPTIONS] = replace_once(originals[OPTIONS], "        V::Toggle(on) => on_off(*on),",
        "        V::LegacyAction => return Text::literal(setting.label.clone()),\n        V::Toggle(on) => on_off(*on),")
    render = replace_once(originals[RENDER], "pub fn needs() -> RenderNeeds {\n    let mut n = RenderNeeds::default();",
        "fn legacy_render_needs(needs: &mut RenderNeeds) {\n    let _ = needs;\n}\n\n"
        "fn legacy_needs() -> RenderNeeds {\n    let mut n = RenderNeeds::default();\n"
        "    legacy_render_needs(&mut n);\n    n\n}\n\n"
        "pub fn needs() -> RenderNeeds {\n    let mut n = legacy_needs();")
    originals[RENDER] = replace_once(render, "    SLOTS.get().is_some_and(|s| (s.world_drawn_hooked)())",
        "    SLOTS.get().is_some_and(|s| (s.world_drawn_hooked)()) || {\n"
        "        let needs = legacy_needs();\n        needs.world_depth || needs.entity_spans\n    }")
    shared = originals[HOOKS + "shared.rs"]
    changes = (
        ('''        if pressed
            && !repeat
            && let Some(name) = keybinds::key_name(key)
        {
            self.native_panel_key(name);
        }''', '''        if !repeat && let Some(name) = keybinds::key_name(key) {
            if pressed {
                self.native_panel_key(name);
            }
            self.native_key(name, pressed);
        }'''),
        ("        let native = self.mods.native.clone();\n", "        let native = self.mods.native.clone();\n"
         "        let legacy = self.legacy_native_mod_rows();\n"),
        ("            .retain(|row| !rows.iter().any(|code| code.id == row.id));",
         "            .retain(|row| !rows.iter().chain(&legacy).any(|code| code.id == row.id));"),
        ("        for mut row in rows {",
         "        // A compiled transport can share its identity with a legacy settings page.\n"
         "        rows.retain(|row| !legacy.iter().any(|code| code.id == row.id));\n"
         "        for mut row in rows {"),
        ("            self.settings.mods.installed.push(row);\n        }\n    }\n    /// The settings mod",
         "            self.settings.mods.installed.push(row);\n        }\n"
         "        self.settings.mods.installed.extend(legacy);\n    }\n    /// The settings mod"),
        ("            NativeSettingValue::Toggle(on) => mod_api::SettingValue::Toggle(*on),",
         "            NativeSettingValue::LegacyAction => {\n                self.native_settings_click(&id, j);\n"
         "                self.native_settings_refresh(i);\n                return;\n            }\n"
         "            NativeSettingValue::Toggle(on) => mod_api::SettingValue::Toggle(*on),"),
        ("        let settings = self.native_page_settings(&id);",
         "        let settings = if self.has_legacy_native_mod(&id) {\n            self.legacy_native_settings(&id)\n"
         "        } else {\n            self.native_page_settings(&id)\n        };"),
    )
    for before, after in changes:
        shared = replace_once(shared, before, after)
    originals[HOOKS + "shared.rs"] = shared
    for name, source in originals.items():
        (workspace / name).write_text(source, encoding="utf-8", newline="\n")
    bridge.parent.mkdir(parents=True, exist_ok=True)
    owners = ", ".join(f'"{owner}"' for owner in sorted(setting_owners))
    bridge_source = Path(__file__).with_suffix(".rs").read_text(encoding="utf-8")
    bridge.write_text(replace_once(bridge_source, "const LEGACY_SETTINGS_OWNERS: &[&str] = &[];",
                                   f"const LEGACY_SETTINGS_OWNERS: &[&str] = &[{owners}];"),
                      encoding="utf-8", newline="\n")


def prepare_wheel(workspace: Path) -> None:
    paths = {
        GAME: (
            "98769993cbeb7f8f40eecfbc7843d820ce729a7c7e7f7c83c4168e75a65e20c0",
            "497b9f928a626f2f21b24943f60828e2905e5e5c0391477250bfde7cac899bdf",
            # The same exact Edition versions after the Creative JEI patch.
            "153b4325027d9e15b9a0b088ac9921f061f3fec2f07d5013ad219061608fab91",
            "5ddbee87cb6041c904eb812353c130fdc6b7d9b0eca12ea12bf6822d66487c5b",
        ),
        INPUT: ("92776b00326eb210f2ceaf908312113596c0456a89cf3f1dd87b249fd5295d9c",),
    }
    verified = {}
    for name, hashes in paths.items():
        path = workspace / name
        raw = path.read_bytes()
        if path.is_symlink() or path.is_junction() or sha256(raw).hexdigest() not in hashes:
            raise ValueError(f"Legacy native wheel source changed: {name}")
        verified[path] = raw.decode("utf-8")
    game = workspace / GAME
    input_path = workspace / INPUT
    game_text = replace_once(verified[game], "                    if self.hotbar_takes_wheel() {\n",
        "                    if self.hotbar_takes_wheel() && !self.legacy_wheel_claimed(d) {\n")
    input_text = replace_once(verified[input_path], "        if self.hotbar_takes_wheel()\n",
        "        if self.hotbar_takes_wheel()\n            && !self.legacy_wheel_claimed(wheel as f32)\n")
    game.write_text(game_text, encoding="utf-8", newline="\n")
    input_path.write_text(input_text, encoding="utf-8", newline="\n")


def route_manifest(text: str) -> str:
    """Only redirect known legacy dispatches. The mixin planner still checks their real targets."""
    sections = re.split(r"(?m)(?=^\[\[injection\]\])", route_source_paths(text))
    for index, section in enumerate(sections[1:], 1):
        injection = tomllib.loads(section.split("\n[[", 1)[0])["injection"][0]
        named = injection.get("hook")
        direct = injection.get("target", "")
        target = LEGACY_ROUTES.get(named) or RENDER_ROUTES.get(named)
        if target and injection.get("hook_version") == 1:
            section, count = re.subn(r'(?m)^hook\s*=\s*[^\n]*\nhook_version\s*=\s*1\s*\n',
                                     f'target = "{target}"\n', section, count=1)
            if count != 1:
                raise ValueError(f"Cannot route changed named legacy hook: {named}")
        for name, target in RENDER_ROUTES.items():
            before = f"horizon_client_render::render_slots::native_{name}"
            if direct == before:
                section = replace_once(section, before, target)
        for name, target in LEGACY_ROUTES.items():
            before = f'{TARGET}Game<\'_>::native_{name}'
            if direct == before:
                section = replace_once(section, before, target)
        for name in BASE_HOOKS:
            before = f"{TARGET}Game<'_>::native_{name}"
            if direct == before:
                section = replace_once(section, before, f"{TARGET}base::Game<'_>::native_{name}")
                if (name in ("gui_layer_post", "screen_render_post") and any(
                        binding.get("value") == "&mut *batch" for binding in injection.get("bindings", []))):
                    section = replace_once(section, 'value = "&mut *batch"', 'value = "&mut *draw"')
        if direct == "horizon_client_render::chat::Chat::draw" and injection.get("variable", {}).get("name") == "draws":
            section = replace_once(section, 'Chat::draw"', 'Chat::draw_at"')
        if direct == "horizon_client_render::camera::view_matrix":
            section = replace_once(section, 'camera::view_matrix"', 'camera::view_matrix_default"')
        sections[index] = route_injection(section, injection)
    return "".join(sections)


def route_source_paths(text: str) -> str:
    text = text.replace("gfx::native_render::", "render_slots::").replace(
        "crates/horizon-client-render/src/gfx/native_render.rs", RENDER).replace(
        "crates/horizon-client/src/game/chat.rs", CHAT)
    for old, new in VOCABULARY.items():
        text = text.replace(old, new)
    return text


def route_bundle_source(text: str) -> str:
    # Both Horizon revisions use the same GUI atlas measurement. The new typed
    # host takes an additional (unused) HudBatch; old source has no draw handle.
    text = route_source(route_source_paths(text)).replace(".native_text_width(", ".legacy_text_width(")
    for name in ("mod_screen", "mod_screen_open", "mod_screen_close"):
        text = text.replace(f".native_{name}(", f".{name}(")
    text = text.replace("Game::native_config_dir()?", "Game::native_config_dir().map_err(|error| error.to_string())?")
    text = text.replace("game.native_acting_player().map(|p| p.uuid)", "game.native_acting_player().map(|p| p.uuid())")
    return route_placement_input(route_inventory(route_camera(text)))
