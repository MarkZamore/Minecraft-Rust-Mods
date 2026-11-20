// Keep exact row semantics and only divide a dense read if the packed reply
// cannot fit one existing wire packet. No new terrain or read limits.
fn answer_columns(
    session: &payload::Session,
    tag: u32,
    offset: u32,
    total: u32,
    span: usize,
    read: &NativeColumnBlocks,
    count: usize,
) {
    let palette_bytes = read.palette.iter().map(|entry| entry.len() + 4).sum::<usize>();
    let rows = orbital_column_reply::MAX_PLAIN.saturating_sub(palette_bytes + 32) / (17 + span * 2);
    if rows == 0 {
        log::warn!("[mod {ID}] column palette cannot fit a bounded reply ({} bytes)", palette_bytes);
        return;
    }
    for start in (0..count.max(1)).step_by(rows) {
        let mut fragments = vec![start..(start + rows).min(count)];
        while let Some(fragment) = fragments.pop() {
            let mut writer = Writer::new(orbital_column_reply::COLUMNS);
            writer.u32(tag);
            writer.u32(offset + fragment.start as u32);
            writer.u32(total);
            writer.u32(span as u32);
            writer.strs(&read.palette);
            writer.u32(fragment.len() as u32);
            for column in fragment.clone() {
                let base = read.bases.get(column).copied().flatten();
                writer.0.push(u8::from(base.is_some()));
                writer.i32(base.unwrap_or(0));
                for y in 0..span {
                    let state = read.blocks.get(column * span + y).copied().unwrap_or(0);
                    writer.0.extend(state.to_le_bytes());
                }
                for tint in 0..3 {
                    writer.u32(read.tints.get(column * 3 + tint).copied().unwrap_or(0));
                }
            }
            if let Some(packet) = orbital_column_reply::pack(writer.0, MESSAGE) {
                answer(session, packet);
            } else if fragment.len() > 1 {
                let middle = fragment.start + fragment.len() / 2;
                fragments.push(middle..fragment.end);
                fragments.push(fragment.start..middle);
            } else {
                log::warn!("[mod {ID}] column reply cannot fit the negotiated packet");
            }
        }
    }
}
