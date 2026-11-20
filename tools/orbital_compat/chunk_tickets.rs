// Track Orbital's shared engine ticket without sharing ownership between players.

use std::collections::{BTreeMap, btree_map::Entry};

pub(crate) type ChunkKey = (String, i32, i32);

pub(crate) struct ChunkTickets<Session> {
    // An empty owner list keeps the ticket only until its queued edits drain.
    held: BTreeMap<ChunkKey, Vec<Session>>,
}

impl<Session> Default for ChunkTickets<Session> {
    fn default() -> Self {
        Self {
            held: BTreeMap::new(),
        }
    }
}

impl<Session: Clone + Eq> ChunkTickets<Session> {
    /// Returns true when the engine needs to acquire the shared ticket.
    pub(crate) fn acquire(&mut self, key: ChunkKey, session: &Session) -> bool {
        match self.held.entry(key) {
            Entry::Vacant(entry) => {
                entry.insert(vec![session.clone()]);
                true
            }
            Entry::Occupied(mut entry) => {
                if !entry.get().contains(session) {
                    entry.get_mut().push(session.clone());
                }
                false
            }
        }
    }

    /// Returns true only when the last owner releases and no edits are waiting.
    pub(crate) fn release(&mut self, key: &ChunkKey, session: &Session, waiting: bool) -> bool {
        let Some(owners) = self.held.get_mut(key) else {
            return false;
        };
        let old_len = owners.len();
        owners.retain(|owner| owner != session);
        owners.len() != old_len && !waiting && self.finish_waiting(key)
    }

    pub(crate) fn finish_waiting(&mut self, key: &ChunkKey) -> bool {
        if let Entry::Occupied(entry) = self.held.entry(key.clone())
            && entry.get().is_empty()
        {
            entry.remove();
            return true;
        }
        false
    }

    /// Removes departed, deauthorized and replaced epochs for the active level.
    pub(crate) fn prune(
        &mut self,
        dimension: &str,
        live: impl Fn(&Session) -> bool,
        waiting: impl Fn(&ChunkKey) -> bool,
    ) -> Vec<[i32; 2]> {
        let mut released = Vec::new();
        self.held.retain(|key, owners| {
            if key.0 != dimension {
                return true;
            }
            owners.retain(&live);
            if owners.is_empty() && !waiting(key) {
                released.push([key.1, key.2]);
                return false;
            }
            true
        });
        released
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn chunk(dimension: &str, x: i32) -> ChunkKey {
        (dimension.to_owned(), x, 0)
    }

    #[test]
    fn two_players_hold_one_chunk_until_the_last_release() {
        let mut tickets = ChunkTickets::default();
        let key = chunk("overworld", 1);
        assert!(tickets.acquire(key.clone(), &1));
        assert!(!tickets.acquire(key.clone(), &1));
        assert!(!tickets.acquire(key.clone(), &2));
        assert!(!tickets.release(&key, &1, false));
        assert!(!tickets.release(&key, &1, false));
        assert!(!tickets.finish_waiting(&key));
        assert!(tickets.release(&key, &2, false));
        assert!(tickets.acquire(key, &1));
    }

    #[test]
    fn waiting_work_keeps_a_ticket_until_it_drains() {
        let mut tickets = ChunkTickets::default();
        let key = chunk("overworld", 1);
        assert!(tickets.acquire(key.clone(), &1));
        assert!(!tickets.release(&key, &1, true));
        assert!(!tickets.acquire(key.clone(), &2));
        assert!(!tickets.finish_waiting(&key));
        assert!(!tickets.release(&key, &2, true));
        assert!(tickets.finish_waiting(&key));
        assert!(!tickets.finish_waiting(&key));
    }

    #[test]
    fn prune_distinguishes_connection_epochs_and_levels() {
        let mut tickets = ChunkTickets::default();
        let old = (1, 0);
        let new = (1, 1);
        let replaced = chunk("overworld", 1);
        let disconnected = chunk("overworld", 2);
        let pending = chunk("overworld", 3);
        let parked = chunk("nether", 4);
        for key in [&replaced, &disconnected, &pending, &parked] {
            assert!(tickets.acquire(key.clone(), &old));
        }
        assert!(!tickets.acquire(replaced.clone(), &new));
        assert_eq!(
            tickets.prune(
                "overworld",
                |session| *session == new,
                |key| *key == pending
            ),
            [[2, 0]]
        );
        assert!(tickets.finish_waiting(&pending));
        assert!(tickets.release(&replaced, &new, false));
        assert_eq!(tickets.prune("nether", |_| false, |_| false), [[4, 0]]);
    }
}
