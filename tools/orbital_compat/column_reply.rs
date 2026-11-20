// Compact the canonical column reply with Horizon's bounded zlib codec.
use horizon_protocol::{buf::Reader, frame};
use std::cell::RefCell;

pub(crate) const COLUMNS: u8 = 11;
pub(crate) const COMPRESSED_COLUMNS: u8 = 13;
pub(crate) const MAX_PLAIN: usize = 1 << 20;

thread_local! {
    static INFLATER: RefCell<frame::Inflater> = RefCell::new(frame::Inflater::default());
}

pub(crate) fn pack(plain: Vec<u8>, packet_limit: usize) -> Option<Vec<u8>> {
    if plain.first() != Some(&COLUMNS) || plain.len() > MAX_PLAIN {
        return None;
    }
    if plain.len() <= packet_limit {
        return Some(plain);
    }
    let compressed = frame::contents(&plain, Some(0)).ok()?;
    if compressed.len() >= packet_limit {
        return None;
    }
    let mut packet = Vec::with_capacity(compressed.len() + 1);
    packet.push(COMPRESSED_COLUMNS);
    packet.extend(compressed);
    Some(packet)
}

pub(crate) fn unpack(packet: &[u8]) -> Option<Vec<u8>> {
    let (&COMPRESSED_COLUMNS, compressed) = packet.split_first()? else {
        return None;
    };
    let plain_size = usize::try_from(Reader::new(compressed).varint().ok()?).ok()?;
    if !(1..=MAX_PLAIN).contains(&plain_size) {
        return None;
    }
    INFLATER.with(
        |cell| match frame::body(compressed, Some(0), &mut cell.borrow_mut()).ok()? {
            frame::Body::Inflated(plain) if plain.first() == Some(&COLUMNS) => Some(plain),
            _ => None,
        },
    )
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn full_size_land_read_fits_one_peer_packet() {
        let mut plain = vec![COLUMNS];
        for col in 0..3000_u32 {
            plain.extend((col % 12 + 64).to_le_bytes());
            plain.push(1);
            for y in 0..35_u16 {
                let state = match y {
                    0..=26 => 0_u16,
                    27..=32 => 1,
                    33 => 2,
                    _ => (col % 4 + 3) as u16,
                };
                plain.extend(state.to_le_bytes());
            }
            plain.extend([7_u8; 12]);
        }
        assert!(plain.len() > 250_000);
        let packet = pack(plain.clone(), 28_000).unwrap();
        assert!(packet.len() < 28_000);
        assert_eq!(unpack(&packet), Some(plain));
        assert!(unpack(&packet[..packet.len() - 1]).is_none());
    }

    #[test]
    fn small_reply_stays_plain_and_oversized_or_wrong_kind_is_refused() {
        let small = vec![COLUMNS, 2, 3, 4];
        assert_eq!(pack(small.clone(), 28_000), Some(small));
        assert!(pack(vec![COLUMNS; MAX_PLAIN + 1], 28_000).is_none());
        let mut packet = vec![COMPRESSED_COLUMNS];
        packet.extend(frame::contents(&vec![COLUMNS; MAX_PLAIN + 1], Some(0)).unwrap());
        assert!(unpack(&packet).is_none());
        let mut packet = vec![COMPRESSED_COLUMNS];
        packet.extend(frame::contents(&[12, 0], Some(0)).unwrap());
        assert!(unpack(&packet).is_none());
    }
}
