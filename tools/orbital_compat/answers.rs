/// Answers belong only to a query this window sent through its local server.
pub(super) fn from_server(game: &mut Game<'_>, channel: &str, data: &[u8]) {
    if channel != ANSWER
        || !game.native_window()
        || game.integrated.is_none()
        || data.len() > wire::MESSAGE_LIMIT
    {
        return;
    }
    let mut r = Reader(data);
    let done = CLIENT.with(|c| -> Option<()> {
        let mut c = c.borrow_mut();
        let kind = r.u8()?;
        let (tag, offset, total) = (r.u32()?, r.u32()? as usize, r.u32()? as usize);
        match kind {
            10 => {
                let expected = *c.pending_surface.get(&tag)?;
                let n = r.u32()? as usize;
                let seen = c.surface.get(&tag).map(|entry| entry.1.as_slice());
                let range = wire::answer_part(expected, total, offset, n, 5, r.0, seen)?;
                let heights: Vec<_> = (0..n).map(|_| r.height()).collect::<Option<_>>()?;
                let entry = c.surface.entry(tag).or_insert_with(|| (vec![None; total], vec![false; total]));
                entry.0[range.clone()].copy_from_slice(&heights);
                entry.1[range].fill(true);
                if entry.1.iter().all(|&b| b) {
                    let (heights, _) = c.surface.remove(&tag)?;
                    c.pending_surface.remove(&tag);
                    host::with(|s| s.surface.push(world::SurfaceHeights { tag, heights }));
                }
            }
            11 => {
                let (expected, span) = *c.pending_columns.get(&tag)?;
                if r.u32()? as usize != span {
                    return None;
                }
                let palette = r.strs()?;
                let n = r.u32()? as usize;
                let seen = c.columns.get(&tag).map(|entry| entry.seen.as_slice());
                let range = wire::answer_part(expected, total, offset, n, 17 + span * 2, r.0, seen)?;
                let mut check = Reader(r.0);
                for _ in range.clone() {
                    check.height()?;
                    for _ in 0..span {
                        let block = check.u16()? as usize;
                        if block >= palette.len() && !(palette.is_empty() && block == 0) {
                            return None;
                        }
                    }
                    check.take(12)?;
                }
                let entry = c.columns.entry(tag).or_insert_with(|| Columns {
                    seen: vec![false; total],
                    bases: vec![None; total],
                    blocks: vec![0; total * span],
                    tints: vec![0; total * 3],
                    ..Columns::default()
                });
                let added = palette
                    .iter()
                    .filter(|name| !entry.index.contains_key(*name))
                    .collect::<std::collections::BTreeSet<_>>();
                if entry.palette.len() + added.len() > usize::from(u16::MAX) + 1 {
                    return None;
                }
                let map: Vec<u16> = palette
                    .into_iter()
                    .map(|name| {
                        if let Some(&i) = entry.index.get(&name) {
                            return i;
                        }
                        let i = entry.palette.len() as u16;
                        entry.palette.push(name.clone());
                        entry.index.insert(name, i);
                        i
                    })
                    .collect();
                for k in range.clone() {
                    entry.bases[k] = r.height()?;
                    for j in 0..span {
                        let block = r.u16()? as usize;
                        entry.blocks[k * span + j] = map.get(block).copied().unwrap_or(0);
                    }
                    for t in 0..3 {
                        entry.tints[k * 3 + t] = r.u32()?;
                    }
                }
                entry.seen[range].fill(true);
                entry.got += n;
                if entry.got == total {
                    let e = c.columns.remove(&tag)?;
                    c.pending_columns.remove(&tag);
                    host::with(|s| {
                        s.columns.push(world::ColumnBlocks {
                            tag,
                            palette: e.palette,
                            bases: e.bases,
                            blocks: e.blocks,
                            tints: e.tints,
                        })
                    });
                }
            }
            _ => return None,
        }
        Some(())
    });
    if done.is_none() {
        log::warn!("[mod {ID}] a bad answer from the server half");
    }
}
