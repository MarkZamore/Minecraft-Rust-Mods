"""Route the Mods title-screen Multiplayer action directly to the server list."""

from hashlib import sha256
from pathlib import Path


TITLE = "crates/horizon-client/src/title.rs"
TITLE_TESTS = "crates/horizon-client/src/title/tests.rs"
AUTHOR_ACTION = """            (Mode::Main, Some(4)) => {
                // TitleScreen.init: SafetyScreen first, unless it was turned off.
                self.mode = if self.skip_multiplayer_warning {
                    Mode::Multiplayer(crate::multiplayer::Screen::list(&self.saves))
                } else {
                    Mode::MultiplayerWarning { check: false }
                };
                Action::None
            }"""
PACK_ACTION = """            (Mode::Main, Some(4)) => {
                self.mode = Mode::Multiplayer(crate::multiplayer::Screen::list(&self.saves));
                Action::None
            }"""
PACK_ACTION_SHA256 = sha256(PACK_ACTION.encode()).hexdigest()
MIXINS_ACTION = PACK_ACTION.replace(
    "Mode::Multiplayer(crate::multiplayer::Screen::list(&self.saves));",
    "Mode::Multiplayer(\n                    crate::multiplayer::Screen::list(&self.saves),\n                );",
)


def prepare_source(workspace: Path) -> None:
    path = workspace / TITLE
    tests_path = workspace / TITLE_TESTS
    for file in (path, tests_path):
        if file.is_symlink() or file.is_junction():
            raise ValueError("The Multiplayer menu policy requires ordinary source files")
    source = path.read_bytes()
    # The base recipe already verifies the upstream file and shared Steam edits.
    # Match the entire menu action so a changed access flow needs a review.
    anchor = AUTHOR_ACTION.encode("utf-8")
    if source.count(anchor) != 1:
        raise ValueError("Horizon Multiplayer menu action changed before warning policy preparation")
    tests = tests_path.read_bytes()
    previous = (b"    // SafetyScreen comes first while its box was never ticked.\n"
                b"    assert!(matches!(f.title.mode, Mode::MultiplayerWarning { .. }));\n")
    proceed = b'    f.click("Proceed");\n'
    if tests.count(previous) != 1 or tests.count(proceed) != 4:
        raise ValueError("Horizon Multiplayer title tests changed before warning policy preparation")
    tests = tests.replace(previous, b"    assert!(matches!(f.title.mode,\n"
                                    b"        Mode::Multiplayer(crate::multiplayer::Screen::List { .. })));\n"
                                    b"    assert!(!f.title.skip_multiplayer_warning);\n")
    tests = tests.replace(b"            " + proceed.lstrip(), b"")
    tests_path.write_bytes(tests.replace(proceed, b""))
    path.write_bytes(source.replace(anchor, PACK_ACTION.encode("utf-8")))


def verify_prepared_source(workspace: Path) -> str:
    source = (workspace / TITLE).read_bytes()
    # horizon-mixins pretty-prints the title module after Physics injections.
    if (source.count(b"(Mode::Main, Some(4))") != 1 or
            sum(source.count(action.encode()) for action in (PACK_ACTION, MIXINS_ACTION)) != 1):
        raise ValueError("Prepared Horizon client lost the required Multiplayer menu policy")
    return PACK_ACTION_SHA256
