// Application quotas and connection ownership for Orbital's negotiated play channels.

use super::{Game, orbital_visual_wire, orbital_wire};
use horizon_client::raw_payloads::{Endpoint, Side, connection::Epoch};
use horizon_protocol::payload_negotiation::Phase;
use mod_api::{PayloadBytes, PayloadId, PayloadSendError, PlayerRef};
use std::{cell::RefCell, sync::Arc};

pub(super) const WORK: &str = "orbital:work";
pub(super) const ANSWER: &str = "orbital:answer";
pub(super) const PER_TICK: usize = 32;

#[derive(Clone)]
pub(super) struct Session {
    player: PlayerRef,
    epoch: Arc<Epoch>,
}

impl PartialEq for Session {
    fn eq(&self, other: &Self) -> bool {
        self.player == other.player && Arc::ptr_eq(&self.epoch, &other.epoch)
    }
}

impl Eq for Session {}

impl Session {
    pub(super) fn uuid(&self) -> u128 {
        self.player.uuid()
    }

    pub(super) fn matches_epoch(&self, epoch: &Arc<Epoch>) -> bool {
        self.epoch.active() && Arc::ptr_eq(&self.epoch, epoch)
    }
}

pub(super) struct Peer {
    pub(super) session: Session,
    pub(super) uuid: u128,
    pub(super) dimension: String,
    pub(super) permission: u8,
}

fn live_session(endpoint: &Endpoint, player: PlayerRef) -> Option<Session> {
    let transport = endpoint.transport();
    let epoch = transport.epoch()?;
    if transport.player() != Some(player) || epoch.phase != Phase::Play || !epoch.active() {
        return None;
    }
    Some(Session { player, epoch })
}

pub(super) fn client_session(game: &Game<'_>) -> Option<Session> {
    let player = game.local_player()?;
    live_session(&game.remote.as_ref()?.session.payload_endpoint(), player)
}

fn peer(game: &Game<'_>, player: PlayerRef) -> Option<Peer> {
    let guest = game.online_player_record(player)?;
    let session = live_session(&Endpoint::server(guest.sender.clone()), player)?;
    Some(Peer {
        uuid: session.uuid(),
        session,
        dimension: guest.dimension.clone(),
        permission: guest.permission,
    })
}

pub(super) fn server_players(game: &Game<'_>) -> Vec<Peer> {
    game.native_server_players()
        .into_iter()
        .filter_map(|player| peer(game, player))
        .collect()
}

pub(super) fn authenticated_peer(game: &Game<'_>, uuid: u128, epoch: &Arc<Epoch>) -> Option<Peer> {
    let peer = peer(game, game.player(uuid)?)?;
    peer.session.matches_epoch(epoch).then_some(peer)
}

pub(super) fn operator_connection(
    game: &Game<'_>,
    session: &Session,
    dimension: &str,
) -> Option<u32> {
    let guest = game.online_player_record(session.player)?;
    let endpoint = Endpoint::server(guest.sender.clone());
    if guest.permission < 2
        || guest.dimension != dimension
        || live_session(&endpoint, session.player).as_ref() != Some(session)
    {
        return None;
    }
    Some(guest.conn)
}

#[derive(Default)]
struct Budget {
    tick: u64,
    used: Vec<(Session, Side, usize)>,
}

impl Budget {
    fn count(&mut self, tick: u64, session: &Session, side: Side) -> &mut usize {
        if self.tick != tick {
            self.tick = tick;
            self.used.clear();
        }
        let slot = self
            .used
            .iter()
            .position(|(existing, direction, _)| existing == session && *direction == side);
        match slot {
            Some(slot) => &mut self.used[slot].2,
            None => {
                self.used.push((session.clone(), side, 0));
                &mut self.used.last_mut().expect("inserted above").2
            }
        }
    }
}

thread_local! {
    static BUDGET: RefCell<Budget> = RefCell::new(Budget::default());
}

pub(super) fn send_to_server(
    game: &mut Game<'_>,
    session: &Session,
    channel: &str,
    bytes: &[u8],
) -> Result<(), PayloadSendError> {
    let endpoint = client_endpoint(game)?;
    if channel == orbital_visual_wire::CHANNEL {
        return send_visual(game, session, endpoint, Some(bytes));
    }
    send(game, session, endpoint, channel, bytes)
}

fn client_endpoint(game: &Game<'_>) -> Result<Endpoint, PayloadSendError> {
    Ok(game
        .remote
        .as_ref()
        .ok_or(PayloadSendError::Disconnected)?
        .session
        .payload_endpoint())
}

pub(super) fn send_to_player(
    game: &mut Game<'_>,
    session: &Session,
    channel: &str,
    bytes: &[u8],
) -> Result<(), PayloadSendError> {
    let endpoint = server_endpoint(game, session)?;
    if channel == orbital_visual_wire::CHANNEL {
        return send_visual(game, session, endpoint, Some(bytes));
    }
    send(game, session, endpoint, channel, bytes)
}

fn server_endpoint(game: &Game<'_>, session: &Session) -> Result<Endpoint, PayloadSendError> {
    let guest = game
        .online_player_record(session.player)
        .ok_or(PayloadSendError::StaleEpoch)?;
    Ok(Endpoint::server(guest.sender.clone()))
}

fn send(
    game: &mut Game<'_>,
    session: &Session,
    endpoint: Endpoint,
    channel: &str,
    bytes: &[u8],
) -> Result<(), PayloadSendError> {
    let id = PayloadId(channel.to_owned());
    let side = endpoint.side();
    if channel != orbital_visual_wire::CHANNEL
        && channel
            != match side {
                Side::Client => WORK,
                Side::Server => ANSWER,
            }
    {
        return Err(PayloadSendError::WrongDirection(id));
    }
    if bytes.len() > orbital_wire::MESSAGE_LIMIT {
        return Err(PayloadSendError::PayloadTooLarge {
            id,
            size: bytes.len() as u64,
            limit: orbital_wire::MESSAGE_LIMIT as u64,
        });
    }
    if live_session(&endpoint, session.player).as_ref() != Some(session) {
        return Err(PayloadSendError::StaleEpoch);
    }
    // The packet cursor is only advanced for an accepted send. The Orbital
    // tick drains its queues outside open roots, where a later rollback would
    // otherwise undo the publication while leaving its external cursor moved.
    if !game.transactions.is_empty() {
        return Err(PayloadSendError::Backpressure);
    }
    BUDGET.with(|budget| {
        let mut budget = budget.borrow_mut();
        let used = budget.count(game.ticks, session, side);
        if *used >= PER_TICK {
            return Err(PayloadSendError::Backpressure);
        }
        game.queue_payload(
            endpoint,
            session.epoch.clone(),
            &id,
            PayloadBytes(bytes.to_vec()),
        )?;
        *used += 1;
        Ok(())
    })
}
