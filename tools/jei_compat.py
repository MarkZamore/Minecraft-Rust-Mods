"""Repair lifecycle of the verified JEI for Horizon 0.1.0 native viewer."""

import hashlib
from pathlib import Path


SOURCE_HASHES = {
    "glue.rs": "3b531b4d6c67d5da7c22fc62ac4de073c7f069094cdcfa2d48bf484d8748d7cf",
    "runtime.rs": "7436912434a65143bc5b6c2fc3a7321a03ec23bea79e0ccea9fe60d660e1e8c8",
}
F00125F_REVISION = "f00125f61f794aaa3d4e950d301f353b35b6ee3d"
F00125F_BUNDLE_SHA256 = {
    "command.rs": "0fbf3c193a61982dc79388afa911eab6a3aec9245f429268f4e783407c2fc7a5",
    "settings.rs": "fa48ac717cd4b025c5c965d025931d77c737f8349cc3a8010f51b5788769b83d",
    "click.rs": "53969c649b872accc309b173f0d8bdcd2dc1ba0b9763c77cc37034b8c984beaf",
    "event.rs": "02e4ded1c710b962847c71209eeb8c20b943850dea16dbda37f44f956ed00b40",
    "horizon-mixin.toml": "1707694d511375b415acfdd0102d26b2704d6cc71fdb1832dc27357dbe264904",
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

CLIENT = "crates/horizon-client/src/"
CREATIVE_SCREEN = CLIENT + "game/interface/creative_screen.rs"
PLAY_KEYS = CLIENT + "game/interface/play_keys.rs"
MODDING_PANEL = CLIENT + "game/modding/native_panel.rs"
MODDING_RECIPES = CLIENT + "game/modding/native_panel_recipes.rs"
FOCUSED_TEXT = CLIENT + "game/interface/focused_text_field.rs"
PAD_MENU = CLIENT + "game/interface/pad_menu.rs"
RUNTIME_CONTROL = CLIENT + "game/runtime/control.rs"
IME = CLIENT + "game/interface/ime.rs"

# Official f00125f sources, with game.rs after the pinned Edition overlay.
# The shared legacy hooks do not replace these files.
F00125F_HOST_SHA256 = {
    CLIENT + "game.rs": (
        "98769993cbeb7f8f40eecfbc7843d820ce729a7c7e7f7c83c4168e75a65e20c0",
        "497b9f928a626f2f21b24943f60828e2905e5e5c0391477250bfde7cac899bdf",
    ),
    CREATIVE_SCREEN: "14a02a728479d2cf8517c93a074f69e91ad879a48d47acfb9f26f5a5b835fa02",
    PLAY_KEYS: "6192bed85215a9cb0922007ad42341424ee6599353a0ce589c17906fc30bc9ff",
    MODDING_PANEL: "b21bdd244033884de58e66fc85831cada48d7333345d05957e3761a88cc5cc22",
    MODDING_RECIPES: "af0eb0b65a71f1fc9ec7f627f76b0a2004ee04c639ba34a9c950c6086cad1a4f",
    FOCUSED_TEXT: "298a216847601e5c16e6786d035160bdce881d4337b03788bde7db1747f5362f",
    PAD_MENU: "b886b3cbd970bd9e235253e307529e5596cfa768e246b902543800c1f0777c5b",
    RUNTIME_CONTROL: (
        "c5a2f6e4ce0218101f172a06fea9a34d810280ffaadc5bf22621122f198b5ab9",
        "da9fc811b15579ee2de60e2fa84d85f02178b9ced961e158ab02a3b8749a2501",
    ),
    IME: "826f626cf82c854869aa8c06b3e000777176f48a6c70bcbbd95ff58c787104d4",
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
    return adapt_creative_close(adapt_creative_input(adapt_creative_screen(source)))


def adapt_creative_screen(source: str) -> str:
    click = '''fn click_creative(&mut self, right: bool) {
        if self.mod_browser_press(right) {
            return;
        }'''
    source = replace_once(source, click, click.replace("            return;",
        "            if self.jei_panel_owner() { self.inv_clicks.skip_release(); }\n            return;"))
    source = replace_once(source, '''            // CreativeModeInventoryScreen: an unbordered EditBox of 50 at (82, 6).
            let search = &mut self.creative_screen.search;''', '''            // CreativeModeInventoryScreen: an unbordered EditBox of 50 at (82, 6).
            let panel_typing = self.jei_panel_owner() && self.mod_browser_typing();
            let search = &mut self.creative_screen.search;''')
    return replace_once(source, '''            search.bordered = false;
            search.focused = true;''', '''            search.bordered = false;
            search.focused = !panel_typing;''')


def adapt_creative_input(source: str) -> str:
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
    return replace_once(source, "                    if self.screen == Screen::Creative && d != 0.0 && !bundle {",
        "                    if self.screen == Screen::Creative && d != 0.0 && !bundle && !browser {")


def adapt_creative_close(source: str) -> str:
    # Escape already blurs every browser upstream. E needs the JEI equivalent.
    anchor = '''                Screen::Creative => {
                    self.pad_clear_focus();
                    if let Some(c) = self.carried.take() {'''
    return replace_once(source, anchor, anchor.replace("self.pad_clear_focus();",
        "self.pad_clear_focus();\n                    if self.jei_panel_owner() { self.mod_browser_closed(); }"))


def adapt_host_panel(source: str, *, separate_tooltip: bool = False) -> str:
    # Exempt only the selected JEI; keep built-in Recipes and other owners as authored.
    source = replace_once(source,
        '        if self.screen == Screen::Creative && of.0.native_id() != "recipes" {',
        '        if self.screen == Screen::Creative && !matches!(of.0.native_id(), "recipes" | "jei") {')
    source = replace_once(source, '''        matches!(self.screen, Screen::Inventory | Screen::Creative)
            && self.panel.of.is_some()''', '''        matches!(self.screen, Screen::Inventory | Screen::Creative)
            && self.title.is_none()
            && self.panel.of.is_some()''')
    if not separate_tooltip:
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


def adapt_pad_search(source: str) -> str:
    return replace_once(source,
        '        } else if self.screen == Screen::Creative && self.creative_screen.tab().search {',
        '        } else if self.screen == Screen::Creative && self.creative_screen.tab().search && !(self.jei_panel_owner() && self.mod_browser_typing()) {')


def verify_text_routing(control: str, ime: str, creative_ime: str) -> None:
    panel_typing = 'Screen::Inventory | Screen::Creative if self.mod_browser_typing() => {'
    if control.count(panel_typing) != 2:
        raise ValueError("Horizon Creative text/backspace paths changed")
    for anchor in (
        'Screen::Creative => self.mod_browser_typing() || self.creative_screen.tab().search,',
        panel_typing,
        creative_ime,
    ):
        if ime.count(anchor) != 1:
            raise ValueError("Horizon Creative IME path changed")
    if ime.index(panel_typing) > ime.index(creative_ime):
        raise ValueError("Horizon Creative IME field priority changed")


def read_host_files(workspace: Path, hashes: dict[str, str | tuple[str, ...]]) -> dict[str, str]:
    originals = {}
    for name, expected in hashes.items():
        path = workspace / name
        if path.is_symlink() or path.is_junction():
            raise ValueError(f"Horizon native panel source must be an ordinary file: {name}")
        raw = path.read_bytes()
        allowed = expected if isinstance(expected, tuple) else (expected,)
        if hashlib.sha256(raw).hexdigest() not in allowed:
            raise ValueError(f"Horizon native panel source changed before JEI creative port: {name}")
        originals[name] = raw.decode("utf-8")
    return originals


def prepare_source(workspace: Path) -> None:
    if (workspace / CREATIVE_SCREEN).exists():
        updated = adapt_f00125f_host(read_host_files(workspace, F00125F_HOST_SHA256))
    else:
        updated = adapt_2ebf00a_host(read_host_files(workspace, HOST_SOURCE_HASHES))
    for name, source in updated.items():
        (workspace / name).write_text(source, encoding="utf-8", newline="\n")


def adapt_f00125f_host(originals: dict[str, str]) -> dict[str, str]:
    screen = originals[CREATIVE_SCREEN]
    for anchor in (
        "let browser_tip = self.draw_mod_browser(batch);",
        "self.draw_mod_browser_tooltip(batch, tooltip);",
    ):
        if screen.count(anchor) != 1:
            raise ValueError("Horizon Creative panel drawing or tooltip path changed")
    focused = originals[FOCUSED_TEXT]
    panel_text = "Screen::Inventory | Screen::Creative if self.mod_browser_typing() => {"
    creative_text = "Screen::Creative if self.creative_screen.tab().search && !self.creative_ignore_text => {"
    if focused.count(panel_text) != 1 or focused.count(creative_text) != 1 or focused.index(panel_text) > focused.index(creative_text):
        raise ValueError("Horizon Creative focused field priority changed")
    verify_text_routing(originals[RUNTIME_CONTROL], originals[IME],
                        "Screen::Creative => self.creative_search_edit(Some(text)),")
    panel = adapt_host_panel(originals[MODDING_PANEL], separate_tooltip=True)
    # Empty JEI results keep their search field across a recipe refresh and on Back.
    panel = replace_once(panel, "self.native_changed(self.searching && !self.sent.is_empty());",
        'self.native_changed((self.searching || mod_id == "jei") && !self.sent.is_empty());')
    panel = replace_once(panel, 'let filtering = mod_id == "recipes" && !self.sent.is_empty();',
        'let filtering = matches!(mod_id, "recipes" | "jei") && !self.sent.is_empty();')
    return {
        CLIENT + "game.rs": adapt_creative_input(originals[CLIENT + "game.rs"]),
        CREATIVE_SCREEN: adapt_creative_screen(screen),
        PLAY_KEYS: adapt_creative_close(originals[PLAY_KEYS]),
        MODDING_PANEL: panel,
        MODDING_RECIPES: adapt_host_recipes(originals[MODDING_RECIPES]),
        PAD_MENU: adapt_pad_search(originals[PAD_MENU]),
    }


def adapt_2ebf00a_host(originals: dict[str, str]) -> dict[str, str]:
    base = CLIENT
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
    updated[base + "game/pad_menu.rs"] = adapt_pad_search(originals[base + "game/pad_menu.rs"])
    # 2ebf already has correct Creative text/backspace and IME admission and
    # priority. Validate these author paths; no additional rewrite is needed.
    verify_text_routing(originals[base + "game/control.rs"], originals[base + "game/ime.rs"],
                        "Screen::Creative => self.creative_search_edit(Some(&text)),")
    return updated


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


def adapt_f00125f_glue(source: str) -> str:
    source = replace_once(source,
        '''    NativePanelEvent, NativeRecipeKind, NativeRecipeLayout, NativeRecipeStack, NativeRecipeView,
    NativeRecipes, NativeStack,
};''', '''    NativeRecipeKind, NativeRecipeLayout, NativeRecipeResult, NativeRecipeStack,
    NativeRecipeView, NativeRecipes, PanelEvent as NativePanelEvent,
};
use mod_api::{Stack as NativeStack, __private::{item_key, item_key_id}};''')
    for before, after in (
        ("native_known_recipes()", "known_recipes()"),
        ("native_give(", "recipe_give("),
        ("native_place_recipe(", "place_recipe("),
    ):
        source = source.replace(before, after)
    source = replace_once(source, "game.native_panel_items(jei::ID, panel_stacks(stacks));",
                          "game.panel_items(jei::ID, to_native(stacks)?);")
    source = replace_once(source, "                to_native(tabs),\n", "                to_native(tabs)?,\n")
    source = replace_once(source, "                views.into_iter().map(native_view).collect(),",
                          "                views.into_iter().map(native_view).collect::<Result<_, _>>()?,")
    start = source.index("/// The game's stacks.")
    end = source.index("fn native_view", start)
    source = source[:start] + '''/// Resolve the exact list without changing the indices passed back by the panel.
fn to_native(stacks: Vec<Stack>) -> Result<Vec<NativeStack>, String> {
    stacks.into_iter().map(|s| {
        let item = item_key(&s.item).ok_or_else(|| format!("Unknown item {}", s.item))?;
        Ok(NativeStack { item, count: s.count, components: s.components })
    }).collect()
}

''' + source[end:]
    source = replace_once(source, "fn native_view(view: View) -> NativeRecipeView {\n    NativeRecipeView {",
        '''fn native_view(view: View) -> Result<NativeRecipeView, String> {
    // The previous viewer's result stacks were alternatives in a single slot.
    let stacks = to_native(view.results)?;
    let results = if stacks.is_empty() { Vec::new() } else {
        vec![NativeRecipeResult { stacks, chance: None }]
    };
    Ok(NativeRecipeView {''')
    source = replace_once(source,
        '''        slots: view.slots.into_iter().map(to_native).collect(),
        results: to_native(view.results),
        note: view.note,
        action: view.action,
    }
}''', '''        slots: view.slots.into_iter().map(to_native).collect::<Result<_, _>>()?,
        results,
        fluid_inputs: Vec::new(),
        fluid_outputs: Vec::new(),
        note: view.note,
        action: view.action,
    })
}''')
    for name in ("Key", "SlotClicked"):
        # The guards on the exact original files make both replacements unambiguous.
        anchor = f"}} => Event::{name} {{"
        start = source.index(anchor)
        end = source.index("\n        },", start)
        source = source[:start] + replace_once(source[start:end], "stack: stack_of(stack),",
                                                "stack: stack_of(stack)?,") + source[end:]
    return replace_once(source, '''fn stack_of(stack: &NativeStack) -> Stack {
    Stack {
        item: stack.item.clone(),
        count: stack.count,
        components: stack.components.clone(),
    }
}''', '''fn stack_of(stack: &NativeStack) -> Option<Stack> {
    Some(Stack {
        item: item_key_id(stack.item)?.to_owned(),
        count: stack.count,
        components: stack.components.clone(),
    })
}''')


def adapt_f00125f_bundle(originals: dict[str, str], updated: dict[str, str]) -> None:
    updated["glue.rs"] = adapt_f00125f_glue(updated["glue.rs"])
    for name in ("event.rs", "horizon-mixin.toml"):
        updated[name] = replace_once(originals[name], "native_hooks::NativePanelEvent", "native_hooks::PanelEvent")
    prefix = "horizon_client::game::native_hooks::"
    manifest = updated["horizon-mixin.toml"]
    for name in ("command", "panel_event"):
        manifest = replace_once(manifest, f"{prefix}Game<'_>::native_{name}",
                                 f"{prefix}legacy::Game<'_>::legacy_{name}")
    updated["horizon-mixin.toml"] = manifest


def apply(bundle: Path, *, revision: str | None = None) -> None:
    hashes = SOURCE_HASHES | F00125F_BUNDLE_SHA256 if revision == F00125F_REVISION else SOURCE_HASHES
    originals = {}
    for name, expected in hashes.items():
        path = bundle / name
        raw = path.read_bytes()
        if path.is_symlink() or path.is_junction() or hashlib.sha256(raw).hexdigest() != expected:
            raise ValueError(f"Unexpected JEI for Horizon 0.1.0 source: {name}")
        originals[name] = raw.decode("utf-8")
    updated = {
        "glue.rs": adapt_glue(originals["glue.rs"]),
        "runtime.rs": adapt_runtime(originals["runtime.rs"]),
    }
    if revision == F00125F_REVISION:
        adapt_f00125f_bundle(originals, updated)
    for name, source in updated.items():
        (bundle / name).write_text(source, encoding="utf-8", newline="\n")
