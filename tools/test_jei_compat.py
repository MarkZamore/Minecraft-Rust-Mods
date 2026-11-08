"""Exercise patched JEI against host lifecycle changes, without a game copy."""

import argparse
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import subprocess

import jei_compat


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True, help="Verified original mixins/jei directory")
    parser.add_argument("--work", type=Path, required=True, help="An agent-runs task subdirectory")
    parser.add_argument("--baseline", action="store_true", help="Confirm the unpatched author sources fail lifecycle cases")
    args = parser.parse_args()
    work = args.work.resolve()
    if "agent-runs" not in work.parts:
        raise ValueError("Test work must be inside the registered agent-runs task")
    work.mkdir(parents=True, exist_ok=True)
    created = datetime.now(timezone.utc)
    (work / "RUN.json").write_text(json.dumps({
        "owner": "codex/jei_fix",
        "purpose": "JEI lifecycle harness; generated test source and executable removed in finally",
        "createdUtc": created.isoformat(), "expectedMiB": 30,
        "deleteBy": (created + timedelta(days=1)).date().isoformat(),
        "results": ["result.json", "baseline-result.json"],
    }, indent=2) + "\n", encoding="utf-8")
    for name, expected in jei_compat.SOURCE_HASHES.items():
        if hashlib.sha256((args.source / name).read_bytes()).hexdigest() != expected:
            raise ValueError(f"Unexpected author source: {name}")
    glue = (args.source / "glue.rs").read_text(encoding="utf-8")
    runtime = (args.source / "runtime.rs").read_text(encoding="utf-8")
    if not args.baseline:
        glue = jei_compat.adapt_glue(glue)
        runtime = jei_compat.adapt_runtime(runtime)
    fragment = glue[glue.index("/// The viewer while"):glue.index("/// The panel's events:")]
    template = Path(__file__).with_name("JeiCompatTests.rs").read_text(encoding="utf-8")
    test_source = work / "jei-tests.rs"
    test_binary = work / "jei-tests.exe"
    # Generated sources and executable are recoverable; retain only the receipt.
    try:
        test_source.write_text(template.replace("/* RUNTIME */", runtime).replace("/* GLUE */", fragment), encoding="utf-8")
        subprocess.run(["rustc", "--test", "--edition=2024", str(test_source), "-o", str(test_binary)], check=True)
        result = subprocess.run([str(test_binary)], text=True, encoding="utf-8", capture_output=True)
        if args.baseline:
            failures = {line.split()[1].removesuffix(" ...") for line in result.stdout.splitlines()
                        if line.startswith("test ") and line.endswith(" ... FAILED")}
            expected = {f"game::{name}" for name in (
                "missing_panel_is_restored_without_resetting_search",
                "new_game_object_does_not_inherit_an_invisible_viewer",
                "replaced_catalog_updates_items_and_keeps_search",
                "replaced_recipes_keep_history_and_refresh_book_ids",
            )}
            if result.returncode == 0 or failures != expected:
                raise RuntimeError(f"Unexpected baseline result: {result.stdout}")
        else:
            result.check_returncode()
        print(result.stdout)
        receipt = {
            "sourceSha256": jei_compat.SOURCE_HASHES,
            "recipeSha256": hashlib.sha256(Path(jei_compat.__file__).read_bytes()).hexdigest(),
            "rustc": subprocess.check_output(["rustc", "--version"], text=True).strip(),
            "lifecycleTests": 7,
            "status": "EXPECTED_FAILURES" if args.baseline else "PASS",
            "actualWindowsClient": "NOT RUN",
            "summary": result.stdout.strip().splitlines()[-1],
        }
        receipt_name = "baseline-result.json" if args.baseline else "result.json"
        (work / receipt_name).write_text(json.dumps(receipt, indent=2) + "\n", encoding="utf-8")
    finally:
        test_binary.unlink(missing_ok=True)
        test_binary.with_suffix(".pdb").unlink(missing_ok=True)
        test_source.unlink(missing_ok=True)


if __name__ == "__main__":
    main()
