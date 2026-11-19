"""Keep the author's remote state sender usable by the Spider-Man return hook."""

from hashlib import sha256
from pathlib import Path

SOURCE_SHA256 = "412038605c92bd98a2fbd07f36ac0c9f03a972a48c8b5119ec582f579d09a097"
REMOTE = "crates/horizon-client/src/game/network/remote/mod.rs"
# Signed f00125f Edition remote source with the reviewed legacy tick relay.
REMOTE_SHA256 = "5ba3042f103418efa59187b45c39163835204d0f1d548d2d56d8bca2ae322177"
CAMERA = "crates/horizon-client-render/src/camera.rs"
CAMERA_SHA256 = "4ee7f45c43eb6852e784b25090cfe60002acda2d980a4806ac8a4cf51b2277c6"


def prepare_camera(workspace: Path) -> None:
    path = workspace / CAMERA
    raw = path.read_bytes()
    signature = "pub fn view_matrix(yaw: f32, pitch: f32) -> glam::Mat4 {"
    source = raw.decode("utf-8")
    if sha256(raw).hexdigest() != CAMERA_SHA256 or source.count(signature) != 1:
        raise ValueError("Horizon camera source changed before Spider-Man compatibility preparation")
    fallback = signature + "\n    view_matrix_default(yaw, pitch)\n}\n\n"
    fallback += "fn view_matrix_default(yaw: f32, pitch: f32) -> glam::Mat4 {"
    path.write_text(source.replace(signature, fallback), encoding="utf-8", newline="\n")


def prepare_source(workspace: Path) -> None:
    path = workspace / "crates/horizon-client/src/game/remote.rs"
    expected = SOURCE_SHA256
    latest = not path.exists()
    if latest:
        path = workspace / REMOTE
        expected = REMOTE_SHA256
    raw = path.read_bytes()
    if sha256(raw).hexdigest() != expected:
        raise ValueError("Horizon remote source changed before Spider-Man compatibility preparation")
    source = raw.decode("utf-8")
    signature = "    fn remote_send_state(&mut self, input: u8) {"
    if latest:
        if source.count(signature) != 1:
            raise ValueError("The reviewed remote state sender no longer has one source definition")
        # The external return hook observes every exit after vanilla sends its state.
        # Keep macros, recording imports and early exits inside the original implementation.
        wrapped = signature + "\n        self.send_vanilla_remote_state(input);\n    }\n\n"
        wrapped += "    fn send_vanilla_remote_state(&mut self, input: u8) {"
        prepare_camera(workspace)
        path.write_text(source.replace(signature, wrapped), encoding="utf-8", newline="\n")
        return
    start = source.index(signature)
    end = source.index("    fn creative_stacks(&self)", start)
    method = source[start:end]
    local_import = "                use horizon_protocol::entity_data::{Value, serializer};\n"
    if method.count(local_import) != 1 or method.count("serializer::") != 3 or method.count("Value::") != 3:
        raise ValueError("Horizon remote state recording changed before Spider-Man compatibility preparation")
    # Mixins 0.6.0 refuses nested items in a RETURN target, including use.
    # Qualify the same author types in place; packet order and replay recording
    # remain unchanged, and no additional names enter the surrounding module.
    method = method.replace(local_import, "").replace("serializer::", "horizon_protocol::entity_data::serializer::")
    method = method.replace("Value::", "horizon_protocol::entity_data::Value::")
    path.write_text(source[:start] + method + source[end:], encoding="utf-8", newline="\n")
