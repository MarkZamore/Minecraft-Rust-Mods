"""Run the pinned shared notice QA against the exact Mods bootstrap and inventory."""

import argparse
import json
import os
from pathlib import Path
import sys

from native_build import ROOT, load
from native_notice_acceptance import execution_context, module_at, shared_modules, source_proof, verify_native_notices


def windows_cleanup_path(path):
    """Keep the shared fixture guards while enabling Windows long-path IO."""
    path = Path(path)
    if os.name != "nt":
        return path
    absolute = os.path.abspath(path)
    if absolute.startswith("\\\\?\\"):
        return Path(absolute)
    if absolute.startswith("\\\\"):
        return Path("\\\\?\\UNC\\" + absolute[2:])
    return Path("\\\\?\\" + absolute)


def long_path_cleanup(remove_fixture):
    def cleanup(path, qa):
        # The original implementation still checks the exact parent, allowed
        # fixture name and every link, and owns the bounded file-lock retry.
        return remove_fixture(windows_cleanup_path(path), windows_cleanup_path(qa))
    return cleanup


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("base-repo", "candidate", "controller", "cache", "out"):
        parser.add_argument("--" + name, required=True, type=lambda value: Path(value).resolve())
    args = parser.parse_args()
    from generate_manifest import accepted_build
    from native_qa_environment import environment_policy
    from smoke_runtime import CONTENT, NATIVE
    import smoke_startup
    build, _ = accepted_build(args.candidate)
    proof = source_proof(args.base_repo, build, published=False)
    environment = environment_policy(args.candidate)
    from stage_mod_pack import environment_defaults
    if environment["environmentDefaults"] != environment_defaults():
        raise ValueError("Restage the notice candidate with the mandatory pack environment defaults")
    smoke = load(ROOT / "tools/qa/orbital-startup.json")
    if smoke.get("clientSha256") != build["clientSha256"] or smoke.get("gameplay") != "PASS":
        raise ValueError("First save the exact Mods client's complete gameplay report")
    expected = sorted(smoke["registeredNative"])
    expected_content = sorted(smoke["loadedContent"])
    inventories = []
    visible_widgets = []

    def context(candidate):
        current = source_proof(args.base_repo, build, published=False)
        if any(current[key] != value for key, value in proof.items() if key != "revision"):
            raise ValueError("The shared notice QA sources changed during acceptance")
        return execution_context(candidate, build, proof)

    def check_mods(session):
        session.control("options", "mods")
        page = session.control("ui")
        ids = sorted(item["id"] for item in page["widgets"])
        log = (session.qa / "client.log").read_text(encoding="utf-8", errors="replace")
        registered = sorted(set(NATIVE.findall(log)))
        content = sorted([list(row) for row in set(CONTENT.findall(log))])
        if registered != expected or content != expected_content or "done" not in ids or "open-mod-folder" not in ids:
            raise AssertionError("Native notices changed the accepted Mods inventory")
        # Fullscreen can expose more rows without changing the actual mod set.
        evidence = {"registeredNative": registered, "loadedContent": content}
        inventories.append(evidence)
        visible_widgets.append(ids)
        for _ in range(3):
            page = session.control("ui")
            done = [item for item in page["widgets"] if item["id"] == "done"]
            if not done:
                return evidence, shared.ui_shape(page)
            if len(done) != 1 or not done[0]["enabled"]:
                raise AssertionError("The Mods inventory has no usable Done action")
            session.control("click", done[0]["id"])
        page = session.control("ui")
        if any(item["id"] == "done" for item in page["widgets"]):
            raise AssertionError("The Mods inventory did not return from options")
        return evidence, shared.ui_shape(page)

    original_argv = sys.argv
    try:
        with shared_modules(args.base_repo):
            shared = module_at(args.base_repo / "tools/native_notices_qa.py", "_mods_notice_qa")
            shared.execution_context = context
            shared.check_mods = check_mods
            shared.remove_fixture = long_path_cleanup(shared.remove_fixture)
            original_session = shared.Session

            class ModsSession(original_session):
                def start(self):
                    super().start()
                    # The first Physics setup belongs to this disposable profile.
                    # It is the author's custom canvas, with no control widgets.
                    # Use the same reviewed Skip bounds as the full startup QA.
                    settings = self.game / smoke_startup.PHYSICS_SETTINGS
                    if not settings.is_file() or "setup_done=1" not in settings.read_text(encoding="utf-8").splitlines():
                        width, height = self.control("ui")["gui_size"]
                        self.control("click-at", str(width * 0.9015), str(height * 0.0795))
                        smoke_startup.widget(self.control, "singleplayer")
                        if not settings.is_file() or "setup_done=1" not in settings.read_text(encoding="utf-8").splitlines():
                            raise AssertionError("The technical notice profile did not complete Physics Skip Setup")

            shared.Session = ModsSession
            sys.argv = [str(args.base_repo / "tools/native_notices_qa.py"),
                        "--candidate", str(args.candidate), "--controller", str(args.controller),
                        "--cache", str(args.cache), "--out", str(args.out)]
            shared.main()
    finally:
        sys.argv = original_argv
        path = args.out / "native-notices-qa.json"
        if path.is_file():
            report = load(path)
            report.update(clientSha256=build["clientSha256"], horizonRevision=build["horizonRevision"],
                          **environment, environmentApplications=[{**environment, "environmentApplication": "bootstrap"}],
                          baseSource=proof, modInventory={"selectedIds": build["ids"],
                          "before": inventories[0] if inventories else None,
                          "after": inventories[-1] if len(inventories) >= 2 else None,
                          "visibleWidgetsBefore": visible_widgets[0] if visible_widgets else [],
                          "visibleWidgetsAfter": visible_widgets[-1] if len(visible_widgets) >= 2 else []})
            validation_error = None
            if report.get("status") == "PASS":
                try:
                    # A raw pre-publication run still needs every real shared
                    # observation. Only the published-base check is deferred.
                    verify_native_notices(report, args.candidate, build, args.base_repo, published=False)
                except Exception as error:
                    validation_error = error
                    report.update(status="FAIL", error=f"Notice acceptance evidence: {error}")
            path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            if validation_error is not None:
                raise RuntimeError("Shared native notice acceptance is incomplete; see native-notices-qa.json") from validation_error


if __name__ == "__main__":
    main()
