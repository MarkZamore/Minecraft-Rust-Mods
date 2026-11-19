"""Move ElytraTrails 0.1.1 colour synchronization onto Horizon's typed payloads."""

from hashlib import sha256
from pathlib import Path
import re


REVISION = "f00125f61f794aaa3d4e950d301f353b35b6ee3d"
SOURCE_HASHES = {
    "core.rs": "70478db211731135132551b8a521b980770b4cdabd7ceb1c313126379b0438e0",
    "horizon-mixin.toml": "3853438480f6d93667d7cc4968b3367187a201e25946a251447040a0329624d4",
    "net.rs": "1db56cf608df7c6d8048b2665eeefce35098ea92609a398ffd09564610ea1c0e",
    "net_from_client.rs": "56dad1316e3beb7f2da17ab528b51d1a6f59083cfee042ca9e804da8847b2714",
    "net_from_server.rs": "e38dec3c739e4b2e79e8c21c94f0390642dce76435c45e4e02d2ef949457848e",
}

PAYLOAD_IMPORTS = """use mod_api::{
    Hooks, IPayloadContext, PayloadCodec, PayloadId, Registrar, RegistrationContext, ToClient,
    ToServer,
};
use std::sync::OnceLock;
"""

TYPED_PAYLOADS = """#[derive(Clone, Copy, PayloadCodec)]
struct TrailStyle {
    colour: u8,
    pattern: u8,
}

#[derive(PayloadCodec)]
struct PlayerStyle {
    uuid_high: i64,
    uuid_low: i64,
    style: TrailStyle,
}

impl PlayerStyle {
    fn new(uuid: u128, (colour, pattern): Style) -> Self {
        Self {
            uuid_high: (uuid >> 64) as i64,
            uuid_low: uuid as i64,
            style: TrailStyle { colour, pattern },
        }
    }

    fn uuid(&self) -> u128 {
        (u128::from(self.uuid_high as u64) << 64) | u128::from(self.uuid_low as u64)
    }
}

#[derive(PayloadCodec)]
struct StyleTable {
    players: Vec<PlayerStyle>,
}

static SEND_STYLE: OnceLock<ToServer<TrailStyle>> = OnceLock::new();
static SEND_TABLE: OnceLock<ToClient<StyleTable>> = OnceLock::new();

fn snapshot() -> StyleTable {
    with_net(|net| StyleTable {
        players: net.server.iter().map(|(&uuid, &style)| PlayerStyle::new(uuid, style)).collect(),
    })
}

fn set_peer_style(uuid: u128, style: TrailStyle) -> Option<bool> {
    if !valid(style.colour, style.pattern) {
        return None;
    }
    let style = (style.colour, style.pattern);
    Some(with_net(|net| {
        let changed = net.server.insert(uuid, style) != Some(style);
        net.dirty |= changed;
        changed
    }))
}

fn from_client(style: TrailStyle, context: &mut dyn IPayloadContext) -> Result<(), String> {
    let Ok(player) = context.player() else { return Ok(()); };
    // The advertised style contains no player identifier. Only the currently
    // authenticated connection can select the server-side owner of this style.
    if set_peer_style(player.uuid(), style) == Some(false)
        && let Some(token) = SEND_TABLE.get()
    {
        let _ = token.reply(context, &snapshot());
    }
    Ok(())
}

fn from_server(table: StyleTable, _: &mut dyn IPayloadContext) -> Result<(), String> {
    let players = table.players.iter()
        .filter(|row| valid(row.style.colour, row.style.pattern))
        .map(|row| (row.uuid(), (row.style.colour, row.style.pattern)))
        .collect();
    with_net(|net| net.client = players);
    Ok(())
}

#[derive(Default)]
pub(super) struct ElytraHooks;

#[mod_api::horizon_mod]
impl Hooks for ElytraHooks {
    fn register(&self, registrar: &mut dyn Registrar, _: &RegistrationContext) {
        let mut event = registrar.payload_handlers();
        let mut payloads = event.registrar("1").optional();
        let style = payloads.play_to_server_typed::<TrailStyle>(
            PayloadId("elytratrails:color".into()),
            from_client,
        );
        let table = payloads.play_to_client_typed::<StyleTable>(
            PayloadId("elytratrails:color_table".into()),
            from_server,
        );
        let _ = SEND_STYLE.set(style);
        let _ = SEND_TABLE.set(table);
    }
}

"""


def replace_once(text: str, old: str, new: str) -> str:
    if text.count(old) != 1:
        raise ValueError(f"ElytraTrails network anchor changed: {old[:90]!r}")
    return text.replace(old, new)


def verified_source(bundle: Path, name: str) -> str:
    raw = (bundle / name).read_bytes()
    if sha256(raw).hexdigest() != SOURCE_HASHES[name]:
        raise ValueError(f"Unexpected ElytraTrails 0.1.1 input: {name}")
    return raw.decode("utf-8")


def apply(bundle: Path, *, revision: str) -> None:
    if revision != REVISION:
        return
    originals = {name: verified_source(bundle, name) for name in SOURCE_HASHES}
    manifest = originals["horizon-mixin.toml"]
    for name in ("net_from_client", "net_from_server"):
        pattern = rf'(?ms)^\[\[injection\]\]\nid = "{name}"\n.*?(?=^\[\[|\Z)'
        manifest, removed = re.subn(pattern, "", manifest)
        if removed != 1:
            raise ValueError(f"ElytraTrails legacy network injection changed: {name}")

    network = originals["net.rs"]
    network = replace_once(network, 'pub const CHANNEL: &str = "elytratrails:color";\nconst ENTRY: usize = 18;\n',
                           PAYLOAD_IMPORTS + "\n")
    start = network.index("fn encode(")
    end = network.index("pub fn style_of(", start)
    network = network[:start] + TYPED_PAYLOADS + network[end:]

    start = network.index("    pub(super) fn elytratrails_server_message(")
    end = network.index("    pub(super) fn elytratrails_net_tick(", start)
    network = network[:start] + """    pub(super) fn elytratrails_server_tick(&mut self) {
        let Some(token) = SEND_TABLE.get() else { return; };
        if !with_net(|net| std::mem::take(&mut net.dirty)) {
            return;
        }
        let table = snapshot();
        for player in self.native_server_players() {
            let _ = token.send(self, player, &table);
        }
    }

""" + network[end:]
    network = replace_once(network,
                           "        if send_now {\n            let _ = self.native_net_send_to_server(CHANNEL, &[style.0, style.1]);",
                           """        if send_now && let Some(token) = SEND_STYLE.get() {
            let _ = token.send(self, &TrailStyle { colour: style.0, pattern: style.1 });""")
    (bundle / "horizon-mixin.toml").write_text(manifest, encoding="utf-8", newline="\n")
    (bundle / "net.rs").write_text(network, encoding="utf-8", newline="\n")


def package_source(bundle: Path) -> str:
    """Return the actual registered types and handlers without injected Game methods."""
    colours = verified_source(bundle, "core.rs")
    palette = []
    for name in ("COLORS", "PATTERNS"):
        line = re.findall(rf"(?m)^pub const {name}: \[&str; \d+\] = \[.*\];$", colours)
        if len(line) != 1:
            raise ValueError(f"ElytraTrails {name} table changed")
        palette.append(line[0])
    network = (bundle / "net.rs").read_text(encoding="utf-8")
    if network.count(TYPED_PAYLOADS) != 1:
        raise ValueError("ElytraTrails package source does not match its registered typed adapter")
    registered = network[:network.index("pub fn style_of(")]
    registered = replace_once(registered, "use crate::game::Game;\n", "")
    registered = replace_once(registered, "use crate::game::official_elytratrails as m;\n",
                              "mod m {\n    " + "\n    ".join(palette) + "\n}\n")
    registered = replace_once(registered, "pub(super) struct ElytraHooks;", "pub struct ElytraHooks;")
    return registered
