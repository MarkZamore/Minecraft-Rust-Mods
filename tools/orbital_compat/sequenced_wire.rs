// The author's 0.5.17 work protocol adds a client sequence stream to the
// existing validated request and answer formats. Play epoch is checked by the
// enclosing transport before any of these application values are admitted.

impl Reader<'_> {
    pub(super) fn i16(&mut self) -> Option<i16> {
        Some(i16::from_le_bytes(self.take(2)?.try_into().ok()?))
    }
}

pub(super) struct WorkBatch<'a> {
    pub(super) client_epoch: u32,
    pub(super) sequence: u32,
    pub(super) requests: Vec<&'a [u8]>,
}

pub(super) fn sequenced_batch(data: &[u8]) -> Option<WorkBatch<'_>> {
    let mut reader = Reader(data);
    if data.len() > MESSAGE_LIMIT || reader.u8()? != 9 {
        return None;
    }
    let client_epoch = reader.u32()?;
    let sequence = reader.u32()?;
    if client_epoch == 0 {
        return None;
    }
    let mut requests = Vec::new();
    while !reader.0.is_empty() {
        let size = reader.u32()? as usize;
        let request = reader.take(size)?;
        if request.is_empty() {
            return None;
        }
        requests.push(request);
    }
    (!requests.is_empty()).then_some(WorkBatch { client_epoch, sequence, requests })
}

/// Return the acknowledged prefix of the actual unacknowledged window. An
/// old or forged ACK must neither drop unsent work nor advance this stream.
pub(super) fn acknowledged(data: &[u8], client_epoch: u32, first: u32, pending: usize) -> Option<usize> {
    let mut reader = Reader(data);
    if reader.u8()? != 12 || reader.u32()? != client_epoch {
        return None;
    }
    let count = reader.u32()?.wrapping_sub(first) as usize;
    (reader.0.is_empty() && count <= pending).then_some(count)
}

#[cfg(test)]
mod sequenced_tests {
    use super::*;

    fn frame(kind: u8, client_epoch: u32, sequence: u32, tail: &[u8]) -> Vec<u8> {
        let mut bytes = vec![kind];
        bytes.extend_from_slice(&client_epoch.to_le_bytes());
        bytes.extend_from_slice(&sequence.to_le_bytes());
        bytes.extend_from_slice(tail);
        bytes
    }

    #[test]
    fn work_requires_an_exact_fully_bounded_batch() {
        let bytes = frame(9, 31, 1, &[1, 0, 0, 0, 4, 2, 0, 0, 0, 5, 6]);
        let batch = sequenced_batch(&bytes).unwrap();
        assert_eq!((batch.client_epoch, batch.sequence), (31, 1));
        assert_eq!(batch.requests, vec![&[4][..], &[5, 6][..]]);
        assert!(sequenced_batch(&bytes[..bytes.len() - 1]).is_none());
        assert!(sequenced_batch(&frame(9, 31, 0, &[])).is_none());
        assert!(sequenced_batch(&frame(9, 31, 0, &[0; 4])).is_none());
        assert!(sequenced_batch(&frame(9, 0, 0, &[1, 0, 0, 0, 4])).is_none());
        assert!(sequenced_batch(&[9; MESSAGE_LIMIT + 1]).is_none());
    }

    #[test]
    fn ack_accepts_only_the_current_sent_prefix_even_across_wrap() {
        let first = u32::MAX;
        assert_eq!(acknowledged(&frame(12, 31, first, &[]), 31, first, 2), Some(0));
        assert_eq!(acknowledged(&frame(12, 31, 1, &[]), 31, first, 2), Some(2));
        assert_eq!(acknowledged(&frame(12, 31, 2, &[]), 31, first, 2), None);
        assert_eq!(acknowledged(&frame(12, 32, 0, &[]), 31, first, 2), None);
        assert_eq!(acknowledged(&frame(12, 31, 0, &[0]), 31, first, 2), None);
    }
}
