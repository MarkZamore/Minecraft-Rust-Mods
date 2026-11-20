use super::{convergence, hu_blocks::HU_BLOCKS, hurricane, sb_lift, tsunami, we_body, we_fx, we_path};
use hurricane::HU_BKW;

pub(crate) fn uses_atlas(shader: u8) -> bool {
    matches!(shader, 9 | 12 | 13 | 15 | 16)
}

pub(crate) fn repack(shader: u8, data: &mut [u32]) {
    match shader {
        9 => hurricane::hu_repack(data),
        12 if data.get(3) == Some(&(tsunami::TS_KINDS.len() as u32)) => {
            repack_palette(data, tsunami::TSB, tsunami::TS_KINDS.len(), false);
        }
        13 => repack_island(data),
        15 => {
            repack_world_eater(data, we_path::WE_BODY_T, we_body::WB_BK0, we_body::WB_BLOCKS.len());
            repack_world_eater(data, we_path::WE_FX_T + we_fx::WEF_BK, we_fx::WEF_BK0, we_fx::WEF_BLOCKS.len());
        }
        16 => repack_convergence(data),
        _ => {}
    }
}

fn repack_island(data: &mut [u32]) {
    let Some(&[kind, count]) = data.get(sb_lift::SB_LF..sb_lift::SB_LF + 2) else {
        return;
    };
    let count = f32::from_bits(count);
    if matches!(f32::from_bits(kind), 1.0 | 2.0)
        && (1.0..=sb_lift::LF_NK as f32).contains(&count)
        && count.fract() == 0.0
    {
        repack_palette(data, sb_lift::LF_PAL, count as usize, true);
    }
}

fn repack_world_eater(data: &mut [u32], header: usize, palette: usize, count: usize) {
    if data.get(header..header + 2) == Some(&[count as u32, 1.0f32.to_bits()]) {
        repack_palette(data, header + palette, count, false);
    }
}

fn repack_convergence(data: &mut [u32]) {
    if data.first() != Some(&convergence::CV_MAGIC.to_bits()) {
        return;
    }
    let start = convergence::CV_SPR;
    let Some(words) = data.get_mut(start..start + 1 + convergence::CV_SPRITES.len()) else {
        return;
    };
    // A missing local sprite uses the author's own color fallback, never the
    // unrelated layer with the same number in the receiver's atlas.
    if let Some(layers) = convergence::cv_sprites() {
        words[0] = 1.0f32.to_bits();
        words[1..].copy_from_slice(&layers);
    } else {
        words.fill(0);
    }
}

fn unpack_tint(bits: u32) -> u32 {
    let (r, g, b) = ((bits >> 11) & 31, (bits >> 5) & 63, bits & 31);
    (r << 3 | r >> 2) << 16 | (g << 2 | g >> 4) << 8 | (b << 3 | b >> 2)
}

fn repack_palette(data: &mut [u32], start: usize, count: usize, plants: bool) {
    let Some(palette) = data.get_mut(start..start + count * HU_BKW) else {
        return;
    };
    for words in palette.chunks_exact_mut(HU_BKW) {
        if words.iter().all(|&word| word == 0) {
            continue;
        }
        let cross = plants && words[1] >> 31 != 0;
        let mut block = (words[1] >> 16) as u16;
        if cross {
            block &= 0x7fff;
        }
        if usize::from(block) >= HU_BLOCKS.len() {
            continue;
        }
        let tint = words[HU_BKW - 1];
        let grass = unpack_tint(tint & 0xffff);
        let foliage = unpack_tint(tint >> 16);
        let mut rebuilt = hurricane::bk_words(block, grass, foliage);
        if cross {
            rebuilt[2] = sb_lift::cross_of(block, grass, foliage).unwrap_or(0);
            rebuilt[3] = 0;
            rebuilt[4] = words[4];
        }
        words[2..HU_BKW - 1].copy_from_slice(&rebuilt[2..HU_BKW - 1]);
    }
}

#[cfg(test)]
mod pack_network_atlas_tests {
    use super::*;
    use crate::sdk::fake::atlas::network_test_atlas;

    fn sample(name: &str) -> [u32; HU_BKW] {
        hurricane::bk_words(hurricane::hu_block(name).expect("canonical block"), 0x123456, 0x789abc)
    }

    fn sample_layer(word: u32) -> u32 {
        (word >> 2) & 8191
    }

    fn verify_palette(data: &mut [u32], shader: u8, start: usize, name: &str) {
        let header = data[..start].to_vec();
        let original = data[start..start + HU_BKW].to_vec();
        assert_eq!(sample_layer(original[2]), 5);
        network_test_atlas(71, &[name]);
        crate::network_repack(shader, data);
        assert_eq!(sample_layer(data[start + 2]), 71);
        assert_eq!(data[start + 1], original[1]);
        assert_eq!(data[start + HU_BKW - 1], original[HU_BKW - 1]);
        assert_eq!(data[..start], header);
    }

    #[test]
    fn tsunami_maps_debris_palette_without_changing_land_map() {
        let name = "minecraft:grass_block";
        network_test_atlas(5, &[name]);
        let mut data = vec![0; tsunami::TS_WORDS];
        data[3] = tsunami::TS_KINDS.len() as u32;
        data[tsunami::TSM] = 456;
        let start = tsunami::TSB;
        data[start..start + HU_BKW].copy_from_slice(&sample(name));
        verify_palette(&mut data, 12, start, name);
        assert_eq!(data[tsunami::TSM], 456);
    }

    #[test]
    fn sky_break_keeps_crossed_plants_and_uses_receivers_texture() {
        let name = "minecraft:short_grass";
        network_test_atlas(5, &[name]);
        let mut data = vec![0; sb_lift::LF_PAL + HU_BKW];
        data[sb_lift::SB_LF] = 1.0f32.to_bits();
        data[sb_lift::SB_LF + 1] = 1.0f32.to_bits();
        let start = sb_lift::LF_PAL;
        let mut plant = sample(name);
        let block = hurricane::hu_block(name).unwrap();
        plant[1] |= 1 << 31;
        plant[2] = sb_lift::cross_of(block, 0x123456, 0x789abc).unwrap();
        plant[3] = 0;
        plant[4] = 1 | 2 << 2 | 2 << 4 | 1 << 6 | 1 << 8;
        data[start..].copy_from_slice(&plant);
        verify_palette(&mut data, 13, start, name);
        assert_eq!(data[start + 3], 0);
        assert_eq!(data[start + 4], plant[4]);
    }

    #[test]
    fn world_eater_rebuilds_both_body_and_debris_palettes() {
        let name = "minecraft:grass_block";
        network_test_atlas(5, &[name]);
        let debris = we_path::WE_FX_T + we_fx::WEF_BK;
        let body = we_path::WE_BODY_T;
        let mut data = vec![0; debris + we_fx::WEF_BK0 + HU_BKW * we_fx::WEF_BLOCKS.len()];
        for (header, index, count) in [(body, we_body::WB_BK0, we_body::WB_BLOCKS.len()),
                                       (debris, we_fx::WEF_BK0, we_fx::WEF_BLOCKS.len())] {
            data[header] = count as u32;
            data[header + 1] = 1.0f32.to_bits();
            let start = header + index;
            data[start..start + HU_BKW].copy_from_slice(&sample(name));
        }
        verify_palette(&mut data, 15, body + we_body::WB_BK0, name);
        assert_eq!(sample_layer(data[debris + we_fx::WEF_BK0 + 2]), 71);
    }

    #[test]
    fn convergence_uses_local_island_layers_and_falls_back_if_missing() {
        let mut data = vec![0; convergence::CV_SPR + 1 + convergence::CV_SPRITES.len()];
        data[0] = convergence::CV_MAGIC.to_bits();
        let start = convergence::CV_SPR;
        data[start] = 1.0f32.to_bits();
        data[start + 1..].fill(5);
        network_test_atlas(71, &[]);
        crate::network_repack(16, &mut data);
        assert!(data[start..].iter().all(|&word| word == 0));
        network_test_atlas(71, &convergence::CV_SPRITES);
        crate::network_repack(16, &mut data);
        assert_eq!(data[start], 1.0f32.to_bits());
        assert!(data[start + 1..].iter().all(|&word| word == 71));
    }

    #[test]
    fn truncated_or_unrelated_tables_are_ignored() {
        let mut short = [0; 4];
        for shader in [9, 12, 13, 15, 16] {
            assert!(crate::network_uses_atlas(shader));
            crate::network_repack(shader, &mut short);
        }
        assert_eq!(short, [0; 4]);
        assert!(!crate::network_uses_atlas(18));
    }
}
