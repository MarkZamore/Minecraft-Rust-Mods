"""Keep received play payloads in order when the Horizon client falls behind."""

import hashlib
from pathlib import Path


ROOT = "crates/horizon-client/src/"
INGRESS = ROOT + "net/ingress.rs"
MAIN_QUEUE = ROOT + "raw_payloads/main_queue.rs"
EXECUTOR = ROOT + "raw_payloads/executor.rs"
SOURCE_SHA256 = {
    INGRESS: "f9f3e1acefa3d73b9f92268e0c1ae3578a3eb5071d087af34a52cc40ae638bb7",
    MAIN_QUEUE: "dbb84a3c01ef4ded1ab68a5e5e2ba604971e01df05036b550ff1553113383fa7",
    EXECUTOR: "e30738cc9dbca7ada5322e653ed98762f0b5b216e50a1d363923cd536ddfdbff",
}


def changes(originals: dict[str, bytes]) -> dict[str, bytes]:
    files = dict(originals)
    for name, expected in SOURCE_SHA256.items():
        if hashlib.sha256(files[name]).hexdigest() != expected:
            raise ValueError(f"Unexpected Horizon payload queue source: {name}")

    def replace(name, old, new):
        before, after = old.encode(), new.encode()
        if files[name].count(before) != 1:
            raise ValueError(f"Unexpected Horizon payload queue anchor: {name}")
        files[name] = files[name].replace(before, after)

    replace(INGRESS, """    pub fn send_waiting(&self, value: T) -> Result<(), Refused<T>> {
        let charge = (self.charge)(&value);
        Self::prepared(self.sender.reserve_waiting(charge), value)?.publish()
    }""", """    pub fn send_waiting(&self, value: T) -> Result<(), Refused<T>> {
        self.prepare_waiting(value)?.publish()
    }
    /// Reserve from the same bounded budget, leaving publication to the caller.
    pub fn prepare_waiting(&self, value: T) -> Result<Prepared<T>, Refused<T>> {
        let charge = (self.charge)(&value);
        Self::prepared(self.sender.reserve_waiting(charge), value)
    }""")
    replace(MAIN_QUEUE, """use mod_api::PayloadContextError;
""", """use horizon_protocol::payload_negotiation::Phase;
use mod_api::PayloadContextError;

fn refusal(reason: Refusal) -> PayloadContextError {
    match reason {
        Refusal::Pressure => PayloadContextError::Backpressure,
        Refusal::Closed => PayloadContextError::Disconnected,
    }
}
""")
    replace(MAIN_QUEUE, """    pub(crate) fn prepare(&self, incoming: Incoming) -> Result<PreparedMain, PayloadContextError> {
        let refusal = |reason| match reason {
            Refusal::Pressure => PayloadContextError::Backpressure,
            Refusal::Closed => PayloadContextError::Disconnected,
        };
""", """    /// Direct play payloads and ordinary play packets have the same dedicated
    /// client reader. Let it wait for room in stream order, outside the epoch
    /// gate. The receiving actor can drain or close the queue independently.
    pub(crate) fn prepare_incoming(&self, incoming: Incoming) -> Result<PreparedMain, PayloadContextError> {
        if let Self::Client(events) = self
            && incoming.epoch.phase == Phase::Play
            && matches!(incoming.body, super::Body::Payload(_))
        {
            return events
                .prepare_waiting(Event::RawPayload(Box::new(incoming)))
                .map(PreparedMain::Client)
                .map_err(|error| refusal(error.reason));
        }
        self.prepare(incoming)
    }

    pub(crate) fn prepare(&self, incoming: Incoming) -> Result<PreparedMain, PayloadContextError> {
""")
    replace(EXECUTOR, "        let prepared = match main.prepare(incoming) {",
            "        let prepared = match main.prepare_incoming(incoming) {")
    return files


def prepare_source(stage: Path) -> None:
    paths = {name: stage / name for name in SOURCE_SHA256}
    if any(not path.is_file() or path.is_symlink() or path.is_junction()
           or path.resolve(strict=True) != path.absolute() for path in paths.values()):
        raise ValueError("Horizon payload queue sources must be ordinary files")
    for name, content in changes({name: path.read_bytes() for name, path in paths.items()}).items():
        paths[name].write_bytes(content)
