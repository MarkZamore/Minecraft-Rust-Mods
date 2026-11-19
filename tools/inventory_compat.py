"""Retarget the reviewed inventory mod calls to the f00125f client APIs."""

import re


_MOUSE_TRICKS = "fn mousetricks_near(&self, container: &[usize]) -> bool"
_INVENTORY_PROFILES = "fn sort_slots_with_mode(game: &mut Game<'_>, slots: &[usize], sort_mode: u8)"


def _replace(text: str, pattern: str, new: str, expected: int = 1) -> str:
    updated, count = re.subn(pattern, new, text)
    if count != expected:
        raise ValueError(f"Expected {expected} inventory compatibility anchors for {pattern!r}, found {count}")
    return updated


def _names(text: str, owner: str, expected: int) -> str:
    old = f"let names = |id: &str| {owner}.pack.names.display_item(id);"
    return _replace(text, re.escape(old), f"let names = &{owner}.pack.names;", expected)


def route_source(text: str) -> str:
    name_calls = 0
    if _INVENTORY_PROFILES in text:
        # Game's live language table also resolves authored and translated custom names.
        text = _names(text, "game", 3)
        name_calls += 3
    if _MOUSE_TRICKS in text:
        # This is the renamed original click path, including bundle deselection
        # before the server prediction. The caller still notifies local storage.
        text = _replace(text, re.escape("game.menu_click(Some(slot), button, input)"),
                        "game.click_open_inventory(Some(slot), button, input)")
        text = _replace(text, re.escape("HudBatch::new([r.config.width, r.config.height])"),
                        "HudBatch::new([r.config.width, r.config.height], self.settings.gui_scale_inputs())")
        text = _names(text, "self", 1)
        name_calls += 1
    if name_calls:
        text = _replace(text, r"hover_name\(&names, ", "hover_name(names, ", name_calls)
    return text
