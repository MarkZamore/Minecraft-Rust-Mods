type PendingPayloads = BTreeMap<u128, (payload::Session, VecDeque<Vec<u8>>)>;

fn queue_for<'a>(
    queues: &'a mut PendingPayloads,
    session: &payload::Session,
) -> &'a mut VecDeque<Vec<u8>> {
    let (current, messages) = queues
        .entry(session.uuid())
        .or_insert_with(|| (session.clone(), VecDeque::new()));
    if current != session {
        *current = session.clone();
        messages.clear();
    }
    messages
}

fn sync_payload_session(game: &mut Game<'_>) -> Option<payload::Session> {
    let current = payload::client_session(game);
    let changed = CLIENT.with(|client| {
        let mut client = client.borrow_mut();
        let changed = client.payload_session.is_some() && client.payload_session != current;
        if changed {
            client.out.clear();
            client.surface.clear();
            client.columns.clear();
            client.pending_surface.clear();
            client.pending_columns.clear();
        }
        client.payload_session = current.clone();
        changed
    });
    if changed {
        super::orbital_visual::clear(game);
    }
    current
}

/// Called after the engine commits this payload's authenticated play epoch.
pub(super) fn from_client(
    game: &mut Game<'_>,
    player: u128,
    channel: &str,
    data: &[u8],
    epoch: &Arc<Epoch>,
) {
    if game.native_window() || !game.native_on(ID) || data.len() > wire::MESSAGE_LIMIT {
        return;
    }
    let Some(peer) = payload::authenticated_peer(game, player, epoch) else {
        return;
    };
    if super::orbital_visual::from_client(game, &peer, channel, data)
        || peer.permission < 2
        || channel != WORK
    {
        return;
    }
    SERVER.with(|server| {
        let mut server = server.borrow_mut();
        let queue = queue_for(&mut server.work, &peer.session);
        if queue.len() < payload::PER_TICK {
            queue.push_back(data.to_vec());
        }
    });
}

fn uuid(u: u128) -> String {
    let h = format!("{u:032x}");
    format!(
        "{}-{}-{}-{}-{}",
        &h[0..8],
        &h[8..12],
        &h[12..16],
        &h[16..20],
        &h[20..32]
    )
}

/// Run placed operators' requests in the level that currently owns their session.
pub(super) fn level_tick(game: &mut Game<'_>, dimension: &str) {
    if game.native_window() || !game.native_on(ID) {
        return;
    }
    let players = payload::server_players(game);
    SERVER.with(|server| {
        let mut server = server.borrow_mut();
        let live = |session: &payload::Session| {
            players
                .iter()
                .any(|peer| peer.permission >= 2 && peer.session == *session)
        };
        server.work.retain(|_, (session, _)| live(session));
        server.answers.retain(|_, (session, _)| live(session));
    });
    for peer in players
        .iter()
        .filter(|peer| peer.permission >= 2 && peer.dimension == dimension)
    {
        let work = SERVER
            .with(|s| s.borrow_mut().work.remove(&peer.uuid))
            .unwrap_or_else(|| (peer.session.clone(), VecDeque::new()));
        for batch in work.1 {
            let Some(requests) = wire::batch(&batch) else {
                log::warn!("[mod {ID}] a bad message from {}", uuid(peer.uuid));
                continue;
            };
            for request in requests {
                if serve(game, &work.0, dimension, request).is_none() {
                    log::warn!("[mod {ID}] a bad request from {}", uuid(peer.uuid));
                }
            }
        }
    }
    // Visual state uses at most half of the same per-connection application quota.
    super::orbital_visual::tick_server(game, dimension);
    for peer in players.iter().filter(|peer| peer.permission >= 2) {
        if payload::operator_connection(game, &peer.session, &peer.dimension).is_none() {
            continue;
        }
        loop {
            let message = SERVER.with(|s| {
                s.borrow_mut()
                    .answers
                    .get_mut(&peer.uuid)
                    .and_then(|(_, q)| q.pop_front())
            });
            let Some(message) = message else {
                break;
            };
            if message.len() > wire::MESSAGE_LIMIT {
                log::warn!("[mod {ID}] an answer of {} bytes left out", message.len());
                continue;
            }
            if let Err(error) = payload::send_to_player(game, &peer.session, ANSWER, &message) {
                log::debug!("[mod {ID}] answers waiting: {error:?}");
                SERVER.with(|s| {
                    queue_for(&mut s.borrow_mut().answers, &peer.session).push_front(message)
                });
                break;
            }
        }
    }
    SERVER.with(|s| s.borrow_mut().answers.retain(|_, (_, q)| !q.is_empty()));
}

fn answer(session: &payload::Session, message: Vec<u8>) {
    SERVER.with(|s| queue_for(&mut s.borrow_mut().answers, session).push_back(message));
}
