/// Apply one valid sequenced batch at most once for this play connection.
fn serve_batch(game: &mut Game<'_>, session: &payload::Session, dimension: &str, bytes: &[u8]) {
    let Some(batch) = wire::sequenced_batch(bytes) else {
        log::warn!("[mod {ID}] a bad work batch from {}", uuid(session.uuid()));
        return;
    };
    let take = SERVER.with(|server| {
        let mut server = server.borrow_mut();
        let (_, epoch, next) = server.sequence.entry(session.uuid())
            .or_insert_with(|| (session.clone(), batch.client_epoch, 0));
        // A negotiated play epoch owns a single application stream. Only the
        // authenticated epoch change can start it again after a disconnect.
        if *epoch != batch.client_epoch {
            return false;
        }
        let take = *next == batch.sequence;
        if take {
            *next = next.wrapping_add(1);
        }
        let ack = (*epoch, *next);
        server.acks.insert(session.uuid(), (session.clone(), ack));
        take
    });
    if take {
        for request in batch.requests {
            if serve(game, session, dimension, request).is_none() {
                log::warn!("[mod {ID}] a bad request from {}", uuid(session.uuid()));
            }
        }
    }
}

/// ACK deadlines take precedence over large column answers and effect frames.
fn flush_acks(game: &mut Game<'_>, recipients: &[payload::Peer]) {
    for peer in recipients.iter().filter(|peer| peer.permission >= 2) {
        if payload::operator_connection(game, &peer.session, &peer.dimension).is_none() {
            continue;
        }
        let ack = SERVER.with(|server| {
            server.borrow().acks.get(&peer.uuid)
                .filter(|(session, _)| *session == peer.session)
                .map(|(_, ack)| *ack)
        });
        let Some((epoch, next)) = ack else { continue; };
        let mut reply = Writer::new(12);
        reply.u32(epoch);
        reply.u32(next);
        if payload::send_to_player(game, &peer.session, ANSWER, &reply.0).is_ok() {
            SERVER.with(|server| server.borrow_mut().acks.remove(&peer.uuid));
        }
    }
}
