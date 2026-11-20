"""Expose the Horizon ClientLib helpers called by the external patch crate."""


PATCH_EXPORTS = {
    "crates/horizon-client/src/living_attributes.rs": (
        "1fee2ae1a2aca3afc6633c4da0f0ec2b41bd2076d9f422a235e0258bfd00ab76",
        (("pub(crate) fn attribute_error(error: impl fmt::Debug) -> io::Error {",
          "pub fn attribute_error(error: impl fmt::Debug) -> io::Error {"),),
    ),
    "crates/horizon-client/src/mobs/model.rs": (
        "bd14f5204fcb00f2426bb7bc6ad2b8b1e73d47b694024000d0141a574cd1c4ff",
        (
            ("    pub(super) fn part(mut self, pose: [f32; 9]) -> Self {",
             "    pub fn part(mut self, pose: [f32; 9]) -> Self {"),
            ("    pub(super) fn vertex_normal(&self, pose: ModelNormals) -> [i8; 4] {",
             "    pub fn vertex_normal(&self, pose: ModelNormals) -> [i8; 4] {"),
        ),
    ),
    "crates/horizon-client/src/realms.rs": (
        "f8b9e02a31c1cf8bbaa41d7cf3d350bb34e08a6d537580f76ca458870eabee34",
        (("    pub(crate) fn reset_tile_at(&self, x: f32, y: f32, (w, h): (f32, f32)) -> Option<usize> {",
          "    pub fn reset_tile_at(&self, x: f32, y: f32, (w, h): (f32, f32)) -> Option<usize> {"),),
    ),
    "crates/horizon-client/src/title/buffet.rs": (
        "1e6e666edc73305d59d81058cad3f4c6f195b15fe6ba41ae1a842e0fb3fe9ef0",
        (("pub(super) fn draw(\n", "pub fn draw(\n"),),
    ),
    "crates/horizon-client/src/title/create_world.rs": (
        "7a33cf6cb260c99a840ad361b1cc0dc0ca8872656d3531c3882149a532d594e3",
        (
            ("pub(super) fn tab_at(w: f32, (x, y): (f32, f32)) -> Option<usize> {",
             "pub fn tab_at(w: f32, (x, y): (f32, f32)) -> Option<usize> {"),
            ("    pub(super) fn draw_create(\n", "    pub fn draw_create(\n"),
            ("    pub(super) fn create_tooltip(\n", "    pub fn create_tooltip(\n"),
        ),
    ),
    "crates/horizon-client/src/title/edit_world.rs": (
        "8d787fe25e29e6e9bb3b4843892ff09978a6332634ed07bd7c6d11f40c87cc52",
        (
            ("    pub(super) fn draw_edit(\n", "    pub fn draw_edit(\n"),
            ("    pub(super) fn draw_adopt_regions(\n", "    pub fn draw_adopt_regions(\n"),
        ),
    ),
    "crates/horizon-client/src/title/flat.rs": (
        "cbdd2f12d94ebea6cf786ad79dbc2d07f7863c862b9cf30596ce27b39302975a",
        (("pub(super) fn draw(\n", "pub fn draw(\n"),),
    ),
    "crates/horizon-client/src/title/missing_biome_regions.rs": (
        "e83bb05ab990409bb2a51db342ef4e3a1903dcc2b4ba189c0ce541e38c9681fc",
        (("    pub(super) fn draw_missing_biome_regions(\n", "    pub fn draw_missing_biome_regions(\n"),),
    ),
    "crates/horizon-client/src/title/missing_mods.rs": (
        "a9868e452ad2103974509488aeaea49b8417159e34ea39e87c82078dd65b6691",
        (("    pub(super) fn draw_missing_mods(\n", "    pub fn draw_missing_mods(\n"),),
    ),
    "crates/horizon-client/src/title/onboarding.rs": (
        "7a7b45840d477637596c66766af56819d34ec556a749f2335163c0fe74d1d443",
        (
            ("    pub(super) fn draw_onboarding(\n", "    pub fn draw_onboarding(\n"),
            ("    pub(super) fn draw_onboarding_logo(\n", "    pub fn draw_onboarding_logo(\n"),
        ),
    ),
    "crates/horizon-client/src/title/preset_buttons.rs": (
        "c8cf069c5c962c05f7fcd6dd22fc1276d5beeeb4c1e386475a70955ccf76870f",
        (("    pub(super) fn buttons(&self, w: f32, h: f32) -> Vec<Button> {",
          "    pub fn buttons(&self, w: f32, h: f32) -> Vec<Button> {"),),
    ),
}
