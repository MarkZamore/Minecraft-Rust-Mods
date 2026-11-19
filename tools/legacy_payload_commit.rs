//! Run the pack's Orbital notification only when Horizon commits its receiving root.

use super::{Game, orbital_payloads};
use horizon_client::raw_payloads::{Incoming, connection::Epoch};
use mod_api::{IPayloadContext, PayloadDirection, PayloadProtocol, PlayerRef};
use std::sync::Arc;

pub(super) struct Delivery {
    channel: &'static str,
    sender: Option<PlayerRef>,
    epoch: Arc<Epoch>,
    bytes: Vec<u8>,
}

impl Delivery {
    pub(super) fn capture(
        incoming: &Incoming,
        context: &dyn IPayloadContext,
        bytes: &[u8],
    ) -> Option<Self> {
        if incoming.raw
            || !incoming.epoch.active()
            || context.protocol() != PayloadProtocol::Play
            || incoming.receiver.owner() != "orbital"
            || bytes.len() > orbital_payloads::MAX_BYTES
        {
            return None;
        }
        let (channel, sender) = match (context.flow(), incoming.id.0.as_str()) {
            (PayloadDirection::ToServer, orbital_payloads::WORK) => {
                (orbital_payloads::WORK, Some(context.player().ok()?))
            }
            (PayloadDirection::ToServer, orbital_payloads::VISUAL) => {
                (orbital_payloads::VISUAL, Some(context.player().ok()?))
            }
            (PayloadDirection::ToClient, orbital_payloads::ANSWER) => (orbital_payloads::ANSWER, None),
            (PayloadDirection::ToClient, orbital_payloads::VISUAL) => (orbital_payloads::VISUAL, None),
            _ => return None,
        };
        Some(Self {
            channel,
            sender,
            epoch: incoming.epoch.clone(),
            bytes: bytes.to_vec(),
        })
    }

    pub(super) fn dispatch(self, game: &mut Game<'_>) {
        if !self.epoch.active() {
            return;
        }
        match self.sender {
            Some(sender) if game.player(sender.uuid()) == Some(sender) => {
                game.native_net_from_client(sender.uuid(), self.channel, &self.bytes, &self.epoch);
            }
            Some(_) => {} // Rejoined or respawned under a different live player handle.
            None => game.native_net_from_server(self.channel, &self.bytes, &self.epoch),
        }
    }
}
