"""Keep the verified JEI 0.2.1 viewer on Horizon's typed f00125f client."""

import hashlib
import re

from jei_compat import adapt_f00125f_glue, replace_once


PINS = {
    "glue.rs": "bf7463ad1d2cbf83d5ca3dbd8c43823e4a8d57c6ba1b28eadf03e590f37ca8d6",
    "runtime.rs": "3b96c174dcc50dcf76c84ecb081ff24d174b2b6a16730ae9a888e99e02235ad5",
    "horizon-mixin.toml": "8dc18ef9b80af05ccff1dbb9129cba0cd01c3b486baf3782bebdba2ee5d91b01",
}

NAMED_HOOKS = (
    "screen_render_post", "screen_mouse_button_pressed_pre", "screen_mouse_button_released_pre",
    "screen_mouse_dragged_pre", "tooltip",
)


def route_manifest(source):
    sections = re.split(r"(?m)(?=^\[\[injection\]\])", source)
    for index, section in enumerate(sections[1:], 1):
        for name in NAMED_HOOKS:
            anchor = f'hook = "{name}"\nhook_version = 1'
            if anchor not in section:
                continue
            target = f'horizon_client::game::native_hooks::base::Game<\'_>::native_{name}'
            section = replace_once(section, anchor, f'target = "{target}"')
            if name == "screen_render_post":
                section = replace_once(section, 'value = "&mut *batch"', 'value = "&mut *draw"')
        sections[index] = section
    result = "".join(sections).replace("native_hooks::NativePanelEvent", "native_hooks::PanelEvent")
    for name in ("command", "panel_event"):
        result = replace_once(result, f"native_hooks::Game<'_>::native_{name}",
                              f"native_hooks::legacy::Game<'_>::legacy_{name}")
    return result


def update_catalog(source):
    return replace_once(source, "    /// The player changed the language:", '''    pub fn update_tables(&mut self, items: Vec<Item>, recipes: Vec<Recipe>, variants: Vec<Variant>,
                         lang: Lang, known: &[i32]) -> Vec<Out> {
        let mut next = Self::with_variants(items, recipes, variants, lang).0;
        next.sort = self.sort;
        next.search = std::mem::take(&mut self.search);
        let history = std::mem::take(&mut self.history);
        next.history = history.into_iter().filter(|page| !next.found(page).is_empty()).collect();
        let result = next.refresh(known);
        *self = next;
        result
    }

    pub fn refresh(&mut self, known: &[i32]) -> Vec<Out> {
        vec![self.list(), self.show(known)]
    }

    /// The player changed the language:''')


def adapt_glue(source):
    source = replace_once(source, '    NativeRecipes, NativeScreenView, NativeStack,', '    NativeRecipes, NativeStack,')
    source = replace_once(source, 'use std::cell::RefCell;',
                          'use mod_api::ScreenView as NativeScreenView;\nuse std::{cell::RefCell, sync::Arc};')
    first = source.index("/// A bookmark as the game's stack")
    last = source.index("fn native_view(", first)
    bookmark = source[first:last]
    source = adapt_f00125f_glue(source)
    source = replace_once(source, "fn native_view(", bookmark + "fn native_view(")
    if source.count("stack: stack_of(stack)?,") != 2:
        raise ValueError("The JEI 0.2.1 event stack conversions changed")
    source = replace_once(source, "    session: Session,", "    session: Session,\n    catalog: Option<Arc<NativeRecipes>>,")
    source = replace_once(source, "            session: Session::new(),",
                          "            session: Session::new(),\n            catalog: None,")
    first = source.index("pub(super) fn sync(")
    last = source.index("/// Runs", first)
    return source[:first] + '''pub(super) fn sync(game: &mut Game<'_>) {
    let has_player = game.native_player().is_some();
    let enabled = game.native_on(jei::ID);
    if !has_player || !enabled {
        STATE.with(|s| {
            let mut state = s.borrow_mut();
            state.session.update(has_player, enabled);
            if !has_player {
                state.catalog = None;
                state.drag = None;
                state.pending.clear();
            }
        });
        return;
    }
    let Some(catalog) = game.native_recipes() else { return; };
    let missing = game.panel.shown(|id| game.native_on(id)).is_none();
    let language = lang(game);
    let known = game.known_recipes();
    load();
    let out = STATE.with(|s| {
        let mut state = s.borrow_mut();
        let changed = state.catalog.as_ref().is_none_or(|old| !Arc::ptr_eq(old, &catalog));
        state.catalog = Some(catalog.clone());
        if let Some(viewer) = state.viewer_mut() {
            viewer.set_lang(language);
            if changed {
                let recipes = recipes(&catalog);
                let variants = jei::harvest_variants(&recipes, VARIANTS_PER_ITEM, jei::ITEMS_MAX);
                viewer.update_tables(items(&catalog), recipes, variants, language, &known)
            } else if missing {
                viewer.refresh(&known)
            } else {
                Vec::new()
            }
        } else {
            let recipes = recipes(&catalog);
            let variants = jei::harvest_variants(&recipes, VARIANTS_PER_ITEM, jei::ITEMS_MAX);
            let (mut viewer, mut out) = Viewer::with_variants(items(&catalog), recipes, variants, language);
            let sort = state.config.sort;
            if sort != viewer.sort() {
                out = viewer.set_sort(sort);
            }
            state.session.set_viewer(viewer);
            vec![out]
        }
    });
    for out in out {
        if let Err(error) = apply(game, out) {
            game.native_chat(&format!("JEI: {error}"));
        }
    }
}

''' + source[last:]


def apply_if_selected(bundle):
    manifest = bundle / "horizon-mixin.toml"
    if hashlib.sha256(manifest.read_bytes()).hexdigest() != PINS[manifest.name]:
        return False
    originals = {}
    for name, pin in PINS.items():
        path = bundle / name
        raw = path.read_bytes()
        if path.is_symlink() or path.is_junction() or hashlib.sha256(raw).hexdigest() != pin:
            raise ValueError(f"JEI 0.2.1 author file changed: {name}")
        originals[name] = raw.decode("utf-8")
    changes = {"glue.rs": adapt_glue(originals["glue.rs"]),
               "runtime.rs": update_catalog(originals["runtime.rs"]),
               manifest.name: route_manifest(originals[manifest.name])}
    event = bundle / "event.rs"
    changes[event.name] = replace_once(event.read_text(encoding="utf-8"),
                                       "native_hooks::NativePanelEvent", "native_hooks::PanelEvent")
    for name, data in changes.items():
        (bundle / name).write_text(data, encoding="utf-8", newline="\n")
    return True
