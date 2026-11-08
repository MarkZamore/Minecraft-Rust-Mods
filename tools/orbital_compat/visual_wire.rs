// Pack-owned Orbital visual replication. No executable shader source crosses the wire.
use std::sync::Arc;

pub const CHANNEL: &str = "orbital:visual";
// Identical to the author's SDK POST_DATA_WORDS, not a new effect/mechanics limit.
pub const MAX_WORDS: usize = 1 << 18;
pub const CHUNK: usize = 6000;
pub const SHADERS: u8 = 10;

#[derive(Clone, Debug, PartialEq)]
pub struct Meta {
    pub generation: u64,
    pub shader: u8,
    pub params: [f32; 16],
    pub revision: u64,
    pub words: u32,
    pub clock: i64,
}
impl Meta {
    pub fn key(&self) -> (u64, u8, u64, u32) { (self.generation, self.shader, self.revision, self.words) }
}

#[derive(Clone, Debug, PartialEq)]
pub enum Packet {
    State(Meta),
    Data { generation: u64, revision: u64, offset: u32, words: Vec<u32> },
    Sound { event: String, at: Option<[f64; 3]>, volume: f32, pitch: f32 },
    Stop(String),
}

impl Packet {
    pub fn encode(&self) -> Vec<u8> {
        let mut b = Vec::new();
        match self {
            Self::State(m) => {
                b.push(0); b.extend(m.generation.to_le_bytes()); b.push(m.shader);
                for f in m.params { b.extend(f.to_le_bytes()); }
                b.extend(m.revision.to_le_bytes()); b.extend(m.words.to_le_bytes()); b.extend(m.clock.to_le_bytes());
            }
            Self::Data { generation, revision, offset, words } => {
                b.push(1); b.extend(generation.to_le_bytes()); b.extend(revision.to_le_bytes()); b.extend(offset.to_le_bytes());
                for w in words { b.extend(w.to_le_bytes()); }
            }
            Self::Sound { event, at, volume, pitch } => {
                b.push(2); b.extend((event.len() as u16).to_le_bytes()); b.extend(event.as_bytes());
                b.push(u8::from(at.is_some()));
                if let Some(at) = at { for f in at { b.extend(f.to_le_bytes()); } }
                b.extend(volume.to_le_bytes()); b.extend(pitch.to_le_bytes());
            }
            Self::Stop(event) => {
                b.push(3); b.extend((event.len() as u16).to_le_bytes()); b.extend(event.as_bytes());
            }
        }
        b
    }

    pub fn decode(mut b: &[u8]) -> Option<Self> {
        fn take<const N: usize>(b: &mut &[u8]) -> Option<[u8; N]> {
            let (head, tail) = b.split_at_checked(N)?; *b = tail; head.try_into().ok()
        }
        fn name(b: &mut &[u8]) -> Option<String> {
            let len = u16::from_le_bytes(take(b)?) as usize;
            if len == 0 || len > 256 { return None; }
            let (head, tail) = b.split_at_checked(len)?; *b = tail;
            let s = std::str::from_utf8(head).ok()?;
            s.bytes().all(|c| c.is_ascii_lowercase() || c.is_ascii_digit() || b"_-.:/".contains(&c)).then(|| s.to_owned())
        }
        let kind = take::<1>(&mut b)?[0];
        let p = match kind {
            0 => {
                let generation = u64::from_le_bytes(take(&mut b)?);
                let shader = take::<1>(&mut b)?[0];
                if shader >= SHADERS && shader != u8::MAX { return None; }
                let mut params = [0.0; 16];
                for f in &mut params { *f = f32::from_le_bytes(take(&mut b)?); if !f.is_finite() { return None; } }
                let revision = u64::from_le_bytes(take(&mut b)?);
                let words = u32::from_le_bytes(take(&mut b)?);
                let clock = i64::from_le_bytes(take(&mut b)?);
                if words as usize > MAX_WORDS || (shader == u8::MAX && words != 0) { return None; }
                Self::State(Meta { generation, shader, params, revision, words, clock })
            }
            1 => {
                let generation = u64::from_le_bytes(take(&mut b)?);
                let revision = u64::from_le_bytes(take(&mut b)?);
                let offset = u32::from_le_bytes(take(&mut b)?);
                if b.is_empty() || b.len() > CHUNK * 4 || b.len() % 4 != 0 || offset as usize % CHUNK != 0 { return None; }
                let words = b.chunks_exact(4).map(|w| u32::from_le_bytes(w.try_into().expect("four bytes"))).collect(); b = &[];
                Self::Data { generation, revision, offset, words }
            }
            2 => {
                let event = name(&mut b)?;
                let at = match take::<1>(&mut b)?[0] {
                    0 => None,
                    1 => {
                        let mut at = [0.0; 3];
                        for f in &mut at { *f = f64::from_le_bytes(take(&mut b)?); if !f.is_finite() { return None; } }
                        Some(at)
                    }
                    _ => return None,
                };
                let volume = f32::from_le_bytes(take(&mut b)?);
                let pitch = f32::from_le_bytes(take(&mut b)?);
                if !volume.is_finite() || !pitch.is_finite() || volume < 0.0 || pitch <= 0.0 { return None; }
                Self::Sound { event, at, volume, pitch }
            }
            3 => Self::Stop(name(&mut b)?),
            _ => return None,
        };
        b.is_empty().then_some(p)
    }
}

#[derive(Clone)]
pub struct Snapshot { pub meta: Meta, pub data: Arc<[u32]> }

/// Incomplete data never replaces the last complete frame. A newer state supersedes
/// only its unfinished predecessor; duplicates cannot count as additional chunks.
#[derive(Default)]
pub struct Receive {
    pub current: Option<Snapshot>,
    pending: Option<(Meta, Vec<u32>, Vec<bool>)>,
}
impl Receive {
    pub fn accept(&mut self, p: Packet) -> bool {
        match p {
            Packet::State(m) => {
                if m.shader == u8::MAX { self.current = None; self.pending = None; return true; }
                if let Some(s) = &mut self.current && s.meta.key() == m.key() {
                    s.meta = m; return true;
                }
                if let Some((meta, _, _)) = &mut self.pending && meta.key() == m.key() {
                    *meta = m; return true;
                }
                if m.words == 0 { self.current = Some(Snapshot { meta: m, data: Arc::from([]) }); self.pending = None; }
                else {
                    let words = m.words as usize;
                    let (_, mut data, mut seen) = self.pending.take().unwrap_or_else(|| (m.clone(), Vec::new(), Vec::new()));
                    data.resize(words, 0); seen.clear(); seen.resize(words.div_ceil(CHUNK), false);
                    self.pending = Some((m, data, seen));
                }
                true
            }
            Packet::Data { generation, revision, offset, words } => {
                let Some((m, data, seen)) = &mut self.pending else { return false; };
                let offset = offset as usize;
                if generation != m.generation || revision != m.revision || offset >= data.len()
                    || words.len() != CHUNK.min(data.len() - offset) || offset % CHUNK != 0 { return false; }
                let Some(slot) = seen.get_mut(offset / CHUNK) else { return false; };
                if !*slot { data[offset..offset + words.len()].copy_from_slice(&words); *slot = true; }
                if seen.iter().all(|v| *v) {
                    let (meta, data, _) = self.pending.take().expect("pending above");
                    self.current = Some(Snapshot { meta, data: Arc::from(data) });
                }
                true
            }
            _ => false,
        }
    }
}

/// Finishes the current immutable data snapshot before taking a newer one. Only
/// changed SDK data versions are uploaded; parameter updates remain inexpensive.
#[derive(Clone, Default)]
pub struct Send {
    active: Option<Snapshot>,
    offset: usize,
    announced: bool,
}
impl Send {
    pub fn next(&mut self, latest: &Snapshot) -> Packet {
        let completed = self.active.as_ref().is_none_or(|s| self.offset >= s.data.len());
        if (completed || latest.meta.shader == u8::MAX) && self.active.as_ref().is_none_or(|s| s.meta.key() != latest.meta.key()) {
            self.active = Some(latest.clone()); self.offset = 0; self.announced = false;
        }
        let s = self.active.as_mut().expect("assigned above");
        if !self.announced || self.offset >= s.data.len() {
            self.announced = true;
            // Keep the frozen revision until its bytes have arrived, then catch up.
            if s.meta.key() == latest.meta.key() { s.meta = latest.meta.clone(); }
            return Packet::State(s.meta.clone());
        }
        let end = (self.offset + CHUNK).min(s.data.len());
        let packet = Packet::Data { generation: s.meta.generation, revision: s.meta.revision,
            offset: self.offset as u32, words: s.data[self.offset..end].to_vec() };
        self.offset = end;
        packet
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    fn snapshot(revision: u64, words: usize) -> Snapshot {
        Snapshot { meta: Meta { generation: 1, shader: 0, params: [0.0; 16], revision, words: words as u32, clock: 40 },
            data: Arc::from((0..words as u32).collect::<Vec<_>>()) }
    }
    #[test] fn invalid_packets_do_not_allocate_or_panic() {
        for n in 0..128 { assert!(Packet::decode(&vec![255; n]).is_none()); }
        let mut s = snapshot(1, MAX_WORDS + 1).meta;
        assert!(Packet::decode(&Packet::State(s.clone()).encode()).is_none());
        s.words = 0; s.params[3] = f32::NAN;
        assert!(Packet::decode(&Packet::State(s).encode()).is_none());
    }
    #[test] fn multipart_round_trip_is_atomic_and_duplicates_are_idempotent() {
        let source = snapshot(7, CHUNK * 2 + 8); let mut tx = Send::default(); let mut rx = Receive::default();
        for _ in 0..4 {
            let p = tx.next(&source); assert_eq!(Packet::decode(&p.encode()), Some(p.clone()));
            assert!(rx.accept(p.clone())); let _ = rx.accept(p);
        }
        assert_eq!(&*rx.current.unwrap().data, &*source.data);
    }
    #[test] fn newer_pending_cannot_mix_revisions_or_erase_complete_frame() {
        let a = snapshot(1, 0); let b = snapshot(2, CHUNK + 2); let c = snapshot(3, CHUNK + 2);
        let mut r = Receive::default(); assert!(r.accept(Packet::State(a.meta)));
        assert!(r.accept(Packet::State(b.meta))); assert!(r.accept(Packet::State(c.meta)));
        assert!(!r.accept(Packet::Data { generation: 1, revision: 2, offset: 0, words: vec![0; CHUNK] }));
        assert_eq!(r.current.unwrap().meta.revision, 1);
    }
    #[test] fn sender_completes_inflight_data_before_latest_and_reuses_unchanged_data() {
        let a = snapshot(1, CHUNK + 2); let b = snapshot(2, CHUNK + 2); let mut s = Send::default();
        assert!(matches!(s.next(&a), Packet::State(_)));
        assert!(matches!(s.next(&b), Packet::Data { revision: 1, offset: 0, .. }));
        assert!(matches!(s.next(&b), Packet::Data { revision: 1, offset, .. } if offset == CHUNK as u32));
        assert!(matches!(s.next(&b), Packet::State(Meta { revision: 2, .. })));
    }
    #[test] fn clear_cancels_inflight_and_late_old_chunks_cannot_revive_it() {
        let a = snapshot(1, CHUNK + 2); let mut clear = snapshot(0, 0); clear.meta.shader = u8::MAX; clear.meta.generation = 2;
        let mut tx = Send::default(); let mut rx = Receive::default();
        assert!(rx.accept(tx.next(&a)));
        assert!(rx.accept(tx.next(&clear)));
        assert!(!rx.accept(Packet::Data { generation: 1, revision: 1, offset: 0, words: vec![1; CHUNK] }));
        assert!(rx.current.is_none());
    }
    #[test] fn truncated_and_trailing_metadata_are_rejected() {
        let bytes = Packet::State(snapshot(1, 2).meta).encode();
        for end in 0..bytes.len() { assert!(Packet::decode(&bytes[..end]).is_none()); }
        let mut extra = bytes; extra.push(0); assert!(Packet::decode(&extra).is_none());
    }
}
