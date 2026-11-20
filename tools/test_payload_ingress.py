"""Verify the reviewed payload dispatch against the real Horizon bounded queue."""

import argparse
import hashlib
from pathlib import Path
import subprocess
import tempfile

from orbital_compat.payload_ingress import INGRESS, MAIN_QUEUE, SOURCE_SHA256, changes


CHANNEL = "crates/horizon-mod-runtime/src/byte_channel.rs"
CHANNEL_SHA256 = "77ca3f333c33f6627a3fd1401bd4dfb901d88d45b35104c885e1b3705bc05beb"
HERE = Path(__file__).resolve().parent


def run(source, run_root):
    if not (run_root / "RUN.json").is_file() or run_root.parent.name != "agent-runs":
        raise ValueError("Use a registered QA run")
    originals = {name: (source / name).read_bytes() for name in SOURCE_SHA256}
    patched = changes(originals)
    queue = (source / CHANNEL).read_bytes()
    if hashlib.sha256(queue).hexdigest() != CHANNEL_SHA256:
        raise ValueError("The exact Horizon byte channel differs")
    with tempfile.TemporaryDirectory(dir=run_root, prefix="payload-ingress-tests-") as temporary:
        work = Path(temporary)
        (work / "byte_channel.rs").write_bytes(queue)
        (work / "ingress.rs").write_bytes(patched[INGRESS])
        (work / "raw_payloads").mkdir()
        (work / "raw_payloads/main_queue.rs").write_bytes(patched[MAIN_QUEUE])
        harness = work / "payload_ingress.rs"
        harness.write_bytes((HERE / "test_payload_ingress.rs").read_bytes())
        executable = work / "payload_ingress.exe"
        subprocess.run(["rustup", "run", "1.99.1-x86_64-pc-windows-msvc", "rustc", "--edition=2024",
                        "--test", "-o", str(executable), str(harness)], cwd=work, check=True)
        subprocess.run([str(executable)], check=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("source", "run-root"):
        parser.add_argument(f"--{name}", required=True, type=lambda value: Path(value).resolve(strict=True))
    args = parser.parse_args()
    run(args.source, args.run_root)


if __name__ == "__main__":
    main()
