// Application credit for Orbital visuals. The negotiated transport serializes
// main-thread roots, so a successful enqueue alone does not mean one was read.

pub(crate) const WINDOW: u64 = 8;
const HEADER: usize = 16;

#[derive(Default)]
pub(crate) struct Flow {
    sent: u64,
    acknowledged: u64,
    received: u64,
    sent_ack: u64,
}

pub(crate) struct Frame {
    sequence: u64,
    ack: u64,
    pub(crate) bytes: Vec<u8>,
}

impl Flow {
    // An ACK-only frame takes no credit and a subsequent tick does not retry a
    // delivered ACK. Data carries the same cumulative ACK in its header.
    pub(crate) fn prepare(&self, body: Option<&[u8]>) -> Option<Frame> {
        let sequence = match body {
            Some(_) if self.sent - self.acknowledged < WINDOW => self.sent.checked_add(1)?,
            None if self.sent_ack < self.received => 0,
            _ => return None,
        };
        let body = body.unwrap_or_default();
        let ack = self.received;
        let mut bytes = Vec::with_capacity(HEADER + body.len());
        bytes.extend(sequence.to_le_bytes());
        bytes.extend(ack.to_le_bytes());
        bytes.extend(body);
        Some(Frame {
            sequence,
            ack,
            bytes,
        })
    }

    pub(crate) fn sent(&mut self, frame: Frame) {
        if frame.sequence != 0 {
            self.sent = frame.sequence;
        }
        self.sent_ack = self.sent_ack.max(frame.ack);
    }

    pub(crate) fn receive<'a>(&mut self, bytes: &'a [u8]) -> Option<&'a [u8]> {
        let (header, body) = bytes.split_at_checked(HEADER)?;
        let sequence = u64::from_le_bytes(header[..8].try_into().ok()?);
        let ack = u64::from_le_bytes(header[8..].try_into().ok()?);
        if ack > self.sent || (sequence == 0) != body.is_empty() {
            return None;
        }
        if sequence != 0 {
            if self.received.checked_add(1) != Some(sequence) {
                return None;
            }
            self.received = sequence;
        }
        self.acknowledged = self.acknowledged.max(ack);
        (sequence != 0).then_some(body)
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn busy_recipient_bounds_visual_ingress_until_it_consumes_a_frame() {
        let mut source = Flow::default();
        let mut sink = Flow::default();
        let fragment = vec![0; 24_021];
        let mut pending = Vec::new();
        for _ in 0..WINDOW {
            let frame = source.prepare(Some(&fragment)).unwrap();
            pending.push(frame.bytes.clone());
            source.sent(frame);
        }
        for _ in 0..100 {
            assert!(source.prepare(Some(&fragment)).is_none());
        }
        assert!(pending.iter().map(Vec::len).sum::<usize>() < 200_000);
        assert_eq!(sink.receive(&pending[0]), Some(fragment.as_slice()));
        let ack = sink.prepare(None).unwrap();
        assert!(source.receive(&ack.bytes).is_none());
        sink.sent(ack);
        assert!(sink.prepare(None).is_none());
        let next = source.prepare(Some(&fragment)).unwrap();
        source.sent(next);
        assert!(source.prepare(Some(&fragment)).is_none());
    }

    #[test]
    fn duplicate_and_unearned_acknowledgements_do_not_grant_more_credit() {
        let mut source = Flow::default();
        let mut sink = Flow::default();
        let first = source.prepare(Some(b"first")).unwrap();
        let second = source.prepare(Some(b"replacement")).unwrap();
        assert_eq!(&first.bytes[..8], &second.bytes[..8]);
        assert_eq!(sink.receive(&second.bytes), Some(b"replacement".as_slice()));
        assert!(sink.receive(&second.bytes).is_none());
        source.sent(second);
        let mut ack = sink.prepare(None).unwrap();
        ack.bytes[8..16].copy_from_slice(&2_u64.to_le_bytes());
        assert!(source.receive(&ack.bytes).is_none());
        assert_eq!(source.acknowledged, 0);
        let ack = sink.prepare(None).unwrap();
        source.receive(&ack.bytes);
        source.receive(&ack.bytes);
        assert_eq!(source.acknowledged, 1);
    }

    #[test]
    fn data_piggybacks_acknowledgement_without_an_extra_packet() {
        let mut east = Flow::default();
        let mut west = Flow::default();
        let packet = east.prepare(Some(b"state")).unwrap();
        assert_eq!(west.receive(&packet.bytes), Some(b"state".as_slice()));
        east.sent(packet);
        let response = west.prepare(Some(b"other state")).unwrap();
        assert_eq!(
            east.receive(&response.bytes),
            Some(b"other state".as_slice())
        );
        west.sent(response);
        assert_eq!(east.acknowledged, 1);
        assert!(west.prepare(None).is_none());
    }
}
