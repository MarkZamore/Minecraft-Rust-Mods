"""Expose and import the exact renderer items needed by copied SDK functions."""

from legacy_native_hooks import replace_once


SOURCE = "crates/horizon-client-render/src/"


def public(declaration):
    body = declaration.lstrip()
    indent = declaration[:len(declaration) - len(body)]
    visible = body.removeprefix("pub(crate) ").removeprefix("pub(super) ")
    return "\n" + declaration, "\n" + indent + "pub " + visible


PATCH_EXPORTS = {
    SOURCE + "options_ui.rs": (
        "012b7e2e2cda62ca1667152e415be4a39857f3585a9b9493329f78532bd1f7d8",
        tuple(map(public, (
            "pub(crate) use widgets::{button, inactive};",
            "fn tr(key: &str) -> Text {",
            "fn horizon_extras() -> bool {",
            "fn key_conflicts(",
            "fn section_title(text: Text, y: f32, w: f32) -> Widget {",
            "fn binding_text(key: &str, modifiers: u32) -> Text {",
            "fn option_widget(s: &Settings, opt: Opt, x: f32, y: f32, w: f32) -> Widget {",
            "    bands: Vec<[f32; 2]>,",
            "    fn begin(&mut self, out: &[Widget], y: f32) {",
            "    fn end(&mut self, out: &mut [Widget], y: f32) {",
            "    fn title(page: Page) -> &'static str {",
            "    fn list_rows(&self, s: &Settings) -> Vec<Row> {",
            "    fn heading_row(&self, out: &mut Vec<Widget>, label: Text, y: &mut f32, w: f32, top: f32) {",
            "    fn clamp_scroll(&mut self, top: f32, bottom: f32, content: f32) {",
        ))),
    ),
    SOURCE + "options_ui_widgets.rs": (
        "25f4a48776ce58a13da3a09d6c0fffb60c2bba61750ea61b11df034469b4053a",
        (
            public("pub(crate) fn button(x: f32, y: f32, w: f32, label: Text, act: Act) -> Widget {"),
            public("pub(crate) fn inactive(mut w: Widget) -> Widget {"),
        ),
    ),
    SOURCE + "mesher/mod_geometry.rs": (
        "6ebf3c71f4195a6ee8bddc87b3390479c93c7af87417d4d198f292357774e48a",
        (public("pub(super) fn add(mesher: &Mesher<'_>, tints: &mut Tints<'_>, out: &mut SectionMesh) {"),),
    ),
    SOURCE + "mesher/fluid.rs": (
        "f80a9543be757674252f80d9c9c0eff2e423433bd1cea2197ebdc5956ec69307",
        (public("pub(super) fn tesselate("),),
    ),
    SOURCE + "mesher/leaf_culling.rs": (
        "682ee58cca92b413ef1b4deb04d6fc2a82352bef3738bec43746740012a3d492",
        (
            public("    pub(super) fn prepare("),
            public("    pub(super) fn interior(&self, mode: LeafCullingMode, x: i32, y: i32, z: i32) -> bool {"),
            public("pub(super) fn in_section(x: i32, y: i32, z: i32) -> bool {"),
            public("pub(super) fn backed("),
        ),
    ),
}


def adapt_render(text: str) -> str:
    """Bind the options mirror directly to the SDK layout's types and helpers."""
    sdk = "::horizon_client_render::options_ui"
    module = "pub mod options_ui {\n"
    members = (
        "Act", "ListRows", "Row", "WKind", "Widget", "binding_text", "horizon_extras",
        "key_conflicts", "option_widget", "section_title", "tr",
    )
    missing = [name for name in members if f"    pub use {sdk}::{name};\n" not in text]
    imports = "".join(f"    pub use {sdk}::{name};\n" for name in missing)
    text = replace_once(text, module, module + imports)
    old_widgets = "    pub(crate) use widgets::{button, inactive};\n"
    public_widgets = "    pub use widgets::{button, inactive};\n"
    text = replace_once(text, old_widgets if old_widgets in text else public_widgets,
                        f"    pub use {sdk}::{{button, inactive}};\n")
    text = replace_once(text, "    pub(crate) use values::{chat_delay_label, chat_px, generic, on_off, percent};\n", "")
    return replace_once(text, "        gamepad::{self, GlyphStyle},\n", "")
