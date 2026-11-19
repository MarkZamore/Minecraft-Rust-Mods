/// Forward an integrated owner's parsed `/orbital` command to their own window.
pub(super) fn perform_command(
    game: &mut Game<'_>,
    source: &mod_api::CommandSource,
    command: &mod_api::CommandInvocation,
    ran: &mut bool,
) {
    if *ran || game.native_window() || !game.native_on(ID) {
        return;
    }
    let Some(owner) = game
        .lan
        .as_ref()
        .and_then(|host| host.hub.singleplayer_owner)
    else {
        return;
    };
    if !orbital_owner_command(source, owner) {
        return;
    }
    let Some(line) = orbital_command_line(command) else {
        return;
    };
    if let Ok(mut pending) = SERVER_COMMANDS.lock() {
        pending.push(line);
        *ran = true;
    }
}

fn orbital_owner_command(source: &mod_api::CommandSource, owner: u128) -> bool {
    if source.permission() != mod_api::PermissionLevel::Owners {
        return false;
    }
    match source {
        mod_api::CommandSource::Console { .. } => true,
        // Integrated `owner_console_command` temporarily enters that owner's
        // live player state before dispatch. Other players never gain this route.
        mod_api::CommandSource::Player { player, .. } => player.uuid() == owner,
        _ => false,
    }
}

fn orbital_command_line(command: &mod_api::CommandInvocation) -> Option<String> {
    use mod_api::CommandArgumentValue as Value;

    if command.literals.first()?.as_str() != ID {
        return None;
    }
    let mut line = command.literals.join(" ");
    let argument = match command.arguments.as_slice() {
        [] => return Some(line),
        [argument] => argument,
        _ => return None,
    };
    let value = match (argument.name.as_str(), &argument.value) {
        ("seconds" | "blocks" | "speed", Value::Number(number)) if number.is_finite() => {
            number.to_string()
        }
        ("seed", Value::Integer(seed)) => seed.to_string(),
        _ => return None,
    };
    line.push(' ');
    line.push_str(&value);
    Some(line)
}
