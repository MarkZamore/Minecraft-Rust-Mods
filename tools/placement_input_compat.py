"""Port the reviewed placement and input hooks to the f00125f engine API."""

import re


def replace_once(text: str, pattern: str, replacement: str) -> str:
    changed, count = re.subn(pattern, replacement, text)
    if count != 1:
        raise ValueError(f"Changed placement/input hook anchor: {pattern!r}")
    return changed


def route_source(text: str) -> str:
    # These modifiers are explicit physical keys, not mappings such as Sneak.
    text = re.sub(
        r"(\bgame\s*\.keys\s*\.)contains(\(&"
        r"(?:winit::keyboard::)?KeyCode::(?:Shift|Control)(?:Left|Right)\))",
        r"\1raw\2",
        text,
    )
    if "game.options_to_game = true;" in text:
        # Keep the in-world Options layout and restore Playing plus pointer capture on close.
        text = replace_once(
            text,
            r"(game\.open_options_at\(false, horizon_client::options_ui::Page::NativeMod\(i\)\);\s*)"
            r"game\.options_to_game = true;",
            r"\1game.options_from_title = true;",
        )
    if "b.placement_state(level, ticks, &ctx)" in text:
        text = replace_once(
            text,
            r"b\.placement_state\(level, ticks, &ctx\)",
            "b.placement_preview(level, ticks, &ctx).map(|preview| (preview.pos, preview.state))",
        )
        # Horizonmatica's printer constructs click positions rather than casting rays.
        text, count = re.subn(
            r"(super::Hit\s*\{\s*pos: p,\s*face: (?:d|f|\[0, 1, 0\]),\s*"
            r"point(?:: centre)?,?)(\s*)(\})",
            lambda match: f"{match[1].removesuffix(',')}, inside: false{match[2]}{match[3]}",
            text,
        )
        if count != 3:
            raise ValueError("Changed Horizonmatica printer hit anchors")
    if ('pub const ID: &str = "accurate_block_placement";' in text
            and not re.search(r"pub struct Sample \{[^}]*pub inside: bool,", text)):
        text = replace_once(
            text,
            r"(pub struct Sample \{[^}]*pub point: \[f64; 3\],\n)([ \t]*)(pub replaceable: bool,)",
            r"\1\2pub inside: bool,\n\2\3",
        )
        # Existing fixtures simulate rays originating outside their test blocks.
        text, count = re.subn(r"(?m)^([ \t]*)(replaceable: false,)$", r"\1inside: false,\n\1\2", text)
        if count != 2:
            raise ValueError("Changed Accurate Block Placement sample fixtures")
    if ("Some(abp::Sample {" in text
            and not re.search(r"Some\(abp::Sample \{[^}]*inside: hit\.inside,", text)):
        text = replace_once(
            text,
            r"(point: hit\.point,\n)([ \t]*)(replaceable: game\.res\.facts\(\)\.get\(state\)\.replaceable,)",
            r"\1\2inside: hit.inside,\n\2\3",
        )
    if "let kept = game.tick_hit.replace(Some(hit));" in text:
        text = replace_once(
            text,
            r"(point: t\.point,\n)([ \t]*)(\};\s*if game\.remote_block_reacts)",
            lambda match: f"{match[1]}{match[2]}    inside: t.inside,\n{match[2]}{match[3]}",
        )
        text = replace_once(
            text,
            r"let kept = game\.tick_hit\.replace\(Some\(hit\)\);\n([ \t]*)game\.remote_use\(\);\n"
            r"[ \t]*game\.tick_hit = kept;",
            r"let kept = game.tick_hit.replace(Some(Some(hit)));\n"
            r"\1game.remote_use(mod_api::InteractionAttempt::Held);\n\1game.tick_hit.set(kept);",
        )
    return text
