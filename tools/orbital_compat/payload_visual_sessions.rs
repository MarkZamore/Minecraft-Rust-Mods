fn sync_server_sessions(server: &mut Server, players: &[payload::Peer]) {
    let current: BTreeMap<_, _> = players
        .iter()
        .map(|peer| (peer.uuid, peer.session.clone()))
        .collect();
    for (&player, old) in &server.sessions {
        if current.get(&player) == Some(old) {
            continue;
        }
        server.peers.remove(&player);
        server.events.remove(&player);
        for events in server.events.values_mut() {
            events.retain(|(_, actor, _, _)| *actor != player);
        }
        server.round.remove(&player);
        server
            .retry
            .retain(|(target, owner), _| *target != player && *owner != player);
        server.send.retain(|(target, owner), cursor| {
            if *owner == player {
                // Keep a cursor entry for existing observers so they still get a
                // tombstone, but discard all unsent fragments of the old effect.
                *cursor = Send::default();
            }
            *target != player
        });
    }
    server.sessions = current;
}
