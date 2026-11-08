"""Adapt the verified Orbital Laser 0.5.9 native bundle to this pack."""

import hashlib
from pathlib import Path


RECIPE = Path(__file__).resolve().parent
QUIET_SOURCE = "crates/horizon-client/src/game/lan_quiet.rs"
QUIET_SOURCE_SHA256 = "d815f06570366b9146e64f879dc8c6cd675225347296cb3faa08bf44c855d7a9"
SOURCE_HASHES = {
    "engine/src/strike/hurricane.rs": "b8d429b497f8fed80e4007f761169ebeed877602a0b19d10714b9c83645b7b56",
    "engine/src/sdk.rs": "e0ccae671baaf558bd45032db710b1e2c1096ff08ca75f60465e30547ab6670f",
    "post.rs": "7933d9bd5721f0e39035b38b1932c16dbb85ee74144129a3ec40e18950019a4e",
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


def quiet_source(raw: bytes) -> str:
    """Expose the author's checked, quiet player-command path to this mod only."""
    if hashlib.sha256(raw).hexdigest() != QUIET_SOURCE_SHA256:
        raise ValueError(f"Orbital quiet command source has changed: {QUIET_SOURCE}")
    before = "    fn run_quiet_line(&mut self, conn: u32, line: &str) -> bool {"
    after = "    pub(super) fn run_quiet_line(&mut self, conn: u32, line: &str) -> bool {"
    return replace(raw.decode("utf-8"), before, after)


def prepare_source(stage: Path) -> None:
    path = stage / QUIET_SOURCE
    path.write_text(quiet_source(path.read_bytes()), encoding="utf-8", newline="\n")


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
        || data.len() > wire::MESSAGE_LIMIT || !server_operator(game, player)
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

/// World mutation is authorized by the server's placed-player permissions.
/// A Steam or LAN guest with operator permission uses the same path as the owner.
fn server_operator(game: &Game<'_>, player: u128) -> bool {
    game.lan.as_ref().is_some_and(|host| {
        host.hub.guests.iter().any(|guest| {
            guest.placed && guest.uuid == player && guest.permission >= 2
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
            "        if permission < 2 || !server_operator(game, player) {",
        ),
        (
            "            for line in r.strs()? {",
            """            let lines = r.strs()?;
            if !r.0.is_empty() { return None; }
            for line in lines {""",
        ),
        (
            "            let who = uuid(player);",
            """            // Use the placed player's live connection and the author's quiet
            // command path. `execute as` from the console would retain level 4.
            let conn = game.lan.as_ref()?.hub.guests.iter()
                .find(|guest| guest.placed && guest.uuid == player)?.conn;""",
        ),
        (
            '''                game.native_run_command(&format!("execute as {who} at @s run {}", line.trim_start_matches('/')));''',
            '''                game.run_quiet_line(conn, line.trim_start_matches('/'));''',
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


def adapt_visual_glue(source: str) -> str:
    """Keep the author's world engine local, share its typed render output."""
    changes = [
        ('    horizon_client_render::orbital_post::set(pass);', '''    super::orbital_visual::capture(game, pass.as_ref());
    horizon_client_render::orbital_post::set(pass);
    super::orbital_visual::refresh(game);'''),
        ('                    game.audio.play(&event, at, volume, pitch);', '''                    game.audio.play(&event, at, volume, pitch);
                    super::orbital_visual::sound(game, &event, at, volume, pitch);'''),
        ('    if channel != ANSWER\n', '''    if super::orbital_visual::from_server(game, channel, data) { return; }
    if channel != ANSWER
'''),
        ('''pub(super) fn from_client(game: &mut Game<'_>, player: u128, channel: &str, data: &[u8]) {
''', '''pub(super) fn from_client(game: &mut Game<'_>, player: u128, channel: &str, data: &[u8]) {
    if super::orbital_visual::from_client(game, player, channel, data) { return; }
'''),
        ('    // answers leave as the channel allows, the rest next tick', '''    // Visual state/data reserves half the native quota before bulk answers.
    super::orbital_visual::tick_server(game, dimension);
    // answers leave as the channel allows, the rest next tick'''),
        ('    flush_work(game);', '''    super::orbital_visual::tick_client(game);
    flush_work(game);'''),
        ('''pub(super) fn client_level_load(_game: &mut Game<'_>, dimension: &str) {
''', '''pub(super) fn client_level_load(game: &mut Game<'_>, dimension: &str) {
    super::orbital_visual::clear(game);
'''),
        ('''    if !left || !game.native_window() {
        return;
    }''', '''    if !left || !game.native_window() {
        return;
    }
    super::orbital_visual::clear(game);'''),
        ('            host::Output::Commands { commands } => send_commands(&commands),', '''            host::Output::Commands { commands } => {
                for event in commands.iter().filter_map(|c| c.strip_prefix("stopsound @s * ")) {
                    game.audio.stop_sounds(Some(event.trim()), None);
                    super::orbital_visual::stop_sound(game, event.trim());
                }
                send_commands(&commands);
            },'''),
    ]
    for before, after in changes:
        source = replace(source, before, after)
    source = replace(source, '        static GIVEN: Cell<Option<(u64, u32)>> = const { Cell::new(None) };',
        '        static GIVEN: Cell<Option<(u64, u32)>> = const { Cell::new(None) };\n        static ATLAS_SERIAL: Cell<u64> = const { Cell::new(0) };')
    source = replace(source, '    GIVEN.with(|g| g.set(Some((version, split))));',
        '    GIVEN.with(|g| g.set(Some((version, split))));\n    let atlas_version = ATLAS_SERIAL.with(|v| { let next = v.get().wrapping_add(1); v.set(next); next });')
    source = replace(source, '            version,\n        })', '            version: atlas_version,\n        })')
    return source


def adapt_hint_glue(source: str) -> str:
    source = replace(source, "/// `draw_mod_panels`.",
                     (RECIPE / "wheel_hint.rs").read_text(encoding="utf-8").rstrip() + "\n\n/// `draw_mod_panels`.")
    source = replace(source, "    let Some((corner, lines)) = host::with(|s| s.panel.clone()) else {",
                     "    let Some((corner, mut lines)) = host::with(|s| s.panel.clone()) else {")
    source = replace(source,
                     "    let lines: Vec<(&str, u32)> = lines.iter().map(|l| (l.text.as_str(), l.color)).collect();",
                     """    for line in &mut lines {
        line.text = wheel_hint(game, std::mem::take(&mut line.text));
    }
    let lines: Vec<(&str, u32)> = lines.iter().map(|l| (l.text.as_str(), l.color)).collect();""")
    source = replace(source, "            host::Output::Chat { name, text: t } => {",
                     """            host::Output::Chat { name, text: t } => {
                let t = wheel_hint(game, t);""")
    source = replace(source, "        Ok(Some(t)) => chat(game, Text::literal(t)),",
                     """        Ok(Some(t)) => {
            let t = wheel_hint(game, t);
            chat(game, Text::literal(t));
        },""")
    return source


def adapt_post(source: str) -> str:
    source = replace(source, 'use std::sync::{Arc, Mutex};', '''use std::sync::{Arc, Mutex};
use std::collections::BTreeMap;''')
    source = replace(source, 'static PASS: Mutex<Option<Pass>> = Mutex::new(None);',
                     'static PASS: Mutex<BTreeMap<u128, Pass>> = Mutex::new(BTreeMap::new());')
    source = replace(source, 'static DRAWER: Mutex<Option<Drawer>> = Mutex::new(None);',
                     'static DRAWER: Mutex<BTreeMap<u128, Drawer>> = Mutex::new(BTreeMap::new());')
    source = replace(source, '''        *p = pass;''', '''        if let Some(pass) = pass { p.insert(0, pass); } else { p.remove(&0); }''')
    source = replace(source, '/// HEAD of `Renderer::render`: what the pass reads of this frame.', '''/// Remote actors have their own pass and GPU buffers. They never replace the
/// local engine or each other's data and concurrent strikes compose in UUID order.
pub fn set_remote(passes: Vec<(u128, Pass)>) {
    if let Ok(mut p) = PASS.lock() {
        p.retain(|owner, _| *owner == 0);
        for (owner, pass) in passes { if owner != 0 { p.insert(owner, pass); } }
    }
}

/// HEAD of `Renderer::render`: what the pass reads of this frame.''')
    source = replace(source, 'if PASS.lock().is_ok_and(|p| p.is_some()) {', 'if PASS.lock().is_ok_and(|p| !p.is_empty()) {')
    first = '    let Some(pass) = PASS.lock().ok().and_then(|p| p.clone()) else {'
    last = '    // The block sprite arrays (the fork\'s capability `atlas`)'
    source = section(source, first, last, '''    let Some(passes) = PASS.lock().ok().map(|p| p.clone()) else { return; };
    let Some(frame) = CAPTURED.lock().ok().and_then(|c| *c) else { return; };
    let Ok(mut drawers) = DRAWER.lock() else { return; };
    drawers.retain(|owner, _| passes.contains_key(owner));
    if frame.title { return; }
    for (owner, pass) in passes {
        let drawer = drawers.entry(owner).or_insert_with(|| Drawer {
            format: world.color_format,
            post: ModPost::new(world.device, world.color_format),
            input: None,
        });
        if drawer.format != world.color_format {
            *drawer = Drawer { format: world.color_format,
                post: ModPost::new(world.device, world.color_format), input: None };
        }
        draw_one(world, &pass, frame, drawer);
    }
}

fn draw_one(world: &mut crate::gfx::native_render::WorldDrawn<'_>, pass: &Pass, frame: Captured, drawer: &mut Drawer) {
''')
    return source


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

[[module]]
id = "visual_wire"
file = "crates/horizon-client/src/game.rs"
name = "orbital_visual_wire"
source = "visual_wire.rs"

[[module]]
id = "visual"
file = "crates/horizon-client/src/game.rs"
name = "orbital_visual"
source = "visual.rs"
''',
    )
    glue = bundle / "glue.rs"
    updated_glue = adapt_hint_glue(adapt_visual_glue(adapt_glue(glue.read_text(encoding="utf-8"))))
    engine = bundle / "engine/src/lib.rs"
    updated_engine = replace(
        engine.read_text(encoding="utf-8"),
        'input::register_key("wheel", "Open the laser wheel", "key.keyboard.r");',
        'input::register_key("wheel", "Open the laser wheel", "key.keyboard.insert");',
    )
    updated_engine += '''

// Pack-owned visual replication: callers select only a shader already shipped
// by this exact mod, with this client's own quality settings. No remote WGSL.
pub fn network_pass_index(source: &str) -> Option<u8> {
    let mut index = None;
    __mod_with(|m| index = m.passes.iter().position(|p| *p == source).map(|i| i as u8));
    index
}
thread_local! {
    static NETWORK_PASSES: std::cell::RefCell<std::collections::BTreeMap<u8,
        (&'static str, std::sync::Arc<str>, u64)>> = const { std::cell::RefCell::new(std::collections::BTreeMap::new()) };
}
pub fn network_pass(index: u8) -> Option<(std::sync::Arc<str>, u64)> {
    let mut source = None;
    __mod_with(|m| {
        if let Some(p) = m.passes.get(index as usize) {
            NETWORK_PASSES.with(|cache| {
                let mut cache = cache.borrow_mut();
                let entry = cache.entry(index).or_insert_with(|| (*p, std::sync::Arc::from(*p), 0));
                if !std::ptr::eq(entry.0, *p) || entry.2 == 0 {
                    use std::hash::{Hash, Hasher};
                    let mut h = std::collections::hash_map::DefaultHasher::new();
                    p.hash(&mut h);
                    *entry = (*p, std::sync::Arc::from(*p), h.finish());
                }
                source = Some((entry.1.clone(), entry.2));
            });
        }
    });
    source
}
'''
    updated_engine += """
pub fn network_atlas_version() -> u64 { sdk::atlas::resources_version() }
pub fn network_repack(shader: u8, words: &mut [u32]) {
    if shader == 9 { strike::hu_repack(words); }
}
""".replace("\n+", "\n")
    sdk = bundle / "engine/src/sdk.rs"
    sdk_source = sdk.read_text(encoding="utf-8")
    # Native SDK aliasing must not resolve back into the test doubles themselves.
    sdk_source = sdk_source.replace("horizon_mod_sdk::", "crate::horizon_sdk::")
    sdk_source = replace(sdk_source, '        pub fn resources_version() -> u64 {\n            1\n        }', '        thread_local! { static NETWORK_TEST_VERSION: std::cell::Cell<u64> = const { std::cell::Cell::new(1) }; }\n        pub fn resources_version() -> u64 { NETWORK_TEST_VERSION.with(|v| v.get()) }\n        pub fn network_test_atlas(layer: u32) {\n            let face = Face { sprite: Sprite { array: 0, layer, animated: false, cutout: false },\n                rect: (0.0, 0.0, 1.0, 1.0), inset: 0.0, uv: (0.0, 0.0, 1.0, 0.0, 0.0, 1.0),\n                tint: Tint::Grass, translucent: false };\n            let mut table = HashMap::new();\n            table.insert("minecraft:grass_block".to_owned(), BlockFaces { sides: vec![vec![face]; 6], particle: None, cube: true });\n            TABLE.with(|t| *t.borrow_mut() = Some(table));\n            NETWORK_TEST_VERSION.with(|v| v.set(v.get() + 1));\n        }')
    sdk.write_text(sdk_source, encoding="utf-8", newline="\n")
    hurricane = bundle / "engine/src/strike/hurricane.rs"
    hurricane.write_text(hurricane.read_text(encoding="utf-8") + "\n" + (RECIPE / "atlas_test.rs").read_text(encoding="utf-8"), encoding="utf-8", newline="\n")
    post = bundle / "post.rs"
    updated_post = adapt_post(post.read_text(encoding="utf-8"))
    manifest.write_text(source, encoding="utf-8", newline="\n")
    glue.write_text(updated_glue, encoding="utf-8", newline="\n")
    engine.write_text(updated_engine, encoding="utf-8", newline="\n")
    post.write_text(updated_post, encoding="utf-8", newline="\n")
    for name in ("visual_wire.rs", "visual.rs"):
        (bundle / name).write_bytes((RECIPE / name).read_bytes())
    (bundle / "wire.rs").write_bytes((RECIPE / "wire.rs").read_bytes())
    (bundle / "content_find.rs").unlink()
