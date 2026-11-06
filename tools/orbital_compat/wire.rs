use std::ops::Range;

/// Horizon permits 30,000 bytes and 32 messages per player per server tick.
pub(super) const MESSAGE_LIMIT: usize = 30_000;
pub(super) const READ_COLUMNS: usize = 3_000;
pub(super) const QUERY_COLUMNS: usize = 1 << 16;
const QUERY_BLOCKS: usize = 1 << 20;

pub(super) struct Reader<'a>(pub(super) &'a [u8]);

impl<'a> Reader<'a> {
    pub(super) fn take(&mut self, count: usize) -> Option<&'a [u8]> {
        let (head, rest) = self.0.split_at_checked(count)?;
        self.0 = rest;
        Some(head)
    }

    pub(super) fn u8(&mut self) -> Option<u8> {
        Some(*self.take(1)?.first()?)
    }

    pub(super) fn u32(&mut self) -> Option<u32> {
        Some(u32::from_le_bytes(self.take(4)?.try_into().ok()?))
    }

    pub(super) fn u16(&mut self) -> Option<u16> {
        Some(u16::from_le_bytes(self.take(2)?.try_into().ok()?))
    }

    pub(super) fn i32(&mut self) -> Option<i32> {
        Some(i32::from_le_bytes(self.take(4)?.try_into().ok()?))
    }

    pub(super) fn f32(&mut self) -> Option<f32> {
        Some(f32::from_le_bytes(self.take(4)?.try_into().ok()?))
    }

    pub(super) fn count(&mut self, minimum_bytes: usize) -> Option<usize> {
        let count = self.u32()? as usize;
        (count <= self.0.len() / minimum_bytes).then_some(count)
    }

    fn string(&mut self) -> Option<String> {
        let len = self.u32()? as usize;
        Some(std::str::from_utf8(self.take(len)?).ok()?.to_owned())
    }

    pub(super) fn strs(&mut self) -> Option<Vec<String>> {
        let count = self.count(4)?;
        (0..count).map(|_| self.string()).collect()
    }

    pub(super) fn columns(&mut self) -> Option<Vec<(i32, i32)>> {
        let count = self.count(8)?;
        if count > READ_COLUMNS {
            return None;
        }
        (0..count).map(|_| Some((self.i32()?, self.i32()?))).collect()
    }

    pub(super) fn height(&mut self) -> Option<Option<i32>> {
        let present = self.u8()?;
        let height = self.i32()?;
        (present <= 1).then_some((present == 1).then_some(height))
    }
}

pub(super) fn batch(data: &[u8]) -> Option<Vec<&[u8]>> {
    let mut reader = Reader(data);
    if data.len() > MESSAGE_LIMIT || reader.u8()? != 9 {
        return None;
    }
    let mut requests = Vec::new();
    while !reader.0.is_empty() {
        let length = reader.u32()? as usize;
        let request = reader.take(length)?;
        if request.is_empty() {
            return None;
        }
        requests.push(request);
    }
    (!requests.is_empty()).then_some(requests)
}

pub(super) fn fragment(total: usize, offset: usize, count: usize) -> Option<Range<usize>> {
    let end = offset.checked_add(count)?;
    if total > QUERY_COLUMNS || count > READ_COLUMNS || end > total || (count == 0 && total != 0) {
        return None;
    }
    Some(offset..end)
}

pub(super) fn span(total: usize, below: u32, above: u32, max_height: i32) -> Option<usize> {
    let span = below.checked_add(above)? as usize;
    if span == 0 || span > usize::try_from(max_height).ok()? || total.checked_mul(span)? > QUERY_BLOCKS {
        return None;
    }
    Some(span)
}

pub(super) fn answer_part(
    expected: usize,
    total: usize,
    offset: usize,
    count: usize,
    row_bytes: usize,
    payload: &[u8],
    received: Option<&[bool]>,
) -> Option<Range<usize>> {
    if total != expected || payload.len() != count.checked_mul(row_bytes)? {
        return None;
    }
    let range = fragment(total, offset, count)?;
    if received.is_some_and(|seen| seen[range.clone()].iter().any(|&got| got)) {
        return None;
    }
    Some(range)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn rejects_wire_counts_without_their_bytes() {
        assert!(Reader(&u32::MAX.to_le_bytes()).count(25).is_none());
        assert!(Reader(&u32::MAX.to_le_bytes()).strs().is_none());
        assert!(Reader(&u32::MAX.to_le_bytes()).columns().is_none());
        assert!(batch(&[9, 1, 0, 0, 0, 4, 1]).is_none());
        assert_eq!(batch(&[9, 1, 0, 0, 0, 4]), Some(vec![&[4][..]]));
        assert!(batch(&vec![9; MESSAGE_LIMIT + 1]).is_none());
    }

    #[test]
    fn only_matching_new_fragments_fit_a_pending_query() {
        let seen = [true, false];
        assert_eq!(answer_part(2, 2, 1, 1, 5, &[0; 5], Some(&seen)), Some(1..2));
        assert!(answer_part(2, usize::MAX, 1, 1, 5, &[0; 5], Some(&seen)).is_none());
        assert!(answer_part(2, 2, 0, 1, 5, &[0; 5], Some(&seen)).is_none());
        assert!(answer_part(2, 2, 1, 1, 5, &[0; 4], Some(&seen)).is_none());
        assert!(answer_part(2, 2, usize::MAX, 1, 5, &[0; 5], Some(&seen)).is_none());
        assert_eq!(answer_part(0, 0, 0, 0, 5, &[], None), Some(0..0));
    }

    #[test]
    fn column_span_preserves_engine_limits() {
        assert_eq!(span(QUERY_COLUMNS, 8, 8, 384), Some(16));
        assert!(span(QUERY_COLUMNS, 8, 9, 384).is_none());
        assert!(span(1, 384, 1, 384).is_none());
        assert!(span(1, u32::MAX, 2, 384).is_none());
        assert!(span(1, 0, 0, 384).is_none());
    }
}
