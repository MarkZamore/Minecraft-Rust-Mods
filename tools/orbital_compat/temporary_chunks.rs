    /// A native strike's runtime-only ticket. The normal forced set drives
    /// loading and ticking, but these owners are absent from world saves.
    #[doc(hidden)]
    pub fn set_transient_forced(&mut self, owner: &'static str, chunk: (i32, i32), hold: bool) -> bool {
        let in_world = (-1_875_000..1_875_000).contains(&chunk.0)
            && (-1_875_000..1_875_000).contains(&chunk.1);
        if self.remote || !in_world {
            return false;
        }
        let changed = if hold {
            self.transient_forced.entry(chunk).or_default().insert(owner)
        } else if let std::collections::hash_map::Entry::Occupied(mut entry) = self.transient_forced.entry(chunk) {
            let removed = entry.get_mut().remove(owner);
            if entry.get().is_empty() {
                entry.remove();
            }
            removed
        } else {
            false
        };
        if changed && self.settle(chunk) {
            self.rank_forced(chunk.0, chunk.1, chunk.0, chunk.1);
        }
        changed
    }

