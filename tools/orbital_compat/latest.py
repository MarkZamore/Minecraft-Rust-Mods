"""Adapt the verified 0.5.17 source without changing its sequenced LAN protocol."""

import hashlib
from pathlib import Path

from . import atlas_latest, payload_ingress, tsunami_terrain, worldeater_port
from . import (
    RECIPE, adapt_engine, adapt_hint_glue, adapt_post, adapt_typed_commands,
    adapt_visual_glue, add_wire_modules, replace, section,
)
from .payload_port import adapt_visual, route_payloads


SOURCE_HASHES = {
    "horizon-mixin.toml": "86dcd8bfa4a78567af5e531720d013f06e613cb315862f8e8e86a64bf3a5fa14",
    "glue.rs": "0b06d27895a0477ad2c7f3acd73f34ffd5ecb6b07bab156bacbfff99563ae305",
    "post.rs": "b8e16c7d4eed5e97fdf35a2b5c783f7f34bde53a6ac39913c8ef4e2117faa626",
    "content_find.rs": "fbae5ee17b3b203f6b88383dfa18c4cb0b4f7be86236254018e90fb9a843f12b",
    "carry.rs": "f2e38218db9a61ea388804e7508c48a1ede28939abd075a7fdb80952a072e4b1",
    "engine/src/lib.rs": "b68c65313a227ac7f8ea8dfdd02d8777e5d2b954a0acfbbb40dd153700ab69c9",
    "engine/src/sdk.rs": "d3674f305e1b81c2e5465c39b1f7f3ff2f011f20c6975a3ff7e974cae202a4e3",
    "engine/src/strike/hurricane.rs": "57e219531221503ff6d560d7b58b9659a6c1920f3ec87898f00df4e4b7a333b0",
    "net_from_client.rs": "50919c7fe411cbd6592cc60669827ed6877460be3915bb2c63d4508882002198",
    "net_from_server.rs": "28b82ae58efe0b62c4678d828c33d6b1fef70b389c381adfef946ad9b63ffa4a",
    "perform_command.rs": "86a22bd60e0d268141d40a4829e558f36cf828f63735ee518cbc1c52fd25f584",
    "register_commands.rs": "2e5b48730cf9a44809c89f057c651935417bb4bc58dbc637b056ea02cab9a843",
}
STREAMING_SOURCE = "crates/horizon-client-render/src/streaming.rs"
STREAMING_SHA256 = "862431a33595b177c1b686e7823577ae7a2e6b24e5d22b954f61261b07c5cfe9"
RENDER_SOURCE = "crates/horizon-client-render/src/gfx.rs"
RENDER_SHA256 = (
    "5ef5342415fa2bde785a86228a9abe53bf3c9d275363dbbc34a2249f68ba9c46",
    "7a3c778bf7298719ba45301efb5e6168f1ff8790136e81747ecf31293d2fa9eb",
)
MOTION_SOURCE = "crates/horizon-client/src/motion.rs"
MOTION_SHA256 = "928ceded5872044e10f569ab33c967fd12955ad665b6d753cf49b98d31e59f36"


def read(name: str) -> str:
    return (RECIPE / name).read_text(encoding="utf-8")


def write(path: Path, source: str) -> None:
    path.write_text(source, encoding="utf-8", newline="\n")


def checked_source(path: Path, pins: tuple[str, ...]) -> str:
    if path.is_symlink() or path.is_junction():
        raise ValueError(f"Linked Orbital Laser source: {path}")
    raw = path.read_bytes()
    if hashlib.sha256(raw).hexdigest() not in pins:
        raise ValueError(f"Orbital Laser 0.5.17 host source changed: {path}")
    return raw.decode("utf-8")


def prepare_source(stage: Path) -> None:
    path = stage / STREAMING_SOURCE
    source = checked_source(path, (STREAMING_SHA256,))
    render_path = stage / RENDER_SOURCE
    render = checked_source(render_path, RENDER_SHA256)
    motion_path = stage / MOTION_SOURCE
    motion = checked_source(motion_path, (MOTION_SHA256,))
    payload_ingress.prepare_source(stage)
    for before, after in (
        ("    mod_tickets: ModTickets,", "    mod_tickets: ModTickets,\n"
         "    transient_forced: FxHashMap<(i32, i32), FxHashSet<&'static str>>,"),
        ("            mod_tickets: ModTickets::default(),", "            mod_tickets: ModTickets::default(),\n"
         "            transient_forced: FxHashMap::default(),"),
        ("        let held: Vec<_> = self.commanded.iter().copied().collect();",
         "        let held: Vec<_> = self.commanded.iter().chain(self.transient_forced.keys()).copied().collect();"),
        ("    /// Bring `forced` in line with who holds `chunk`:",
         read("temporary_chunks.rs") + "    /// Bring `forced` in line with who holds `chunk`:"),
        ("        if self.commanded.contains(&chunk) || self.mod_tickets.holds(chunk) {",
         "        if self.commanded.contains(&chunk) || self.mod_tickets.holds(chunk)\n"
         "            || self.transient_forced.contains_key(&chunk)\n        {"),
    ):
        source = replace(source, before, after)
    render = replace(render, "pub struct Frame<'a> {", '''fn legacy_orbital_frame(frame: &Frame<'_>) {
    let _ = frame;
}

pub struct Frame<'a> {''')
    render = replace(render, "        debug_assert_eq!(output.encoding, self.hdr.as_ref().map(|hdr| hdr.output));",
                     "        legacy_orbital_frame(frame);\n"
                     "        debug_assert_eq!(output.encoding, self.hdr.as_ref().map(|hdr| hdr.output));")
    motion = replace(motion, "    ) -> Result<()> {\n        self.local_tick(keys, |player, keys| {\n"
                     "            player.sprint_input_tick_with_window(keys, sprint_window)",
                     "    ) -> Result<()> {\n        self.legacy_orbital_local_tick(keys, sprint_window)\n    }\n\n"
                     "    fn legacy_orbital_local_tick(\n        &mut self,\n        keys: crate::sprint::Keys,\n"
                     "        sprint_window: i32,\n    ) -> Result<()> {\n        self.local_tick(keys, |player, keys| {\n"
                     "            player.sprint_input_tick_with_window(keys, sprint_window)")
    write(path, source)
    write(render_path, render)
    write(motion_path, motion)


def apply(bundle: Path) -> None:
    for name, expected in SOURCE_HASHES.items():
        if hashlib.sha256((bundle / name).read_bytes()).hexdigest() != expected:
            raise ValueError(f"Unexpected Orbital Laser 0.5.17 source: {name}")
    worldeater = worldeater_port.changes(bundle)
    manifest = (bundle / "horizon-mixin.toml").read_text(encoding="utf-8")
    # The same checked content is already present as an ordinary orbital.hmod.
    manifest = section(manifest, "# One file for code and content:", "# The Fold's world warp", "")
    manifest = worldeater_port.transform_manifest(manifest)
    manifest = replace(manifest, 'hook = "perform_command"', 'hook = "perform_console_command"')
    manifest = add_wire_modules(manifest)
    manifest = replace(manifest, 'source = "wire.rs"\n', '''source = "wire.rs"

[[module]]
id = "world"
file = "crates/horizon-client/src/game.rs"
name = "orbital_world"
source = "latest_world.rs"

[[module]]
id = "chunk_tickets"
file = "crates/horizon-client/src/game.rs"
name = "orbital_chunk_tickets"
source = "chunk_tickets.rs"

[[module]]
id = "column_reply"
file = "crates/horizon-client/src/game.rs"
name = "orbital_column_reply"
source = "column_reply.rs"
''')
    glue = (bundle / "glue.rs").read_text(encoding="utf-8")
    glue = worldeater_port.transform_glue(glue)
    glue = adapt_client(adapt_hint_glue(adapt_work(glue)))
    manifest, glue = adapt_typed_commands(bundle, manifest, glue, sequenced=True)
    manifest = route_payloads(bundle, manifest)
    manifest = route_client_and_level_hooks(bundle, manifest)
    manifest = route_render_and_motion(manifest)
    carry = bundle / "carry.rs"
    write(carry, replace(carry.read_text(encoding="utf-8"), "    world.move_self(delta)?;",
                         "    let mut gate = horizon_world::move_gate::MoveGate::new();\n"
                         "    world.move_self(&mut gate, delta)?;"))
    post = adapt_post(worldeater_port.transform_post((bundle / "post.rs").read_text(encoding="utf-8")),
                      native_0517=True)
    post = replace(post, "/// HEAD of `Renderer::render`:", "/// HEAD of `Renderer::draw_frame`:")
    adapt_engine(bundle, engine_source=worldeater.pop(worldeater_port.ENGINE))
    for name, content in worldeater.items():
        write(bundle / name, content)
    atlas_latest.apply(bundle)
    tsunami_terrain.apply(bundle)
    write(bundle / "horizon-mixin.toml", manifest)
    write(bundle / "glue.rs", glue)
    write(bundle / "post.rs", post)
    write(bundle / "latest_world.rs", read("latest_world.rs"))
    write(bundle / "chunk_tickets.rs", read("chunk_tickets.rs"))
    write(bundle / "column_reply.rs", read("column_reply.rs"))
    write(bundle / "wire.rs", read("wire.rs") + "\n" + read("sequenced_wire.rs"))
    write(bundle / "visual.rs", adapt_visual(read("visual.rs")))
    write(bundle / "visual_wire.rs", replace(read("visual_wire.rs"), "SHADERS: u8 = 10", "SHADERS: u8 = 19"))
    (bundle / "content_find.rs").unlink()


def adapt_work(glue: str) -> str:
    glue = section(glue, "struct Reader<'a>", "fn send(message", "")
    # Install the validated read-side decoder first so the existing visual
    # relay can inject into the same entry point as with 0.5.9.
    glue = section(glue, "/// The `net_from_server` hook:", "// ---- the server half", read("answers.rs") + "\n")
    glue = adapt_visual_glue(glue)
    glue = section(glue, "/// The `net_from_client` hook:", "/// What holds the chunks", latest_queues() + "\n")
    for before, after in (
        ("use super::Game;", "use super::{Game, orbital_payload as payload};\n"
         "use super::orbital_wire::{self as wire, READ_COLUMNS, Reader};\n"
         "use horizon_client::{chunk::ColumnLayout, raw_payloads::connection::Epoch};\nuse std::sync::Arc;"),
        ("use super::native_world_columns::NativeColumnBlocks;\n"
         "use super::native_world_edit::{NativeBlockEdit, NativeBlockRun, NativeHeightmap};",
         "use super::orbital_world::{self as world_adapter, WireEdit as NativeBlockEdit, WireColumns as NativeColumnBlocks};\n"
         "use mod_api::{BlockRun as NativeBlockRun, Heightmap as NativeHeightmap};"),
        ('const WORK: &str = "orbital:work";\nconst ANSWER: &str = "orbital:answer";', "use payload::{WORK, ANSWER};"),
        ("const READ_COLUMNS: usize = 3000;", "const MAX_HEIGHT: i32 = ColumnLayout::HIGHEST_Y + 1 - ColumnLayout::LOWEST_Y;"),
        ("struct Client {\n", "struct Client {\n    payload_session: Option<payload::Session>,\n"),
        ("    surface: BTreeMap<u32, (u32, Vec<Option<i32>>, Vec<bool>)>,",
         "    pending_surface: BTreeMap<u32, usize>,\n    pending_columns: BTreeMap<u32, (usize, usize)>,\n"
         "    surface: BTreeMap<u32, (Vec<Option<i32>>, Vec<bool>)>,"),
        ("    total: usize,\n    got: usize,\n    span: usize,", "    got: usize,\n    seen: Vec<bool>,"),
        ("    work: BTreeMap<u128, VecDeque<Vec<u8>>>,", "    work: PendingPayloads,"),
        ("    answers: BTreeMap<u128, VecDeque<Vec<u8>>>,", "    answers: PendingPayloads,"),
        ("    next: BTreeMap<u128, (u32, u32)>,", "    sequence: BTreeMap<u128, (payload::Session, u32, u32)>,"),
        ("    acks: BTreeMap<u128, (u32, u32)>,", "    acks: BTreeMap<u128, (payload::Session, (u32, u32))>,"),
        ("    edits: Vec<NativeBlockEdit>,", "    edits: Vec<(payload::Session, NativeBlockEdit)>,"),
        ("fn edit_or_wait(game: &mut Game<'_>, dimension: &str, edit: NativeBlockEdit) {",
         "fn edit_or_wait(game: &mut Game<'_>, session: &payload::Session, dimension: &str, edit: NativeBlockEdit) {"),
        ("                .push(part)", "                .push((session.clone(), part))"),
        ("game.native_chunk_is_loaded(dimension, [cx, cz])", "world_adapter::chunk_loaded(game, dimension, [cx, cz])"),
        ("game.native_chunk_is_loaded(dimension, [cx + dx, cz + dz])", "world_adapter::chunk_loaded(game, dimension, [cx + dx, cz + dz])"),
        ("game.native_edit_blocks(dimension, &edit)", "world_adapter::edit_blocks(game, dimension, &edit)"),
        ("game.native_edit_blocks(dimension, e)", "world_adapter::edit_blocks(game, dimension, e)"),
        ("            for e in &w.edits {", "            for (session, e) in &w.edits {\n"
         "                if payload::operator_connection(game, session, dimension).is_none() { continue; }"),
        ("fn flush_work(game: &mut Game<'_>) {", "fn flush_work(game: &mut Game<'_>, session: &payload::Session) {"),
        ("game.native_net_send_to_server(WORK, &c.unacked[i].1)", "payload::send_to_server(game, session, WORK, &c.unacked[i].1)"),
        ("game.native_net_send_to_server(WORK, &batch)", "payload::send_to_server(game, session, WORK, &batch)"),
        ("world work waiting: {e}", "world work waiting: {e:?}"),
        ("pub(super) fn from_server(game: &mut Game<'_>, channel: &str, data: &[u8]) {",
         "pub(super) fn from_server(game: &mut Game<'_>, channel: &str, data: &[u8], epoch: &Arc<Epoch>) {\n"
         "    let Some(session) = sync_payload_session(game).filter(|session| session.matches_epoch(epoch)) else { return; };"),
        ("super::orbital_visual::from_server(game, channel, data)",
         "super::orbital_visual::from_server(game, &session, channel, data)"),
        ("        let (tag, offset, total) =", "        if kind == 12 {\n"
         "            if let Some(&first) = c.unacked.front() {\n"
         "                let gone = wire::acknowledged(data, c.epoch, first, c.unacked.len())?;\n"
         "                if gone > 0 {\n"
         "                    c.unacked.drain(..gone);\n"
         "                    c.progress = Some(std::time::Instant::now());\n"
         "                }\n"
         "            }\n"
         "            return Some(());\n"
         "        }\n"
         "        let (tag, offset, total) ="),
        ("    warn_no_content(game);\n    let heard:", "    let payload_session = sync_payload_session(game);\n    warn_no_content(game);\n    let heard:"),
        ("    super::orbital_visual::tick_client(game);\n    flush_work(game);",
         "    if let Some(session) = &payload_session {\n"
         "        super::orbital_visual::tick_client(game, session);\n        flush_work(game, session);\n    }"),
        ("            c.surface.clear();\n            c.columns.clear();\n        });\n        horizon_client::orbital_carry::reset();",
         "            c.pending_surface.clear();\n            c.pending_columns.clear();\n"
         "            c.surface.clear();\n            c.columns.clear();\n        });\n        horizon_client::orbital_carry::reset();"),
        ("/// The `perform_command` hook:", "/// The `perform_console_command` hook:"),
    ):
        glue = replace(glue, before, after)
    return adapt_work_transport(adapt_requests(adapt_ticket_tracking(glue)))


def adapt_work_transport(glue: str) -> str:
    glue = section(glue, "/// Batches of world work out at once", "\nthread_local!", '''/// Batches already accepted by the reliable play stream but not yet applied.
const WINDOW: usize = 30;
/// Report stalled acknowledgements without submitting duplicate packets.
const WORK_DIAGNOSTIC_AFTER: std::time::Duration = std::time::Duration::from_secs(4);
''')
    glue = section(glue, "    /// Batches sent and not yet acknowledged", "    next_seq: u32,",
                   "    /// Accepted batch numbers in order; only unsent work retains its bytes.\n"
                   "    unacked: VecDeque<u32>,\n")
    glue = replace(glue, "            c.resend = 0;\n", "")
    glue = replace(glue, "            c.unacked.push_back((c.next_seq, batch));",
                   "            c.unacked.push_back(c.next_seq);")
    glue = section(glue, "/// The client half: the work waiting", "fn flush_work(game:",
                   "/// Submit queued work in order, with no more than WINDOW accepted batches.\n")
    glue = section(glue, "        if c.unacked.is_empty() {", "        // (how far the world work", '''        if let Some((first, _)) = c.unacked.front() {
            if c.progress.is_some_and(|t| now.duration_since(t) > WORK_DIAGNOSTIC_AFTER) {
                log::info!("[mod {ID}] world work: no ACK progress for {} s, batches {}..{} outstanding, {} requests waiting",
                    WORK_DIAGNOSTIC_AFTER.as_secs(), first, c.next_seq, c.out.len());
                c.progress = Some(now);
            }
        } else {
            c.progress = Some(now);
        }
''')
    glue = replace(glue, "        if let Some((first, _)) = c.unacked.front() {", "        if let Some(&first) = c.unacked.front() {")
    glue = replace(glue, "    let mut r = Reader(data);\n    let done = CLIENT.with", '''    let unpacked;
    let data = if data.first() == Some(&super::orbital_column_reply::COMPRESSED_COLUMNS) {
        let Some(decoded) = super::orbital_column_reply::unpack(data) else {
            log::warn!("[mod {ID}] a bad compressed column answer from the server half");
            return;
        };
        unpacked = decoded;
        &unpacked
    } else {
        data
    };
    let mut r = Reader(data);
    let done = CLIENT.with''')
    glue = section(glue, "            let palette_bytes: usize = read.palette", "\n        }\n        4 =>",
                   "            answer_columns(session, tag, offset, total, span, &read, columns.len());")
    return glue.rstrip() + "\n\nuse super::orbital_column_reply;\n\n" + read("column_replies_host.rs")


def adapt_ticket_tracking(glue: str) -> str:
    for before, after in (
        ("use super::{Game, orbital_payload as payload};",
         "use super::{Game, orbital_payload as payload, orbital_chunk_tickets::{ChunkKey, ChunkTickets}};"),
        ("    waiting: BTreeMap<(String, i32, i32), Waiting>,",
         "    waiting: BTreeMap<ChunkKey, Waiting>,\n    tickets: ChunkTickets<payload::Session>,"),
        ("/// A chunk's edits waiting for it, since when, and whether its ticket goes once they are in.",
         "/// A chunk's queued edits and how long they have waited."),
        ("    release: bool,\n", ""),
        ("edits: Vec::new(), release: false", "edits: Vec::new()"),
        ("fn edits_waiting(game: &mut Game<'_>, dimension: &str) {", '''fn edits_waiting(game: &mut Game<'_>, dimension: &str, players: &[payload::Peer]) {
    let live = |session: &payload::Session| players.iter().any(|peer| {
        peer.permission >= 2 && peer.dimension == dimension && peer.session == *session
    });
    let released = SERVER.with(|server| {
        let Server { tickets, waiting, .. } = &mut *server.borrow_mut();
        waiting.retain(|key, queued| {
            if key.0 != dimension { return true; }
            queued.edits.retain(|(session, _)| live(session));
            !queued.edits.is_empty()
        });
        tickets.prune(dimension, live, |key| waiting.contains_key(key))
    });
    for chunk in released {
        world_adapter::force_chunk(game, TICKET, dimension, chunk, false);
    }'''),
        ("        if w.release {\n            game.native_force_chunk_in(TICKET, dimension, [key.1, key.2], false);",
         "        if SERVER.with(|server| server.borrow_mut().tickets.finish_waiting(&key)) {\n"
         "            world_adapter::force_chunk(game, TICKET, dimension, [key.1, key.2], false);"),
        ("fn force_load(game: &mut Game<'_>, dimension: &str, line: &str) -> bool {",
         "fn force_load(game: &mut Game<'_>, session: &payload::Session, dimension: &str, line: &str) -> bool {"),
        ("if force_load(game, dimension, line) {", "if force_load(game, session, dimension, line) {"),
    ):
        glue = replace(glue, before, after)
    return section(glue, "            // (a chunk whose edits still wait for it", "\n        }\n    }\n    true",
                   '''            let key = (dim.to_owned(), cx, cz);
            let changed = SERVER.with(|server| {
                let Server { tickets, waiting, .. } = &mut *server.borrow_mut();
                if hold {
                    tickets.acquire(key, session)
                } else {
                    tickets.release(&key, session, waiting.contains_key(&key))
                }
            });
            if changed {
                world_adapter::force_chunk(game, TICKET, dim, [cx, cz], hold);
            }''')


def adapt_client(glue: str) -> str:
    for before, after, count in (
        ("game.native_block_sprites_version()", "game.block_sprites_version()", 1),
        ("    let textures = context.clone();", "    let textures = context.clone();\n"
         "    let layer_names = game.pack.layer_names.clone();\n    let texture_names = layer_names.clone();", 1),
        ("Lookup::new(&context, split)", "Lookup::new(&context, split, &layer_names)", 1),
        ("Lookup::new(&textures, split)", "Lookup::new(&textures, split, &texture_names)", 1),
        ("            Some(TintSource::Fixed(c)) => atlas::Tint::Fixed(c),",
         "            Some(TintSource::Fixed(c)) => atlas::Tint::Fixed(c),\n            Some(_) => atlas::Tint::None,", 1),
        ("game.native_clock()", "game.clock()", 2),
        ("horizon_client::hud::HudBatch::new(size)",
         "horizon_client::hud::HudBatch::new(size, game.settings.gui_scale_inputs())", 1),
        ("game.native_cursor()", "game.cursor()", 1),
        ("game.native_mod_screen()", "game.mod_screen()", 3),
        ("game.native_mod_screen_open(ID)", "game.mod_screen_open(ID)", 1),
        ("game.native_mod_screen_close(ID)", "game.mod_screen_close(ID)", 1),
        ("game.native_claim_items(ID, &claims)", "game.claim_items(ID, &claims)", 1),
        ("game.native_push_player(velocity)", "game.push_player(velocity)", 1),
        ("game.native_sprite_size(name)", "game.gui.atlas.sprites.get(name).map(|rect| [rect.w as f32, rect.h as f32])", 1),
        ("[0.0, 0.0, w, h], [cx + r.x, cy + r.y, r.w, r.h]);",
         "[0.0, 0.0, w, h], [cx + r.x, cy + r.y, r.w, r.h], None);", 1),
        ("    if !game.native_window() {\n        return from_owner(game, line);\n    }\n", "", 1),
        ("game.native_held().map(|(id, count, _)| input::HeldItem {\n            id,\n            count,",
         "game.inventory.get(game.selected).and_then(Option::as_ref).map(|stack| input::HeldItem {\n"
         "            id: stack.item.to_string(),\n            count: stack.count,", 1),
        ("game.native_view_hit().map(|(d, h)| (d, hit(h)))", "game.view_hit().map(|(d, h)| (d, hit(game, h)))", 1),
        ("use super::native_hooks::{NativeScreenView, NativeShake};", "use mod_api::{ScreenView, game::Shake as NativeShake};", 1),
        ("NativeScreenView<'_>", "ScreenView<'_>", 5),
        ("use super::native_claimed_items::{NativeHeldItem, NativeHit, NativeModEvent};", "use mod_api::game::{HeldItemSlot, ModEvent};", 1),
        ("fn held(h: NativeHeldItem)", "fn held(game: &Game<'_>, h: HeldItemSlot)", 1),
        ("        id: h.id,", "        id: game.item_key_id(h.id),", 1),
        ("fn hit(h: NativeHit)", "fn hit(game: &Game<'_>, h: mod_api::Hit)", 1),
        ("        block: h.block,", "        block: game.block_key_id(h.block),", 1),
        (".native_poll(ID)", ".poll(ID)", 1),
        ("NativeModEvent::", "ModEvent::", 2),
        ("item: held(item)", "item: held(game, item)", 2),
        ("        .map(|e| match e {\n            ModEvent::", "        .filter_map(|e| Some(match e {\n            ModEvent::", 1),
        ("ModEvent::UseStop(item, ticks) => input::Event::UseStop(input::ItemUse { item: held(game, item), ticks }),\n        })",
         "ModEvent::UseStop(item, ticks) => input::Event::UseStop(input::ItemUse { item: held(game, item), ticks }),\n"
         "            _ => return None,\n        }))", 1),
        ("0xFF5555, true)", "0xFFFF5555, true)", 1),
        ("[x, cy + at.y], *c, true)", "[x, cy + at.y], *c | 0xFF00_0000, true)", 1),
    ):
        if glue.count(before) != count:
            raise ValueError(f"Orbital Laser client source anchor changed: {before}")
        glue = glue.replace(before, after)
    return glue


def route_client_and_level_hooks(bundle: Path, manifest: str) -> str:
    target = "horizon_client::game::native_hooks::base::Game<'_>"
    for hook in (
        "client_level_load", "client_level_unload", "camera_shake", "level_tick_post",
        "screen_render_post", "screen_mouse_button_pressed_pre", "screen_key_pressed_pre", "screen_char_typed_pre",
    ):
        manifest = replace(manifest, f'hook = "{hook}"\nhook_version = 1', f'target = "{target}::native_{hook}"')
    manifest = replace(manifest, 'helper = "screen_render_post.rs"\nbindings = [\n'
        '  { name = "game", ty = "&crate::game::Game<\'_>", value = "self" },\n'
        '  { name = "batch", ty = "&mut horizon_client::hud::HudBatch", value = "&mut *batch" },',
        'helper = "screen_render_post.rs"\nbindings = [\n'
        '  { name = "game", ty = "&crate::game::Game<\'_>", value = "self" },\n'
        '  { name = "batch", ty = "&mut horizon_client::hud::HudBatch", value = "&mut *draw" },')
    manifest = section(manifest, 'helper = "level_tick_post.rs"', "\n[[injection]]", '''helper = "level_tick_post.rs"
bindings = [
  { name = "game", ty = "&mut crate::game::Game<'_>", value = "&mut *self" },
  { name = "level", ty = "mod_api::LevelRef", value = "level" },
]
''')
    for old, new, count in (("crate::game::native_hooks::NativeScreenView", "mod_api::ScreenView", 4),
                             ("crate::game::native_hooks::NativeShake", "mod_api::game::Shake", 1)):
        if manifest.count(old) != count:
            raise ValueError(f"Orbital Laser expected hook type {old}")
        manifest = manifest.replace(old, new)
    for helper in ("screen_render_post", "screen_click", "screen_key", "screen_char", "camera_shake", "level_tick_post"):
        path = bundle / f"{helper}.rs"
        source = path.read_text(encoding="utf-8")
        if helper == "camera_shake":
            source = replace(source, "crate::game::native_hooks::NativeShake", "mod_api::game::Shake")
        elif helper == "level_tick_post":
            source = replace(source, "dimension: &str", "level: mod_api::LevelRef")
            source = replace(source, "game, dimension);", "game, level.dimension_id());")
        else:
            source = replace(source, "crate::game::native_hooks::NativeScreenView", "mod_api::ScreenView")
        write(path, source)
    return manifest


def route_render_and_motion(manifest: str) -> str:
    # Keep the game's own replaceable methods and renderer cfg branches. The
    # two compatibility callbacks run at their original execution boundaries.
    manifest = replace(manifest, 'target = "horizon_client_render::gfx::Renderer::render"',
                       'target = "horizon_client_render::gfx::legacy_orbital_frame"')
    return replace(manifest, '::controlled_simulation_tick_with_sprint_window"', '::legacy_orbital_local_tick"')


def latest_queues() -> str:
    queues = read("payload_queues.rs")
    queues = replace(queues, "            client.out.clear();", "            client.out.clear();\n"
                     "            client.unacked.clear();\n"
                     "            client.next_seq = 0;\n            client.epoch = new_epoch();\n"
                     "            client.progress = None;\n            client.backlog = (0, None);\n"
                     "            client.fly_sent = None;")
    queues = replace(queues, "    let players = payload::server_players(game);",
                     "    let players = payload::server_players(game);\n    edits_waiting(game, dimension, &players);")
    queues = replace(queues, "        server.answers.retain(|_, (session, _)| live(session));",
                     "        server.answers.retain(|_, (session, _)| live(session));\n"
                     "        server.sequence.retain(|_, (session, _, _)| {\n"
                     "            players.iter().any(|peer| peer.session == *session)\n"
                     "        });\n"
                     "        server.acks.retain(|_, (session, _)| live(session));")
    queues = section(queues, "        for batch in work.1 {", "    // Visual state", "        for bytes in work.1 {\n"
                     "            serve_batch(game, &work.0, dimension, &bytes);\n        }\n    }\n"
                     "    flush_acks(game, &players);\n")
    return queues + "\n" + read("latest_queues.rs")


def adapt_requests(glue: str) -> str:
    for before, after in (
        ("    for (i, part) in columns.chunks(READ_COLUMNS).enumerate() {", "    if columns.len() > wire::QUERY_COLUMNS { return; }\n"
         "    CLIENT.with(|c| {\n        let mut c = c.borrow_mut();\n        c.surface.remove(&tag);\n"
         "        c.pending_surface.insert(tag, columns.len());\n    });\n"
         "    for (i, part) in columns.chunks(READ_COLUMNS).enumerate() {"),
        ("    let parts: Vec<&[(i32, i32)]>", "    let Some(span) = wire::span(columns.len(), below, above, MAX_HEIGHT) else { return; };\n"
         "    if columns.len() > wire::QUERY_COLUMNS { return; }\n"
         "    CLIENT.with(|c| {\n        let mut c = c.borrow_mut();\n        c.columns.remove(&tag);\n"
         "        c.pending_columns.insert(tag, (columns.len(), span));\n    });\n"
         "    let parts: Vec<&[(i32, i32)]>"),
        ("fn serve(game: &mut Game<'_>, player: u128, dimension: &str, request: &[u8]) -> Option<()> {",
         "fn serve(game: &mut Game<'_>, session: &payload::Session, dimension: &str, request: &[u8]) -> Option<()> {\n"
         "    let conn = payload::operator_connection(game, session, dimension)?;"),
        ("            let who = uuid(player);\n            for line in r.strs()? {",
         "            let lines = r.strs()?;\n            if !r.0.is_empty() { return None; }\n            for line in lines {"),
        ('                game.native_run_command(&format!("execute as {who} at @s run {line}"));',
         "                game.run_quiet_line(conn, line);"),
        ("            let n = r.u32()? as usize;\n            let mut runs = Vec::with_capacity(n);",
         "            let n = r.u32()? as usize;\n"
         "            let (header, width) = if kind == 5 { (8, 13) } else { (0, 25) };\n"
         "            if r.0.len() != n.checked_mul(width)?.checked_add(header)? { return None; }\n"
         "            let mut runs = Vec::with_capacity(n);"),
        ("            edit_or_wait(game, dimension, NativeBlockEdit", "            edit_or_wait(game, session, dimension, NativeBlockEdit"),
        ("            let columns = r.columns()?;\n            let heights = game",
         "            let columns = r.columns()?;\n            wire::fragment(total as usize, offset as usize, columns.len())?;\n"
         "            if !r.0.is_empty() { return None; }\n            let heights = game"),
        (".native_surface_heights(dimension, &columns, heightmap_of(h)?)", ".surface_heights(dimension, &columns, heightmap_of(h)?)"),
        ("            let read = game\n                .native_read_columns(dimension, &columns, heightmap_of(h)?, (below, above))",
         "            let read = world_adapter::read_columns(game, dimension, &columns, heightmap_of(h)?, (below, above))"),
        ("            let span = (below + above) as usize;", "            wire::fragment(total as usize, offset as usize, columns.len())?;\n"
         "            let span = wire::span(total as usize, below, above, MAX_HEIGHT)?;\n"
         "            if !r.0.is_empty() { return None; }"),
        ("            for start in (0..columns.len()).step_by(per) {", "            for start in (0..columns.len().max(1)).step_by(per) {"),
        ("            for (k, part) in heights.chunks(MESSAGE / 5).enumerate() {", "            let pieces: Vec<_> = if heights.is_empty() { vec![&[][..]] } else { heights.chunks(MESSAGE / 5).collect() };\n"
         "            for (k, part) in pieces.into_iter().enumerate() {"),
        ("            game.native_set_flying_speed(player, speed.is_finite().then_some(speed));", "            if !r.0.is_empty() { return None; }\n"
         "            world_adapter::set_flying_speed(game, session.uuid(), speed);"),
    ):
        glue = replace(glue, before, after)
    if glue.count("answer(player, w.0);") != 2:
        raise ValueError("Both Orbital Laser replies must carry the originating session")
    glue = glue.replace("answer(player, w.0);", "answer(session, w.0);")
    notice = r'Install \"Orbital Laser (content)\" from the launcher\'s Mods page.'.replace("\\'", "'")
    if glue.count(notice) != 2:
        raise ValueError("Orbital Laser content error text changed")
    return glue.replace(notice, "Repair Minecraft Rust Mods to restore its Orbital Laser content.")
