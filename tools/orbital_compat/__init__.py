"""Adapt the verified Orbital Laser 0.5.9 native bundle to this pack."""

import hashlib
from pathlib import Path


RECIPE = Path(__file__).resolve().parent
SOURCE_HASHES = {
    "horizon-mixin.toml": "63e0662de7f5b18fed08a89f5df91e8fbbe74f17d8a5a3efdbfb13765af8f51a",
    "content_find.rs": "318dc888ad3e76f84c41e3cae14233ad34eae76c79f5bd0f1de4c51f26ea5abe",
    "glue.rs": "18eff45a2261e6f1a1aae5cd0c1af99efbdffdfe2ead59694f64d6a750b42773",
    "engine/src/lib.rs": "ae7d0e6a09cc799f89f4ddbdfe9acbc096b0a3acde83be9e8990229b1d191747",
    "perform_command.rs": "86a22bd60e0d268141d40a4829e558f36cf828f63735ee518cbc1c52fd25f584",
}


def replace(source: str, before: str, after: str) -> str:
    if source.count(before) != 1:
        raise ValueError(f"Expected one Orbital Laser source anchor: {before[:100]!r}")
    return source.replace(before, after)


def section(source: str, start: str, end: str, replacement: str) -> str:
    left = source.index(start)
    right = source.index(end, left)
    return source[:left] + replacement + source[right:]


def adapt_glue(source: str) -> str:
    source = section(source, "struct Reader<'a>", "fn send(message", "")
    source = section(
        source,
        "/// The `net_from_server` hook:",
        "// ---- the server half",
        (RECIPE / "answers.rs").read_text(encoding="utf-8").rstrip() + "\n\n",
    )
    changes = [
        (
            "use super::Game;",
            """use super::Game;
use super::orbital_wire::{self as wire, READ_COLUMNS, Reader};
use horizon_client::chunk::ColumnLayout;""",
        ),
        (
            "const READ_COLUMNS: usize = 3000;",
            "const MAX_HEIGHT: i32 = ColumnLayout::HIGHEST_Y + 1 - ColumnLayout::LOWEST_Y;",
        ),
        (
            "    surface: BTreeMap<u32, (u32, Vec<Option<i32>>, Vec<bool>)>,",
            """    pending_surface: BTreeMap<u32, usize>,
    pending_columns: BTreeMap<u32, (usize, usize)>,
    surface: BTreeMap<u32, (Vec<Option<i32>>, Vec<bool>)>,""",
        ),
        (
            "    total: usize,\n    got: usize,\n    span: usize,",
            "    got: usize,\n    seen: Vec<bool>,",
        ),
        (
            "    let leaving = CLIENT.with(|c| c.borrow().leaving);",
            "    let leaving = CLIENT.with(|c| c.borrow().leaving);\n    let local_world = game.integrated.is_some();",
        ),
        (
            "            host::Output::Commands { commands } => send_commands(&commands),",
            """            host::Output::Commands { .. } | host::Output::Edit { .. }
            | host::Output::Surface { .. } | host::Output::Columns { .. } if !local_world => {}
            host::Output::Commands { commands } => send_commands(&commands),""",
        ),
        (
            "    sync_flying_speed(game, fly);",
            "    if local_world {\n        sync_flying_speed(game, fly);\n    }",
        ),
        (
            "fn send_surface(tag: u32, columns: &[(i32, i32)], h: u8) {",
            """fn send_surface(tag: u32, columns: &[(i32, i32)], h: u8) {
    if columns.len() > wire::QUERY_COLUMNS {
        return;
    }
    CLIENT.with(|c| {
        let mut c = c.borrow_mut();
        c.surface.remove(&tag);
        c.pending_surface.insert(tag, columns.len());
    });""",
        ),
        (
            "fn send_columns(tag: u32, columns: &[(i32, i32)], h: u8, below: u32, above: u32) {",
            """fn send_columns(tag: u32, columns: &[(i32, i32)], h: u8, below: u32, above: u32) {
    let Some(span) = wire::span(columns.len(), below, above, MAX_HEIGHT) else {
        return;
    };
    if columns.len() > wire::QUERY_COLUMNS {
        return;
    }
    CLIENT.with(|c| {
        let mut c = c.borrow_mut();
        c.columns.remove(&tag);
        c.pending_columns.insert(tag, (columns.len(), span));
    });""",
        ),
        (
            """    if channel != WORK || game.native_window() || !game.native_on(ID) {
        return;
    }
    SERVER.with(|s| s.borrow_mut().work.entry(player).or_default().push_back(data.to_vec()));""",
            """    if channel != WORK || game.native_window() || !game.native_on(ID)
        || data.len() > wire::MESSAGE_LIMIT || !local_operator(game, player)
    {
        return;
    }
    SERVER.with(|s| {
        let mut s = s.borrow_mut();
        let queue = s.work.entry(player).or_default();
        if queue.len() < super::native_net::PER_TICK as usize {
            queue.push_back(data.to_vec());
        }
    });
}

/// The native release drives the local integrated server. A LAN or Steam
/// guest has a network connection, even if it comes from loopback.
fn local_operator(game: &Game<'_>, player: u128) -> bool {
    game.lan.as_ref().is_some_and(|host| {
        host.hub.singleplayer_owner == Some(player)
            && host.hub.guests.iter().any(|guest| {
                guest.placed && guest.uuid == player && guest.permission >= 2
                    && guest.address == horizon_client::lan::conn::MEMORY_ADDRESS
            })
    })""",
        ),
        (
            """            let mut r = Reader(&batch);
            if r.u8() != Some(9) {
                log::warn!("[mod {ID}] a bad message from {}", uuid(player));
                continue;
            }
            while let Some(n) = r.u32() {
                let Some(request) = r.take(n as usize) else { break };""",
            """            let Some(requests) = wire::batch(&batch) else {
                log::warn!("[mod {ID}] a bad message from {}", uuid(player));
                continue;
            };
            for request in requests {""",
        ),
        (
            "        if permission < 2 {",
            "        if permission < 2 || !local_operator(game, player) {",
        ),
        (
            "            for line in r.strs()? {",
            """            let lines = r.strs()?;
            if !r.0.is_empty() { return None; }
            for line in lines {""",
        ),
        (
            "            let n = r.u32()? as usize;\n            let mut runs = Vec::with_capacity(n);",
            """            let n = r.count(25)?;
            if r.0.len() != n * 25 { return None; }
            let mut runs = Vec::with_capacity(n);""",
        ),
        (
            "            let columns = r.columns()?;\n            let heights = game",
            """            let columns = r.columns()?;
            wire::fragment(total as usize, offset as usize, columns.len())?;
            if !r.0.is_empty() { return None; }
            let heights = game""",
        ),
        (
            "            let span = (below + above) as usize;",
            """            wire::fragment(total as usize, offset as usize, columns.len())?;
            let span = wire::span(total as usize, below, above, MAX_HEIGHT)?;
            if !r.0.is_empty() { return None; }""",
        ),
        (
            "            for start in (0..columns.len()).step_by(per) {",
            "            for start in (0..columns.len().max(1)).step_by(per) {",
        ),
        (
            "            let speed = r.f32()?;",
            "            let speed = r.f32()?;\n            if !r.0.is_empty() { return None; }",
        ),
        (
            """    if !game.native_window() {
        if let Ok(mut c) = SERVER_COMMANDS.lock() {
            c.push(line.to_owned());
        }
        return true;
    }
    if !running(game) {""",
            """    if !running(game) {""",
        ),
        (
            "            c.surface.clear();\n            c.columns.clear();",
            """            c.surface.clear();
            c.columns.clear();
            c.pending_surface.clear();
            c.pending_columns.clear();""",
        ),
        (
            '/// The `perform_command` hook:',
            '/// The `perform_console_command` hook:',
        ),
        (
            """    if *ran || game.native_window() || (line != ID && !line.starts_with("orbital ")) {""",
            """    let local = game.lan.as_ref().is_some_and(|host| host.hub.singleplayer_owner.is_some());
    if *ran || game.native_window() || !local || (line != ID && !line.starts_with("orbital ")) {""",
        ),
        (
            r'Install \"Orbital Laser (content)\" from the launcher\'s Mods page.'.replace("\\'", "'"),
            "Repair Minecraft Rust Mods to restore its Orbital Laser content.",
        ),
    ]
    for before, after in changes[:-1]:
        source = replace(source, before, after)
    before, after = changes[-1]
    if source.count(before) != 2:
        raise ValueError("Orbital Laser content error text changed")
    return source.replace(before, after)


def apply(bundle: Path) -> None:
    for name, expected in SOURCE_HASHES.items():
        if hashlib.sha256((bundle / name).read_bytes()).hexdigest() != expected:
            raise ValueError(f"Unexpected Orbital Laser 0.5.9 source: {name}")

    manifest = bundle / "horizon-mixin.toml"
    source = manifest.read_text(encoding="utf-8")
    # The original manifest and assets are installed in an ordinary orbital.hmod.
    anchor = "# One file for code and content:"
    before, found, shim = source.partition(anchor)
    if not found or 'id = "content_find"' not in shim or shim.count("[[injection]]") != 1:
        raise ValueError("Orbital Laser's content discovery shim changed")
    source = replace(before.rstrip() + "\n", 'hook = "perform_command"', 'hook = "perform_console_command"')
    source = replace(
        source, 'source = "glue.rs"\n', '''source = "glue.rs"

[[module]]
id = "wire"
file = "crates/horizon-client/src/game.rs"
name = "orbital_wire"
source = "wire.rs"
''',
    )
    glue = bundle / "glue.rs"
    updated_glue = adapt_glue(glue.read_text(encoding="utf-8"))
    engine = bundle / "engine/src/lib.rs"
    updated_engine = replace(
        engine.read_text(encoding="utf-8"),
        'input::register_key("wheel", "Open the laser wheel", "key.keyboard.r");',
        'input::register_key("wheel", "Open the laser wheel", "key.keyboard.insert");',
    )
    manifest.write_text(source, encoding="utf-8", newline="\n")
    glue.write_text(updated_glue, encoding="utf-8", newline="\n")
    engine.write_text(updated_engine, encoding="utf-8", newline="\n")
    (bundle / "wire.rs").write_bytes((RECIPE / "wire.rs").read_bytes())
    (bundle / "content_find.rs").unlink()
