#[cfg(test)]
mod pack_network_atlas_tests {
    use super::*;
    #[test]
    fn network_repack_uses_receivers_layers_and_keeps_block_identity_and_biome_tints() {
        crate::sdk::fake::atlas::network_test_atlas(5);
        let block = hu_block("minecraft:grass_block").expect("canonical block");
        let mut data = vec![0; HU_BK + HU_BKW];
        data[3] = 1;
        data[HU_BK..HU_BK + HU_BKW].copy_from_slice(&bk_words(block, 0x123456, 0x789abc));
        let before = data.clone();
        assert_eq!((data[HU_BK + 2] >> 2) & 8191, 5);
        crate::sdk::fake::atlas::network_test_atlas(71);
        crate::network_repack(9, &mut data);
        assert_eq!((data[HU_BK + 2] >> 2) & 8191, 71);
        assert_eq!(data[HU_BK + 1] >> 16, before[HU_BK + 1] >> 16);
        assert_eq!(data[HU_BK + HU_BKW - 1], before[HU_BK + HU_BKW - 1]);
        let repacked = data.clone();
        crate::network_repack(0, &mut data);
        assert_eq!(data, repacked, "non-atlas laser data is unchanged");
    }
}
