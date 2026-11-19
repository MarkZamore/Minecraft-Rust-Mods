//! Negotiated main-thread Orbital Laser payloads for the compiled pack.

use mod_api::{
    Hooks, IPayloadContext, IPayloadHandler, PayloadBytes, PayloadDirection, PayloadHandleError,
    PayloadId, PayloadProtocol, Registrar, RegistrationContext,
};
use std::sync::Arc;

pub const WORK: &str = "orbital:work";
pub const ANSWER: &str = "orbital:answer";
pub const VISUAL: &str = "orbital:visual";
pub const MAX_BYTES: usize = 30_000;

pub struct OrbitalPayloads;

struct Receiver(PayloadDirection);

impl IPayloadHandler for Receiver {
    fn handle(
        &self,
        payload: PayloadBytes,
        context: &mut dyn IPayloadContext,
    ) -> Result<(), PayloadHandleError> {
        // Decoding and application are performed by the pack's commit callback,
        // after the engine has admitted the exact authenticated connection.
        if context.protocol() != PayloadProtocol::Play
            || context.flow() != self.0
            || payload.0.len() > MAX_BYTES
        {
            return Err(PayloadHandleError::Failed("invalid Orbital play payload".to_owned()));
        }
        Ok(())
    }
}

#[mod_api::__subscriptions]
impl Hooks for OrbitalPayloads {
    fn register(&self, registrar: &mut dyn Registrar, _: &RegistrationContext) {
        let mut event = registrar.payload_handlers();
        let mut registrar = event.registrar("1");
        let server: Arc<dyn IPayloadHandler> = Arc::new(Receiver(PayloadDirection::ToServer));
        let client: Arc<dyn IPayloadHandler> = Arc::new(Receiver(PayloadDirection::ToClient));
        registrar.play_to_server(PayloadId(WORK.into()), server.clone());
        registrar.play_to_client(PayloadId(ANSWER.into()), Some(client.clone()));
        registrar.play_bidirectional(PayloadId(VISUAL.into()), server, Some(client));
    }
}
