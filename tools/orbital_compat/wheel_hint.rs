// The author's wording, with the player's current binding and Horizon's own
// localized key label. This changes presentation only, never saved controls.
fn wheel_hint(game: &Game<'_>, text: String) -> String {
    let known = matches!(text.as_str(),
        "R: laser wheel"
        | "Type /orbital give for the Designator. Hold it and press R to pick a laser."
        | "Orbital Laser Designator added to your inventory (R with it in hand: the laser wheel)");
    if !known { return text; }
    let key = game.native_mod_key(ID, "wheel", "key.keyboard.insert");
    let label = horizon_client::keybinds::display_name(&key).plain();
    match text.as_str() {
        "R: laser wheel" => format!("{label}: laser wheel"),
        "Type /orbital give for the Designator. Hold it and press R to pick a laser." =>
            format!("Type /orbital give for the Designator. Hold it and press {label} to pick a laser."),
        _ => format!("Orbital Laser Designator added to your inventory ({label} with it in hand: the laser wheel)"),
    }
}
