// Replicate only the author's completed visual outputs. Observers never run a
// second world-mutating engine, and each actor keeps an independent render pass.
use super::{Game, orbital_visual_wire::{self as wire, Packet, Meta, Snapshot, Receive, Send}};
use horizon_client_render::orbital_post::Pass;
use std::{cell::RefCell, collections::{BTreeMap, BTreeSet, VecDeque}, sync::Arc};

const ID: &str = "orbital";
// Transient sound notifications, not effect data; backpressure never grows a
// per-recipient queue beyond 256 small events. Old sound events expire in 5 sec.
const EVENTS: usize = 256;
const STALE: u64 = 200;

#[derive(Default)]
struct Client {
    generation: u64,
    source: Option<(u8, u64)>,
    // The author detects world changes by clock, so a portal can leave its
    // previous pass alive. Never advertise that old pass in the new dimension.
    blocked_source: Option<u64>,
    latest: Option<Snapshot>,
    send: Send,
    retry: Option<(Packet, Send)>,
    events: VecDeque<(u64, Vec<u8>)>,
    peers: BTreeMap<u128, (Receive, u64)>,
    logged: BTreeMap<u128, u64>,
    playing: BTreeMap<u128, BTreeSet<String>>,
    own_sounds: BTreeSet<String>,
    upload: BTreeMap<u128, (((u64, u8, u64, u32), u64), u64, Arc<[u32]>)>,
    next_upload: u64,
}
#[derive(Default)]
struct Server {
    peers: BTreeMap<u128, (Receive, u64, String)>,
    send: BTreeMap<(u128, u128), Send>,
    retry: BTreeMap<(u128, u128), (Vec<u8>, Send)>,
    events: BTreeMap<u128, VecDeque<(u64, u128, String, Vec<u8>)>>,
    round: BTreeMap<u128, usize>,
}
thread_local! {
    static CLIENT: RefCell<Client> = RefCell::new(Client::default());
    static SERVER: RefCell<Server> = RefCell::new(Server::default());
}

fn clear_snapshot(generation: u64, clock: i64) -> Snapshot {
    Snapshot { meta: Meta { generation, shader: u8::MAX, params: [0.0; 16],
        revision: 0, words: 0, clock }, data: Arc::from([]) }
}

/// Called after the SDK has produced the local pass. Warm-up and title-screen
/// passes contain no strike intensity and do not advertise an active effect.
pub(super) fn capture(game: &Game<'_>, pass: Option<&Pass>) {
    let clock = game.native_clock().map_or(0, |c| c.game_time);
    let visible = pass.filter(|p| p.params[9] != 0.0 && game.native_player().is_some());
    CLIENT.with(|cell| {
        let mut c = cell.borrow_mut();
        match visible {
            Some(p) => {
                // A quality rebuild can change WGSL while the old strike is
                // still alive. Only an actual clear/invisible pass ends this
                // block; the author's next legitimate strike follows that clear.
                if c.blocked_source.is_some() {
                    c.latest = Some(clear_snapshot(c.generation, clock));
                    return;
                }
                let shader = c.source.filter(|(_, version)| *version == p.version).map(|(shader, _)| shader)
                    .or_else(|| orbital_laser::network_pass_index(&p.source));
                let Some(shader) = shader else { return; };
                if c.source != Some((shader, p.version)) {
                    c.generation = c.generation.wrapping_add(1); c.source = Some((shader, p.version));
                }
                let (data, revision) = p.data.as_ref().map_or_else(|| (Arc::from([]), 0), |(d, v)| (d.clone(), *v));
                c.latest = Some(Snapshot { meta: Meta { generation: c.generation, shader, params: p.params,
                    revision, words: data.len() as u32, clock }, data });
            }
            None => {
                c.blocked_source = None;
                if c.source.take().is_some() { c.generation = c.generation.wrapping_add(1); }
                c.latest = Some(clear_snapshot(c.generation, clock));
            }
        }
    });
}

pub(super) fn sound(game: &Game<'_>, event: &str, at: Option<[f64; 3]>, volume: f32, pitch: f32) {
    if event.starts_with("orbital:") {
        CLIENT.with(|c| { c.borrow_mut().own_sounds.insert(event.to_owned()); });
        event_out(game, Packet::Sound { event: event.to_owned(), at, volume, pitch });
    }
}
pub(super) fn stop_sound(game: &Game<'_>, event: &str) {
    if event.starts_with("orbital:") {
        CLIENT.with(|c| { c.borrow_mut().own_sounds.remove(event); });
        event_out(game, Packet::Stop(event.to_owned()));
    }
}
fn event_out(game: &Game<'_>, p: Packet) {
    let bytes = p.encode();
    if Packet::decode(&bytes).is_none() { return; }
    CLIENT.with(|cell| {
        let mut c = cell.borrow_mut();
        if c.blocked_source.is_some() { return; }
        if c.events.len() < EVENTS { c.events.push_back((game.ticks, bytes)); }
    });
}

/// Half of the native API's quota, before world work consumes the remainder.
/// Sound notifications share that budget; half of the visual slots remain for
/// effect state/data even while sound events arrive continuously.
/// A failed enqueue is retried
/// byte-for-byte; its cursor is committed only after the reliable channel accepts.
pub(super) fn tick_client(game: &mut Game<'_>) {
    if !game.native_window() || !game.native_on(ID) || game.native_player().is_none() { return; }
    CLIENT.with(|cell| {
        let mut c = cell.borrow_mut();
        let budget = (super::native_net::PER_TICK / 2).max(1) as usize;
        let mut used = 0;
        while c.events.front().is_some_and(|(t, _)| game.ticks.saturating_sub(*t) > 100) { c.events.pop_front(); }
        while used < budget / 2 && let Some((_, bytes)) = c.events.front() {
            if game.native_net_send_to_server(wire::CHANNEL, bytes).is_err() { return; }
            c.events.pop_front();
            used += 1;
        }
        let Some(latest) = c.latest.clone() else { return; };
        if latest.meta.shader == u8::MAX { c.retry = None; }
        for _ in used..budget {
            let (packet, after) = c.retry.take().unwrap_or_else(|| {
                let mut after = c.send.clone(); let p = after.next(&latest); (p, after)
            });
            if game.native_net_send_to_server(wire::CHANNEL, &packet.encode()).is_err() {
                c.retry = Some((packet, after)); break;
            }
            let state = matches!(packet, Packet::State(_));
            c.send = after;
            // One parameter update each tick; data chunks consume the remaining
            // quota on subsequent ticks. This also reserves room for world work.
            if state { break; }
        }
    });
    refresh(game);
}

/// Packet ownership comes from the authenticated server connection, never from
/// a client-supplied UUID. Only an actual placed operator may publish an effect.
pub(super) fn from_client(game: &mut Game<'_>, player: u128, channel: &str, bytes: &[u8]) -> bool {
    if channel != wire::CHANNEL { return false; }
    if game.native_window() || !game.native_on(ID) { return true; }
    let players = game.native_server_players();
    let Some(actor) = players.iter().find(|p| p.uuid == player && p.permission >= 2) else { return true; };
    let Some(packet) = Packet::decode(bytes) else { return true; };
    if let Packet::State(m) = &packet {
        // Use the server clock to reject forged animation timestamps. Ordinary
        // delay stays intact so all observers advance the same effect phase.
        if game.world.game_time.abs_diff(m.clock) > STALE { return true; }
    }
    SERVER.with(|cell| {
        let mut s = cell.borrow_mut();
        match &packet {
            Packet::State(_) | Packet::Data { .. } => {
                let peer = s.peers.entry(player).or_insert_with(|| (Receive::default(), 0, actor.dimension.to_owned()));
                if peer.2 != actor.dimension {
                    *peer = (Receive::default(), 0, actor.dimension.to_owned());
                }
                if peer.0.accept(packet) { peer.1 = game.ticks; }
            }
            Packet::Sound { event, .. } | Packet::Stop(event) if event.starts_with("orbital:") => {
                let envelope = envelope(player, bytes);
                for recipient in players.iter().filter(|p| p.uuid != player && p.dimension == actor.dimension) {
                    let q = s.events.entry(recipient.uuid).or_default();
                    if q.len() < EVENTS { q.push_back((game.ticks, player, actor.dimension.to_owned(), envelope.clone())); }
                }
            }
            _ => {}
        }
    });
    true
}

fn envelope(owner: u128, bytes: &[u8]) -> Vec<u8> {
    let mut b = Vec::with_capacity(16 + bytes.len()); b.extend(owner.to_le_bytes()); b.extend(bytes); b
}

/// Runs after world requests have been served and before their answer flush.
/// Half of each recipient's native quota remains for answers. Within the visual
/// half, bounded sound notifications and round-robin owners cannot starve data.
pub(super) fn tick_server(game: &mut Game<'_>, dimension: &str) {
    if game.native_window() { return; }
    let players = game.native_server_players();
    SERVER.with(|cell| {
        let mut s = cell.borrow_mut();
        s.peers.retain(|owner, (_, seen, dim)| players.iter().any(|p| p.uuid == *owner && p.permission >= 2 && p.dimension == *dim)
            && game.ticks.saturating_sub(*seen) <= STALE && game.native_on(ID));
        let mut snapshots = BTreeMap::new();
        for (owner, (rx, _, _)) in &s.peers {
            if let Some(state) = &rx.current { snapshots.insert(*owner, state.clone()); }
        }
        s.send.retain(|(to, _), _| players.iter().any(|p| p.uuid == *to));
        s.retry.retain(|(to, _), _| players.iter().any(|p| p.uuid == *to));
        s.events.retain(|to, _| players.iter().any(|p| p.uuid == *to));
        s.round.retain(|to, _| players.iter().any(|p| p.uuid == *to));
        for recipient in players.iter().filter(|p| p.dimension == dimension) {
            let to = recipient.uuid;
            let budget = (super::native_net::PER_TICK / 2).max(1) as usize;
            let mut used = 0;
            while used < budget / 2 && let Some((sent, owner, dim, bytes)) = s.events.get_mut(&to).and_then(|q| q.pop_front()) {
                if game.ticks.saturating_sub(sent) > 100 || dim != dimension
                    || !players.iter().any(|p| p.uuid == owner && p.permission >= 2 && p.dimension == dim) { continue; }
                if game.native_net_send_to_player(to, wire::CHANNEL, &bytes).is_err() {
                    s.events.entry(to).or_default().push_front((sent, owner, dim, bytes)); break;
                }
                used += 1;
            }
            let mut owners: Vec<u128> = players.iter().filter(|p| p.uuid != to && p.dimension == dimension
                && snapshots.contains_key(&p.uuid)).map(|p| p.uuid).collect();
            // Send a tombstone for owners who left, changed dimension or lost
            // permission, then retire their cursor after reliable delivery.
            for (target, owner) in s.send.keys() {
                if *target == to && !owners.contains(owner) { owners.push(*owner); }
            }
            owners.sort_unstable(); owners.dedup();
            if owners.is_empty() { continue; }
            let start = *s.round.entry(to).or_default() % owners.len();
            for i in 0..budget.saturating_sub(used) {
                let owner = owners[(start + i) % owners.len()];
                let valid = players.iter().any(|p| p.uuid == owner && p.dimension == dimension);
                let empty = clear_snapshot(u64::MAX, 0);
                let latest = if valid { snapshots.get(&owner).unwrap_or(&empty) } else { &empty };
                let key = (to, owner);
                let (bytes, after) = if latest.meta.shader == u8::MAX {
                    s.retry.remove(&key); let mut after = s.send.entry(key).or_default().clone();
                    let bytes = envelope(owner, &after.next(latest).encode()); (bytes, after)
                } else { s.retry.remove(&key).unwrap_or_else(|| {
                    let mut after = s.send.entry(key).or_default().clone();
                    let bytes = envelope(owner, &after.next(latest).encode()); (bytes, after)
                }) };
                if game.native_net_send_to_player(to, wire::CHANNEL, &bytes).is_err() {
                    s.retry.insert(key, (bytes, after)); break;
                }
                if latest.meta.shader == u8::MAX && latest.meta.generation == u64::MAX {
                    s.send.remove(&key); s.retry.remove(&key);
                } else { s.send.insert(key, after); }
                s.round.insert(to, (start + i + 1) % owners.len());
                // Avoid 32 identical heartbeats when there is no data to send.
                if i + 1 >= owners.len() && bytes.len() < 1024 { break; }
            }
        }
    });
}

pub(super) fn from_server(game: &mut Game<'_>, channel: &str, bytes: &[u8]) -> bool {
    if channel != wire::CHANNEL { return false; }
    if !game.native_window() || !game.native_on(ID) || game.native_player().is_none() { return true; }
    let Some((owner, bytes)) = bytes.split_at_checked(16) else { return true; };
    let owner = u128::from_le_bytes(owner.try_into().expect("16-byte owner"));
    let Some(packet) = Packet::decode(bytes) else { return true; };
    CLIENT.with(|cell| {
        let mut c = cell.borrow_mut();
        match packet {
            Packet::Sound { event, at, volume, pitch } if event.starts_with("orbital:") => {
                let playing = c.playing.entry(owner).or_default();
                if playing.len() >= EVENTS && !playing.contains(&event) { return; }
                playing.insert(event.clone());
                c.peers.entry(owner).or_default().1 = game.ticks;
                game.audio.play(&event, at, volume, pitch);
            }
            Packet::Stop(event) if event.starts_with("orbital:") => {
                if let Some(events) = c.playing.get_mut(&owner) { events.remove(&event); }
                if !c.own_sounds.contains(&event) && !c.playing.values().any(|events| events.contains(&event)) {
                    game.audio.stop_sounds(Some(&event), None);
                }
            }
            packet => {
                if matches!(&packet, Packet::State(m) if m.shader == u8::MAX) { clear_audio(game, &mut c, owner); }
                let peer = c.peers.entry(owner).or_default();
                if peer.0.accept(packet) { peer.1 = game.ticks; }
            }
        }
    });
    true
}

fn clear_audio(game: &mut Game<'_>, c: &mut Client, owner: u128) {
    if let Some(events) = c.playing.remove(&owner) {
        for event in events {
            if !c.own_sounds.contains(&event) && !c.playing.values().any(|events| events.contains(&event)) {
                game.audio.stop_sounds(Some(&event), None);
            }
        }
    }
}

pub(super) fn refresh(game: &mut Game<'_>) {
    let clock = game.native_clock().map_or(0, |c| c.game_time);
    let day_time = game.native_clock().map_or(0, |c| c.day_time.max(0) as u64);
    let mut passes = Vec::new();
    CLIENT.with(|cell| {
        let mut c = cell.borrow_mut();
        let stale: Vec<_> = c.peers.iter().filter(|(_, (_, seen))| game.ticks.saturating_sub(*seen) > STALE || !game.native_on(ID)).map(|(id, _)| *id).collect();
        for owner in stale { c.peers.remove(&owner); clear_audio(game, &mut c, owner); }
        let states: Vec<_> = c.peers.iter().filter_map(|(owner, (rx, _))| rx.current.as_ref().map(|s| (*owner, s.clone()))).collect();
        for (owner, s) in states {
            let Some((source, source_version)) = orbital_laser::network_pass(s.meta.shader) else { continue; };
            if c.logged.get(&owner) != Some(&s.meta.generation) {
                log::info!("[mod orbital] remote visual applied owner={owner:032x} shader={} generation={} data_revision={} words={}",
                    s.meta.shader, s.meta.generation, s.meta.revision, s.data.len());
                c.logged.insert(owner, s.meta.generation);
            }
            let atlas_version = if s.meta.shader == 9 { orbital_laser::network_atlas_version() } else { 0 };
            let key = (s.meta.key(), atlas_version);
            if c.upload.get(&owner).is_none_or(|(old, _, _)| *old != key) {
                c.next_upload = c.next_upload.wrapping_add(1);
                let token = c.next_upload;
                let mut data = s.data.clone();
                if s.meta.shader == 9 { orbital_laser::network_repack(s.meta.shader, Arc::make_mut(&mut data)); }
                c.upload.insert(owner, (key, token, data));
            }
            let data_version = c.upload[&owner].1;
            let data = c.upload[&owner].2.clone();
            let mut params = s.meta.params;
            // The sender and every observer read the same server game clock.
            // Add the transport delay to the effect clock rather than starting
            // a fresh animation when a packet reaches each participant.
            params[3] += clock.saturating_sub(s.meta.clock).clamp(0, STALE as i64) as f32 * 0.05;
            passes.push((owner, Pass { source, version: source_version, params,
                data: Some((data, data_version)), day_time }));
        }
        c.upload.retain(|owner, _| passes.iter().any(|(id, _)| id == owner));
        let cleared: Vec<u128> = c.logged.keys().filter(|owner| !passes.iter().any(|(id, _)| id == *owner)).copied().collect();
        for owner in cleared { c.logged.remove(&owner); log::info!("[mod orbital] remote visual cleared owner={owner:032x}"); }
    });
    horizon_client_render::orbital_post::set_remote(passes);
}

pub(super) fn clear(game: &mut Game<'_>) {
    CLIENT.with(|cell| {
        let mut c = cell.borrow_mut();
        let actors: Vec<_> = c.playing.keys().copied().collect();
        for owner in actors { clear_audio(game, &mut c, owner); }
        let generation = c.generation.wrapping_add(1);
        let next_upload = c.next_upload;
        let blocked_source = c.source.map(|(_, version)| version).or(c.blocked_source);
        *c = Client { generation, next_upload, blocked_source, ..Client::default() };
    });
    horizon_client_render::orbital_post::set_remote(Vec::new());
}
