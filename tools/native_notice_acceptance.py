"""Bind the shared Edition notice contract to the exact Mods payload and sources."""

from contextlib import contextmanager
import hashlib
import importlib.util
import json
from pathlib import Path
import subprocess
import sys

from native_build import ROOT, load, sha


BASE_INPUTS = ("integration/steam", "tools/upstream-lock.json", "tools/overlay-receipt.json",
               "tools/stage_runtime.py", "tools/release_acceptance.py", "tools/native_notices_qa.py",
               "tools/server_list_qa.py", "tools/smoke_runtime.py", "tools/bootstrap_contract.py",
               "launcher/standalone", "launcher/environment-defaults.json", "tools/bootstrap-lock.json")
LAUNCHER_SOURCE_CHECKS = ("nativeCapabilityConstant", "capabilityAdvertisedByInstaller",
                          "nativeSessionWithoutJavaAgent", "nativeLaunchPassesDirectory",
                          "verifiedNativeProcessTree", "nativeHeartbeatBackendAndActualPid",
                          "freshHeartbeatAndErrorFallback")


def canonical_sha(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=True,
                                     separators=(",", ":")).encode("utf-8")).hexdigest()


def source_proof(base_repo, build, *, published):
    base_repo = Path(base_repo).resolve(strict=True)
    git = ["git", "-C", str(base_repo)]
    if subprocess.check_output([*git, "status", "--porcelain", "--untracked-files=all", "--", *BASE_INPUTS], text=True):
        raise ValueError("Use a clean Edition checkout for the shared native notice contract")
    upstream = load(base_repo / "tools/upstream-lock.json")
    overlay = load(base_repo / "tools/overlay-receipt.json")
    if (upstream["commit"] != build["horizonRevision"] or
            overlay["upstreamCommit"] != build["horizonRevision"] or
            overlay["recipeSha256"] != build["steamOverlayRecipeSha256"]):
        raise ValueError("The notice contract base differs from the exact Mods engine/overlay")
    bootstrap = verify_bootstrap_source(base_repo)
    if published:
        pin = load(ROOT / "tools/base-lock.json")
        if (subprocess.run([*git, "merge-base", "--is-ancestor", pin["revision"], "HEAD"], capture_output=True).returncode or
                subprocess.run([*git, "diff", "--quiet", pin["revision"], "HEAD", "--", *BASE_INPUTS]).returncode or
                sha(base_repo / "tools/distribution-lock.json") != pin["distributionLockSha256"]):
            raise ValueError("Publish native notices only against the accepted published Edition base")
    return {
        "revision": subprocess.check_output([*git, "rev-parse", "HEAD"], text=True).strip(),
        "horizonRevision": upstream["commit"], "overlayRecipeSha256": overlay["recipeSha256"],
        "contractSha256": sha(base_repo / "tools/release_acceptance.py"),
        "qaToolSha256": sha(base_repo / "tools/native_notices_qa.py"),
        "stageToolSha256": sha(base_repo / "tools/stage_runtime.py"),
        "desktopKeyHelperSha256": sha(base_repo / "tools/server_list_qa.py"),
        "gameplayToolSha256": sha(base_repo / "tools/smoke_runtime.py"),
        "bootstrapSourceSha256": bootstrap["sourceSha256"],
        "bootstrapContractSha256": sha(base_repo / "tools/bootstrap_contract.py"),
        "bootstrapLockSha256": sha(base_repo / "tools/bootstrap-lock.json"),
    }


def execution_context(candidate, build, proof):
    from generate_manifest import inventory
    return {
        "horizonRevision": build["horizonRevision"],
        "steamOverlayRecipeSha256": build["steamOverlayRecipeSha256"],
        "buildRecipeSha256": build["buildRecipeSha256"],
        "buildReceiptSha256": sha(candidate / "build-receipt.json"),
        "payloadInventorySha256": canonical_sha(inventory(candidate)),
        "noticeContractSha256": proof["contractSha256"],
        "noticeQaToolSha256": proof["qaToolSha256"],
        "noticeStageToolSha256": proof["stageToolSha256"],
        "desktopKeyHelperSha256": proof["desktopKeyHelperSha256"],
        "gameplayToolSha256": proof["gameplayToolSha256"],
        "bootstrapSourceSha256": proof["bootstrapSourceSha256"],
        "bootstrapContractSha256": proof["bootstrapContractSha256"],
        "bootstrapLockSha256": proof["bootstrapLockSha256"],
        "modsNoticeAdapterSha256": canonical_sha({name: sha(ROOT / name) for name in
            ("tools/native_notice_acceptance.py", "tools/smoke_native_notices.py", "tools/stage_mod_pack.py",
             "tools/native_qa_environment.py", "launcher/environment-defaults.json")}),
    }


def module_at(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def verify_bootstrap_source(base_repo, binary=None):
    """Require the generic production feature, exact sources and reviewed binary."""
    base_repo = Path(base_repo).resolve(strict=True)
    contract_file = base_repo / "tools/bootstrap_contract.py"
    if not contract_file.is_file():
        raise ValueError("The base bootstrap does not declare the required presentation environment-defaults contract")
    contract = module_at(contract_file, "_mods_bootstrap_contract")
    common = load(base_repo / "tools/bootstrap-lock.json")
    own = load(ROOT / "tools/bootstrap-lock.json")
    sources = contract.bootstrap_sources(base_repo)
    executable = {"path": "native/launch-rust-edition.exe", "sizeBytes": own["sizeBytes"], "sha256": own["sha256"]}
    if (common.get("schemaVersion") != 1 or own.get("schemaVersion") != 1 or
            common.get("sourceFiles") != sources or own.get("sourceFiles") != sources or
            common.get("sourceSha256") != contract.source_fingerprint(sources) or
            own.get("sourceSha256") != common["sourceSha256"] or own.get("build") != common.get("build") or
            own.get("sourceRevision") != common.get("build", {}).get("sourceCommit") or
            common.get("executable") != executable):
        raise ValueError("Pack and published base bootstrap/source pins must describe the same reviewed implementation")
    main = (base_repo / "launcher/standalone/src/main.rs").read_text(encoding="utf-8")
    module = "launcher/standalone/src/environment_defaults.rs"
    if (module not in sources or "environment_defaults::read_presentation(" not in main or
            "environment_defaults.apply(&mut client);" not in main or
            '"environmentDefaults"' not in (base_repo / module).read_text(encoding="utf-8")):
        raise ValueError("Port and verify mandatory generic client environment-defaults support before publishing")
    if binary is not None:
        contract.verify_bootstrap(binary, root=base_repo)
    return common


@contextmanager
def shared_modules(base_repo):
    """Keep shared absolute imports bound to this explicitly selected checkout."""
    old = {name: sys.modules.get(name) for name in ("bootstrap_contract", "stage_runtime", "release_acceptance")}
    sys.path.insert(0, str(base_repo / "tools"))
    try:
        bootstrap = module_at(base_repo / "tools/bootstrap_contract.py", "_mods_notice_bootstrap")
        sys.modules["bootstrap_contract"] = bootstrap
        stage = module_at(base_repo / "tools/stage_runtime.py", "_mods_notice_stage")
        sys.modules["stage_runtime"] = stage
        contract = module_at(base_repo / "tools/release_acceptance.py", "_mods_notice_contract")
        sys.modules["release_acceptance"] = contract
        yield contract
    finally:
        sys.path.pop(0)
        for name, module in old.items():
            if module is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = module


def verify_native_notices(report, candidate, build, base_repo, *, published=True):
    from generate_manifest import file_info, inventory
    from stage_mod_pack import presentation, verify_environment_evidence
    base_repo = Path(base_repo).resolve(strict=True)
    proof = source_proof(base_repo, build, published=published)
    recorded = report.get("baseSource", {})
    revision = recorded.get("revision", "")
    if (report.get("clientSha256") != build["clientSha256"] or report.get("horizonRevision") != build["horizonRevision"] or
            any(recorded.get(key) != value for key, value in proof.items() if key != "revision") or
            not isinstance(revision, str) or len(revision) != 40 or
            subprocess.run(["git", "-C", str(base_repo), "merge-base", "--is-ancestor", revision, "HEAD"], capture_output=True).returncode or
            subprocess.run(["git", "-C", str(base_repo), "diff", "--quiet", revision, "HEAD", "--", *BASE_INPUTS]).returncode):
        raise ValueError("Native notice acceptance has another Mods client, engine or shared source contract")
    files = inventory(candidate)
    presentation_file = candidate / "native/presentation.json"
    if load(presentation_file) != presentation():
        raise ValueError("Native notice QA used a candidate without the mandatory pack presentation environment policy")
    verify_environment_evidence(report, sha(presentation_file), ("bootstrap",))
    runtime = load(candidate / "portable-pack.json")["nativeRuntime"]
    bootstrap = candidate / runtime["executable"]
    verify_bootstrap_source(base_repo, bootstrap)
    if (runtime["executable"] != "native/launch-rust-edition.exe" or
            report.get("bootstrap") != file_info(bootstrap)):
        raise ValueError("Native notices require the exact staged bootstrap")
    name = load(candidate / "native/presentation.json")["name"]
    smoke = load(ROOT / "tools/qa/orbital-startup.json")
    observed = report.get("modInventory", {})
    before, after = observed.get("before"), observed.get("after")
    if (observed.get("selectedIds") != build["ids"] or not isinstance(before, dict) or before != after or
            before.get("registeredNative") != sorted(smoke["registeredNative"]) or
            before.get("loadedContent") != sorted(smoke["loadedContent"])):
        raise ValueError("Native notices did not retain the accepted Mods inventory")
    with shared_modules(base_repo) as contract:
        contract.verify_native_notices(report, files, execution_context(candidate, build, proof), name)


def verify_launcher_support(report):
    pin = load(ROOT / "tools/launcher-notices-lock.json")
    if (pin.get("schemaVersion") != 1 or pin.get("feature") != "native-launcher-notices-v1" or
            not pin.get("sourceFiles") or set(pin.get("requiredSourceChecks", [])) != set(LAUNCHER_SOURCE_CHECKS) or
            report.get("schemaVersion") != 1 or report.get("status") != "PASS" or
            report.get("feature") != pin["feature"] or report.get("commitSha") != pin["commitSha"] or
            report.get("releaseNumber") != pin["releaseNumber"] or report.get("publicAsset") != pin["publicAsset"] or
            report.get("publicUpdateMetadata") != pin["publicUpdateMetadata"] or
            report.get("sourceFiles") != pin["sourceFiles"] or
            not all(report.get("sourceChecks", {}).get(key) is True for key in LAUNCHER_SOURCE_CHECKS) or
            report.get("publicReadback", {}).get("sha256") != pin["publicAsset"]["sha256"] or
            report.get("publicReadback", {}).get("sizeBytes") != pin["publicAsset"]["sizeBytes"]):
        raise ValueError("The required native-notice capability has no verified published supporting launcher/source")
