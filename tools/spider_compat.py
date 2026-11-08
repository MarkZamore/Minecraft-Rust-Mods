"""Keep the author's remote state sender usable by the Spider-Man return hook."""

from hashlib import sha256
from pathlib import Path

SOURCE_SHA256 = "412038605c92bd98a2fbd07f36ac0c9f03a972a48c8b5119ec582f579d09a097"


def prepare_source(workspace: Path) -> None:
    path = workspace / "crates/horizon-client/src/game/remote.rs"
    raw = path.read_bytes()
    if sha256(raw).hexdigest() != SOURCE_SHA256:
        raise ValueError("Horizon remote source changed before Spider-Man compatibility preparation")
    source = raw.decode("utf-8")
    start = source.index("    fn remote_send_state(&mut self, input: u8) {")
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
