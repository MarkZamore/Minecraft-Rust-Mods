// The two ends live in different game roles even for the integrated owner's
// in-memory connection. Credit always belongs to an exact play epoch.
use std::collections::BTreeMap;

#[derive(Default)]
struct VisualLinks {
    client: BTreeMap<u128, (Session, visual_flow::Flow)>,
    server: BTreeMap<u128, (Session, visual_flow::Flow)>,
}

thread_local! {
    static VISUAL_LINKS: RefCell<VisualLinks> = RefCell::new(VisualLinks::default());
}

fn with_visual_flow<R>(
    session: &Session,
    side: Side,
    work: impl FnOnce(&mut visual_flow::Flow) -> R,
) -> R {
    VISUAL_LINKS.with(|links| {
        let mut links = links.borrow_mut();
        let links = match side {
            Side::Client => &mut links.client,
            Side::Server => &mut links.server,
        };
        links.retain(|_, (previous, _)| previous.epoch.active());
        let (previous, flow) = links.entry(session.uuid())
            .or_insert_with(|| (session.clone(), visual_flow::Flow::default()));
        if previous != session {
            *previous = session.clone();
            *flow = visual_flow::Flow::default();
        }
        work(flow)
    })
}

pub(super) fn visual_from_guest<'a>(session: &Session, bytes: &'a [u8]) -> Option<&'a [u8]> {
    with_visual_flow(session, Side::Server, |flow| flow.receive(bytes))
}

pub(super) fn visual_from_host<'a>(session: &Session, bytes: &'a [u8]) -> Option<&'a [u8]> {
    with_visual_flow(session, Side::Client, |flow| flow.receive(bytes))
}

pub(super) fn flush_visual_ack(game: &mut Game<'_>, session: &Session) {
    let endpoint = if game.native_window() {
        client_endpoint(game)
    } else {
        server_endpoint(game, session)
    };
    if let Ok(endpoint) = endpoint {
        // A refusal keeps the cumulative ACK pending for the next tick.
        let _ = send_visual(game, session, endpoint, None);
    }
}

fn send_visual(
    game: &mut Game<'_>,
    session: &Session,
    endpoint: Endpoint,
    body: Option<&[u8]>,
) -> Result<(), PayloadSendError> {
    let side = endpoint.side();
    let prepared = with_visual_flow(session, side, |flow| flow.prepare(body));
    let Some(frame) = prepared else {
        return if body.is_some() { Err(PayloadSendError::Backpressure) } else { Ok(()) };
    };
    send(game, session, endpoint, orbital_visual_wire::CHANNEL, &frame.bytes)?;
    with_visual_flow(session, side, |flow| flow.sent(frame));
    Ok(())
}
