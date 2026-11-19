"""Bind Orbital to the f00125f negotiated play transport."""

from pathlib import Path

from . import RECIPE, replace, section


EPOCH_TYPE = "std::sync::Arc<horizon_client::raw_payloads::connection::Epoch>"
HOOK_TARGET = "horizon_client::game::native_hooks::legacy::Game<'_>"


def adapt(bundle: Path, manifest: str, glue: str, visual: str) -> tuple[str, str, str]:
    return route_payloads(bundle, manifest), adapt_glue(glue), adapt_visual(visual)


def route_payloads(bundle: Path, manifest: str) -> str:
    manifest = replace(manifest, 'source = "wire.rs"\n', '''source = "wire.rs"

[[module]]
id = "payload"
file = "crates/horizon-client/src/game.rs"
name = "orbital_payload"
source = "negotiated_payload.rs"
''')
    for side in ("client", "server"):
        hook = f"net_from_{side}"
        start = manifest.index(f'id = "{hook}"')
        end = manifest.index("\n[[injection]]", start)
        injection = replace(manifest[start:end], f'hook = "{hook}"\nhook_version = 1',
                            f'target = "{HOOK_TARGET}::native_{hook}"')
        injection = replace(injection, '  { name = "data", ty = "&[u8]", value = "data" },\n',
                            '  { name = "data", ty = "&[u8]", value = "data" },\n'
                            f'  {{ name = "epoch", ty = "&{EPOCH_TYPE}", value = "epoch" }},\n')
        manifest = manifest[:start] + injection + manifest[end:]
        source = bundle / f"{hook}.rs"
        helper = replace(source.read_text(encoding="utf-8"), "data: &[u8])", f"data: &[u8], epoch: &{EPOCH_TYPE})")
        helper = replace(helper, "channel, data);", "channel, data, epoch);")
        source.write_text(helper, encoding="utf-8", newline="\n")
    (bundle / "negotiated_payload.rs").write_bytes((RECIPE / "negotiated_payload.rs").read_bytes())
    return manifest


def adapt_glue(glue: str) -> str:
    changes = (
        ("use super::Game;", "use super::{Game, orbital_payload as payload};\n"
                            "use horizon_client::raw_payloads::connection::Epoch;\nuse std::sync::Arc;"),
        ('const WORK: &str = "orbital:work";\nconst ANSWER: &str = "orbital:answer";',
         "use payload::{WORK, ANSWER};"),
        ("struct Client {\n", "struct Client {\n    payload_session: Option<payload::Session>,\n"),
        ("fn flush_work(game: &mut Game<'_>) {", "fn flush_work(game: &mut Game<'_>, session: &payload::Session) {"),
        ("game.native_net_send_to_server(WORK, &batch)", "payload::send_to_server(game, session, WORK, &batch)"),
        ('world work waiting: {e}', 'world work waiting: {e:?}'),
        ("pub(super) fn from_server(game: &mut Game<'_>, channel: &str, data: &[u8]) {",
         '''pub(super) fn from_server(game: &mut Game<'_>, channel: &str, data: &[u8], epoch: &Arc<Epoch>) {
    if data.len() > wire::MESSAGE_LIMIT
        || !sync_payload_session(game).is_some_and(|session| session.matches_epoch(epoch))
    {
        return;
    }'''),
        ("    work: BTreeMap<u128, VecDeque<Vec<u8>>>,", "    work: PendingPayloads,"),
        ("    answers: BTreeMap<u128, VecDeque<Vec<u8>>>,", "    answers: PendingPayloads,"),
        ("    warn_no_content(game);\n    let heard:",
         "    let payload_session = sync_payload_session(game);\n    warn_no_content(game);\n    let heard:"),
        ("    super::orbital_visual::tick_client(game);\n    flush_work(game);",
         "    if let Some(session) = &payload_session {\n"
         "        super::orbital_visual::tick_client(game, session);\n        flush_work(game, session);\n    }"),
        ("    let mut r = Reader(request);\n    match r.u8()? {",
         "    let conn = payload::operator_connection(game, session, dimension)?;\n"
         "    let mut r = Reader(request);\n    match r.u8()? {"),
        ("fn serve(game: &mut Game<'_>, player: u128, dimension: &str, request: &[u8]) -> Option<()> {",
         "fn serve(game: &mut Game<'_>, session: &payload::Session, dimension: &str, request: &[u8]) -> Option<()> {"),
        ("            let conn = game.lan.as_ref()?.hub.guests.iter()\n"
         "                .find(|guest| guest.placed && guest.uuid == player)?.conn;\n", ""),
        ("game.native_set_flying_speed(player, speed.is_finite().then_some(speed));",
         "game.native_set_flying_speed(session.uuid(), speed.is_finite().then_some(speed));"),
    )
    for before, after in changes:
        glue = replace(glue, before, after)
    if glue.count("answer(player, w.0);") != 2:
        raise ValueError("Both Orbital response producers must use the authenticated session")
    glue = glue.replace("answer(player, w.0);", "answer(session, w.0);")
    return section(glue, "/// The `net_from_client` hook:", "fn serve(game:",
                   (RECIPE / "payload_queues.rs").read_text(encoding="utf-8").rstrip() + "\n\n")


def adapt_visual(visual: str) -> str:
    changes = (
        ("use super::{Game, orbital_visual_wire::", "use super::{Game, orbital_payload as payload, orbital_visual_wire::"),
        ("    peers: BTreeMap<u128, (Receive, u64, String)>,\n",
         "    peers: BTreeMap<u128, (Receive, u64, String)>,\n"
         "    sessions: BTreeMap<u128, payload::Session>,\n"),
        ("pub(super) fn tick_client(game: &mut Game<'_>) {", "pub(super) fn tick_client(game: &mut Game<'_>, session: &payload::Session) {"),
        ("game.native_net_send_to_server(wire::CHANNEL, bytes)", "payload::send_to_server(game, session, wire::CHANNEL, bytes)"),
        ("game.native_net_send_to_server(wire::CHANNEL, &packet.encode())", "payload::send_to_server(game, session, wire::CHANNEL, &packet.encode())"),
        ("pub(super) fn from_client(game: &mut Game<'_>, player: u128, channel: &str, bytes: &[u8]) -> bool {",
         "pub(super) fn from_client(game: &mut Game<'_>, sender: &payload::Peer, channel: &str, bytes: &[u8]) -> bool {"),
        ("    let players = game.native_server_players();\n"
         "    let Some(actor) = players.iter().find(|p| p.uuid == player && p.permission >= 2) else { return true; };",
         "    let players = payload::server_players(game);\n"
         "    let Some(actor) = players.iter().find(|p| p.session == sender.session && p.permission >= 2) else { return true; };\n"
         "    let player = actor.uuid;"),
        ("        match &packet {\n", "        sync_server_sessions(&mut s, &players);\n        match &packet {\n"),
        ("    let players = game.native_server_players();\n    SERVER.with(|cell| {\n        let mut s = cell.borrow_mut();\n",
         "    let players = payload::server_players(game);\n    SERVER.with(|cell| {\n        let mut s = cell.borrow_mut();\n"
         "        sync_server_sessions(&mut s, &players);\n"),
    )
    for before, after in changes:
        visual = replace(visual, before, after)
    for before, after, count in (
        ("(super::native_net::PER_TICK / 2).max(1) as usize", "payload::PER_TICK / 2", 2),
        ("game.native_net_send_to_player(to, wire::CHANNEL, &bytes)",
         "payload::send_to_player(game, &recipient.session, wire::CHANNEL, &bytes)", 2),
        ("game.native_player().is_some()", "game.local_player().is_some()", 1),
        ("game.native_player().is_none()", "game.local_player().is_none()", 2),
        ("game.native_clock()", "game.clock()", 3),
    ):
        if visual.count(before) != count:
            raise ValueError(f"Orbital visual transport anchor changed: {before}")
        visual = visual.replace(before, after)
    return visual.rstrip() + "\n\n" + (RECIPE / "payload_visual_sessions.rs").read_text(encoding="utf-8")
