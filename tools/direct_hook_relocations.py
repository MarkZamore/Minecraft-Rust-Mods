"""Map direct source injection points to the equivalent f00125f modules."""


GAME = "horizon_client::game"
GAME_ROOT_MODULES = {
    "handle_window_event": "window_handler",
    "apply_mouse_look": "pointer_capture",
    "pauses_game": "pointer_capture",
    "attack": "melee",
    "break_block": "block_breaking",
    "destroy_particles": "block_particles",
    "crack_particle": "block_particles",
    "play_mouse": "play_input",
    "inventory_drag_move": "inventory_input",
    "inventory_mouse_release": "inventory_input",
    "use_hand": "use_hand",
}
TARGETS = {
    f"{GAME}::Game<'_>::{method}": f"{GAME}::{module}::Game<'_>::{method}"
    for method, module in GAME_ROOT_MODULES.items()
}
TARGETS.update({
    f"{GAME}::tweaks::Game<'_>::sort_slots": f"{GAME}::extras::sorting::Game<'_>::sort_slots",
    f"{GAME}::tweaks::Game<'_>::sort_mouse_press": f"{GAME}::extras::sorting::Game<'_>::sort_mouse_press",
    f"{GAME}::tweaks::Game<'_>::tweak_scroll": f"{GAME}::extras::mouse_tweaks::Game<'_>::tweak_scroll",
    f"{GAME}::lan_actions::Game<'_>::enter_guest": f"{GAME}::lan_actions::turns::Game<'_>::enter_guest_at",
    f"{GAME}::lan_actions::Game<'_>::guest_command": f"{GAME}::lan_actions::commands::Game<'_>::guest_command",
    "horizon_client_render::options_ui::OptionsUi::layout":
        "horizon_client_render::options_ui::layout::OptionsUi::layout",
})

SNEAK_HOST = ' {name="host",ty="&mut crate::game::lan::LanHost",value="&mut *host"},\n'
SNEAK_GAME = (
    ' {name="game",ty="&crate::game::Game<\'_>",value="&*self"},\n'
    ' {name="i",ty="usize",value="i"},\n'
)
COMMAND_HOST = '  { name = "host", ty = "&mut crate::game::lan::LanHost", value = "&mut *host" },\n'

SNEAK_HELPER = '''fn capture(host: &mut crate::game::lan::LanHost, conn: u32) {
    if let Some(guest) = host.hub.guest(conn) {'''
SNEAK_CURRENT = '''fn capture(game: &crate::game::Game<'_>, i: usize, conn: u32) {
    let guest = game.lan.as_ref().and_then(|host| host.hub.guests.get(i));
    if let Some(guest) = guest.filter(|guest| guest.conn == conn) {'''

COMMAND_HELPER = '''fn guest_command(game: &mut crate::game::Game<'_>, host: &mut crate::game::lan::LanHost, conn: u32, command: &str) -> core::ops::ControlFlow<()> {'''
COMMAND_CURRENT = '''fn guest_command(
    game: &mut crate::game::Game<'_>,
    conn: u32,
    command: &str,
) -> core::ops::ControlFlow<()> {'''
COMMAND_PERMISSION = "    if let Some(permission) = host.hub.guest(conn).map(|g| g.permission) {"
COMMAND_CURRENT_PERMISSION = '''    let Some(host) = game.lan.as_mut() else {
        return core::ops::ControlFlow::Continue(());
    };
    if let Some(permission) = host.hub.guest(conn).map(|g| g.permission) {'''


def replace_once(text: str, old: str, new: str) -> str:
    if text.count(old) != 1:
        raise ValueError(f"Changed direct hook relocation anchor: {old!r}")
    return text.replace(old, new)


def route_injection(section: str, injection: dict) -> str:
    target = injection.get("target")
    if target not in TARGETS:
        return section
    section = replace_once(section, f'"{target}"', f'"{TARGETS[target]}"')
    if target.endswith("::enter_guest"):
        section = replace_once(section, SNEAK_HOST, SNEAK_GAME)
    elif target.endswith("::guest_command"):
        section = replace_once(section, COMMAND_HOST, "")
    return section


def route_source(text: str) -> str:
    if SNEAK_HELPER in text:
        return replace_once(text, SNEAK_HELPER, SNEAK_CURRENT)
    if COMMAND_HELPER in text:
        text = replace_once(text, COMMAND_HELPER, COMMAND_CURRENT)
        text = replace_once(text, COMMAND_PERMISSION, COMMAND_CURRENT_PERMISSION)
        return replace_once(text, "    let _ = game;\n", "")
    return text
