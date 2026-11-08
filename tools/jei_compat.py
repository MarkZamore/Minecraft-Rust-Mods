"""Repair lifecycle of the verified JEI for Horizon 0.1.0 native viewer."""

import hashlib
from pathlib import Path


SOURCE_HASHES = {
    "glue.rs": "3b531b4d6c67d5da7c22fc62ac4de073c7f069094cdcfa2d48bf484d8748d7cf",
    "runtime.rs": "7436912434a65143bc5b6c2fc3a7321a03ec23bea79e0ccea9fe60d660e1e8c8",
}

# Official 2ebf00a sources, with game.rs after the immutable Edition 5ed0ee3
# common Steam/notice recipe. Recheck this pin when that common recipe changes.
HOST_SOURCE_HASHES = {
    "crates/horizon-client/src/game.rs": "e3791cb180e09c20c5972d7489993e279b0c588bd6d2804a5aad17bb2c7e8dd6",
    "crates/horizon-client/src/game/native_panel.rs": "7ea0845828a67489a4b667582d039ed8985bafea1d1a4fe6841c87207e1733e3",
    "crates/horizon-client/src/game/native_text_field.rs": "b88ea7a33256a3a935f90f7173bdb76370e40e6a14a95869966833a6aa462ff2",
    "crates/horizon-client/src/game/pad_menu.rs": "66767aaf202c6995fc6a93bd326905259423fab02534ac54f99a93c99ddb71cc",
    "crates/horizon-client/src/game/native_panel_recipes.rs": "3504b2c7f00dffae5c19f10b4f0ddf3cb7a5d6529b86ea0b58ba0fb9bea0e038",
    "crates/horizon-client/src/game/control.rs": "10a22932d9d35224e860a41aed6c8e02bf991c779cfd4edb0f4572e1b99288b1",
    "crates/horizon-client/src/game/ime.rs": "25a29233992630c0ef30427949005bb2b9ed09d7556f91aa7081f18ea2530488",
}


def replace_once(source: str, before: str, after: str) -> str:
    if source.count(before) != 1:
        raise ValueError(f"Expected one JEI 0.1.0 source anchor: {before[:100]!r}")
    return source.replace(before, after)


def adapt_host_game(source: str) -> str:
    # Keep the new author browser and pad-guide rendering sequence verbatim.
    author_draw = '''            Screen::Creative => {
                let from = batch.vertices.len();
                self.draw_creative(&mut batch);
                self.draw_mod_browser(&mut batch);
                self.draw_pad_menu_guide(&mut batch, from);
            }'''
    if source.count(author_draw) != 1:
        raise ValueError("Horizon Creative browser/pad guide path changed")
    source = replace_once(source, '''    fn click_creative(&mut self, right: bool) {
        if self.mod_browser_press(right) {
            return;
        }''', '''    fn click_creative(&mut self, right: bool) {
        if self.mod_browser_press(right) {
            if self.jei_panel_owner() { self.inv_clicks.skip_release(); }
            return;
        }''')
    source = replace_once(source, '''            // CreativeModeInventoryScreen: an unbordered EditBox of 50 at (82, 6).
            let search = &mut self.creative_screen.search;''', '''            // CreativeModeInventoryScreen: an unbordered EditBox of 50 at (82, 6).
            let panel_typing = self.jei_panel_owner() && self.mod_browser_typing();
            let search = &mut self.creative_screen.search;''')
    source = replace_once(source, '''            search.bordered = false;
            search.focused = true;''', '''            search.bordered = false;
            search.focused = !panel_typing;''')
    source = replace_once(source, '''                    if self.screen == Screen::Creative
                        && self.creative_screen.tab().search
                        && !self.mod_browser_typing()
                        && event.state''', '''                    if self.screen == Screen::Creative
                        && self.creative_screen.tab().search
                        && !self.mod_browser_typing()
                        && !(self.jei_panel_owner() && self.mod_browser_hovered())
                        && event.state''')
    source = replace_once(source, "                    let bundle = bundles::takes_bundle_wheel(self.screen)\n",
        "                    let browser = self.mod_browser_scroll(d);\n                    let bundle = !browser && bundles::takes_bundle_wheel(self.screen)\n")
    source = replace_once(source, "                    if self.screen == Screen::Creative && d != 0.0 && !bundle {",
        "                    if self.screen == Screen::Creative && d != 0.0 && !bundle && !browser {")
    # Escape already blurs every browser upstream. E needs the JEI equivalent.
    anchor = '''                Screen::Creative => {
                    self.pad_clear_focus();
                    if let Some(c) = self.carried.take() {'''
    return replace_once(source, anchor, anchor.replace("self.pad_clear_focus();",
        "self.pad_clear_focus();\n                    if self.jei_panel_owner() { self.mod_browser_closed(); }"))


def adapt_host_panel(source: str) -> str:
    # Exempt only the selected JEI; keep built-in Recipes and other owners as authored.
    source = replace_once(source,
        '        if self.screen == Screen::Creative && of.0.native_id() != "recipes" {',
        '        if self.screen == Screen::Creative && !matches!(of.0.native_id(), "recipes" | "jei") {')
    source = replace_once(source, '''        matches!(self.screen, Screen::Inventory | Screen::Creative)
            && self.panel.of.is_some()''', '''        matches!(self.screen, Screen::Inventory | Screen::Creative)
            && self.title.is_none()
            && self.panel.of.is_some()''')
    source = replace_once(source, '''        } else {
            self.draw_slot_tooltip(batch);
        }
    }

    /// A mouse press''', '''        } else if self.screen == Screen::Inventory || !self.jei_panel_owner() {
            // Creative already drew its own tab-aware tooltip underneath JEI.
            self.draw_slot_tooltip(batch);
        }
    }

    /// A mouse press''')
    source = replace_once(source, "    pub(super) fn native_panel_key(&mut self, key: &str) {\n",
        '''    pub(super) fn native_panel_key(&mut self, key: &str) {
        if self.screen == Screen::Creative && self.jei_panel_owner() && !self.mod_browser_hovered() {
            return;
        }
''')
    return replace_once(source, "    /// Whether keys go to the panel's search box.", '''    /// Pack compatibility behavior belongs to JEI, not built-in Recipes.
    pub(super) fn jei_panel_owner(&self) -> bool {
        self.panel.shown(|id| self.native_on(id))
            .is_some_and(|shown| shown.owner.native_id() == "jei")
    }
    pub(super) fn mod_browser_hovered(&mut self) -> bool {
        if self.browser_sync().is_none() { return false; }
        let Some(batch) = self.browser_batch() else { return false; };
        self.browser_layout(&batch).is_some_and(|layout| inside(self.cursor, layout.area))
    }
    /// JEI alone takes Creative's wheel inside its own panel bounds.
    pub(super) fn mod_browser_scroll(&mut self, delta: f32) -> bool {
        if self.screen != Screen::Creative || !self.jei_panel_owner() { return false; }
        let Some(owner) = self.browser_sync() else { return false; };
        let Some(batch) = self.browser_batch() else { return false; };
        let Some(layout) = self.browser_layout(&batch) else { return false; };
        if !inside(self.cursor, layout.area) { return false; }
        if delta == 0.0 { return true; }
        if self.panel.recipes.is_some() {
            self.browser_recipes_scroll(&layout, &owner, delta);
        } else {
            let max = self.browser_pages(&layout).saturating_sub(1);
            self.panel.page = if delta > 0.0 {
                self.panel.page.saturating_sub(1)
            } else {
                self.panel.page.saturating_add(1).min(max)
            };
        }
        true
    }
    /// Whether keys go to the panel's search box.''')


def adapt_host_recipes(source: str) -> str:
    return replace_once(source, "    /// A press on the recipes: true when it hit one of their parts.", '''    /// A wheel turn over the creative mod panel uses the same page events
    /// as its arrows, leaving creative's underlying list alone.
    pub(super) fn browser_recipes_scroll(&mut self, layout: &Layout, owner: &Owner, delta: f32) {
        let Some(pages) = self.panel.recipes.as_ref() else { return; };
        let starts = Self::recipe_pages(pages, layout);
        let page = pages.page.min(starts.len() - 1);
        let to = if delta > 0.0 { page.saturating_sub(1) } else { (page + 1).min(starts.len() - 1) };
        if to != page {
            if let Some(pages) = self.panel.recipes.as_mut() { pages.page = to; }
            self.panel_event(owner, BrowserEvent::Page(starts[to] as u32));
        }
    }
    /// A press on the recipes: true when it hit one of their parts.''')


def prepare_source(workspace: Path) -> None:
    originals = {}
    for name, expected in HOST_SOURCE_HASHES.items():
        raw = (workspace / name).read_bytes()
        if hashlib.sha256(raw).hexdigest() != expected:
            raise ValueError(f"Horizon native panel source changed before JEI creative port: {name}")
        originals[name] = raw.decode("utf-8")
    base = "crates/horizon-client/src/"
    updated = {
        base + "game.rs": adapt_host_game(originals[base + "game.rs"]),
        base + "game/native_panel.rs": adapt_host_panel(originals[base + "game/native_panel.rs"]),
        base + "game/native_panel_recipes.rs": adapt_host_recipes(originals[base + "game/native_panel_recipes.rs"]),
    }
    text = originals[base + "game/native_text_field.rs"]
    text = replace_once(text, '            Screen::Creative if self.creative_screen.tab().search && !self.creative_ignore_text => {',
        '            Screen::Creative if self.creative_screen.tab().search && !self.creative_ignore_text && !(self.jei_panel_owner() && self.mod_browser_typing()) => {')
    text = replace_once(text, '            Screen::Inventory if self.mod_browser_typing() => {',
        '            screen if self.mod_browser_typing() && (screen == Screen::Inventory || (screen == Screen::Creative && self.jei_panel_owner())) => {')
    updated[base + "game/native_text_field.rs"] = text
    pad = originals[base + "game/pad_menu.rs"]
    updated[base + "game/pad_menu.rs"] = replace_once(pad,
        '        } else if self.screen == Screen::Creative && self.creative_screen.tab().search {',
        '        } else if self.screen == Screen::Creative && self.creative_screen.tab().search && !(self.jei_panel_owner() && self.mod_browser_typing()) {')
    # 2ebf already has correct Creative text/backspace and IME admission and
    # priority. Validate these author paths; no additional rewrite is needed.
    control = originals[base + "game/control.rs"]
    if control.count('Screen::Inventory | Screen::Creative if self.mod_browser_typing() => {') != 2:
        raise ValueError("Horizon Creative text/backspace paths changed")
    ime = originals[base + "game/ime.rs"]
    for anchor in (
        'Screen::Creative => self.mod_browser_typing() || self.creative_screen.tab().search,',
        'Screen::Inventory | Screen::Creative if self.mod_browser_typing() => {',
    ):
        if ime.count(anchor) != 1:
            raise ValueError("Horizon Creative IME path changed")
    if ime.index('Screen::Inventory | Screen::Creative if self.mod_browser_typing() => {') > ime.index('Screen::Creative => self.creative_search_edit(Some(&text)),'):
        raise ValueError("Horizon Creative IME field priority changed")
    for name, source in updated.items():
        (workspace / name).write_text(source, encoding="utf-8", newline="\n")


def adapt_glue(source: str) -> str:
    source = replace_once(source, "use std::cell::RefCell;", "use std::{cell::RefCell, sync::Arc};")
    source = replace_once(source, "    viewer: Option<Viewer>,", "    viewer: Option<Viewer>,\n    catalog: Option<Arc<NativeRecipes>>,")
    source = replace_once(source, "State { viewer: None }", "State { viewer: None, catalog: None }")
    start = source.index("/// Builds the viewer once per world")
    end = source.index("/// The panel's events:", start)
    return source[:start] + '''/// Follow the current world's recipe table and restore an absent panel.
/// Horizon replaces this Arc after login/datapack synchronization; keeping a
/// viewer forever would retain stale tables. The unchanged Arc is a cheap check.
pub(super) fn sync(game: &mut crate::game::Game<'_>) {
    if !game.native_on(jei::ID) {
        return;
    }
    if game.native_player().is_none() {
        STATE.with(|s| {
            let mut s = s.borrow_mut();
            s.viewer = None;
            s.catalog = None;
        });
        return;
    }
    let Some(catalog) = game.native_recipes() else {
        return;
    };
    let missing_panel = game.panel.shown(|id| game.native_on(id)).is_none();
    let known = game.native_known_recipes();
    let lang = jei::lang(game.native_setting(jei::ID, "language").as_deref());
    let out = STATE.with(|s| {
        let mut s = s.borrow_mut();
        let changed = s.catalog.as_ref().is_none_or(|old| !Arc::ptr_eq(old, &catalog));
        s.catalog = Some(catalog.clone());
        if let Some(viewer) = s.viewer.as_mut() {
            viewer.set_lang(lang);
            if changed {
                viewer.update_tables(items(&catalog), recipes(&catalog), &known)
            } else if missing_panel {
                viewer.refresh(&known)
            } else {
                Vec::new()
            }
        } else {
            let (viewer, out) = Viewer::new(items(&catalog), recipes(&catalog), lang);
            s.viewer = Some(viewer);
            vec![out]
        }
    });
    for out in out {
        if let Err(error) = apply(game, out) {
            game.native_chat(&format!("JEI: {error}"));
        }
    }
}

''' + source[end:]


def adapt_runtime(source: str) -> str:
    return replace_once(source, "    /// The player changed the language: rewrite the strings of the next page.", '''    /// New server/resource tables keep the player's search and valid history.
    pub fn update_tables(&mut self, items: Vec<Item>, recipes: Vec<Recipe>, known: &[i32]) -> Vec<Out> {
        self.items = items;
        self.recipes = recipes;
        let history = std::mem::take(&mut self.history);
        self.history = history.into_iter().filter(|page| !self.found(page).is_empty()).collect();
        self.refresh(known)
    }

    /// Restore a lost host panel without clearing the player's viewer state.
    pub fn refresh(&mut self, known: &[i32]) -> Vec<Out> {
        vec![self.list(), self.show(known)]
    }

    /// The player changed the language: rewrite the strings of the next page.''')


def apply(bundle: Path) -> None:
    for name, expected in SOURCE_HASHES.items():
        if hashlib.sha256((bundle / name).read_bytes()).hexdigest() != expected:
            raise ValueError(f"Unexpected JEI for Horizon 0.1.0 source: {name}")
    updated = {
        "glue.rs": adapt_glue((bundle / "glue.rs").read_text(encoding="utf-8")),
        "runtime.rs": adapt_runtime((bundle / "runtime.rs").read_text(encoding="utf-8")),
    }
    for name, source in updated.items():
        (bundle / name).write_text(source, encoding="utf-8", newline="\n")
