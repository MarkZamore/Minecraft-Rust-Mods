use std::{env, fs, process};

fn main() {
    let workspace = env::args_os().nth(1).expect("expected the disposable workspace");
    let workspace = fs::canonicalize(workspace).expect("resolve disposable workspace");
    if let Err(error) = horizon_mod_api_gen::materialize_game_modules(&workspace) {
        eprintln!("{error}");
        process::exit(1);
    }
}
