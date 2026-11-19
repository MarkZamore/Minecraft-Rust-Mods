"""Build the pinned SDK for the current Horizon staging layout."""

import json
from pathlib import Path
import shutil
import subprocess
import tarfile
import tempfile

from inspect_mods import read_https, sha256


REVISION = "e59da9094f0c2fdda6c627fe9eff22dec6a906f0"
ARCHIVE_SIZE = 205924
ARCHIVE_SHA256 = "5ee6da27c822fc3c1b8258618b8de68356dbee8df8f7dc0b7493b8d96620710d"
SOURCE_PATCH = "tools/mixins/narrow-reference-rule.patch"
SOURCE_PATCH_SHA256 = "52eaeef7cac10d894066635dc46b12f76d877416317f06eaf385f176de5e1c35"
COMPAT_PATCH = Path(__file__).resolve().with_name("mixins-contained-rust-paths.patch")


def sdk_tool(source, cache, exe, env):
    official_patch = source / SOURCE_PATCH
    if sha256(official_patch.read_bytes()) != SOURCE_PATCH_SHA256:
        raise ValueError("The signed Horizon patch differs from the reviewed SDK patch")
    identity = {
        "revision": REVISION,
        "archiveSha256": ARCHIVE_SHA256,
        "horizonPatchSha256": SOURCE_PATCH_SHA256,
        "modulePathPatchSha256": sha256(COMPAT_PATCH.read_bytes()),
    }
    suffix = sha256(json.dumps(identity, sort_keys=True).encode("ascii"))[:16]
    entry = cache / (REVISION[:12] + "-" + suffix)
    binary = entry / ("horizon-mixins" + exe)
    receipt = entry / "source.json"
    if cache.is_symlink() or cache.is_junction():
        raise ValueError("Linked path in the SDK cache")
    cache.mkdir(parents=True, exist_ok=True)
    if not entry.exists():
        with tempfile.TemporaryDirectory(dir=cache, prefix="sdk-") as temporary:
            work = Path(temporary)
            archive = work / "sdk.tar.gz"
            url = f"https://codeload.github.com/horizon-rust/horizon-mixins/tar.gz/{REVISION}"
            data = read_https(url, ARCHIVE_SIZE)
            if len(data) != ARCHIVE_SIZE or sha256(data) != ARCHIVE_SHA256:
                raise ValueError("The SDK archive differs from the reviewed Git tree")
            archive.write_bytes(data)
            with tarfile.open(archive) as tar:
                members = tar.getmembers()
                prefix = f"horizon-mixins-{REVISION}"
                if any(not (m.name == prefix or m.name.startswith(prefix + "/")) or
                       not (m.isdir() or m.isfile()) for m in members):
                    raise ValueError("Unexpected SDK source archive member")
                tar.extractall(work, filter="data")
            sdk = work / prefix
            patches = [str(official_patch.resolve()), str(COMPAT_PATCH)]
            git_env = {**env, "GIT_CEILING_DIRECTORIES": str(work.resolve())}
            subprocess.run(["git", "apply", "--recount", "--whitespace=nowarn", *patches],
                           cwd=sdk, env=git_env, check=True)
            result = work / "installed"
            subprocess.run(["cargo", "install", "--locked", "--path", sdk / "crates/horizon-mixins-cli",
                            "--root", result], env=env, check=True)
            candidate = work / "verified"
            candidate.mkdir()
            shutil.move(result / "bin" / binary.name, candidate / binary.name)
            record = {**identity, "sha256": sha256((candidate / binary.name).read_bytes())}
            (candidate / receipt.name).write_text(json.dumps(record, sort_keys=True) + "\n",
                                                  encoding="ascii", newline="\n")
            try:
                candidate.rename(entry)
            except FileExistsError:
                pass
    if any(path.is_symlink() or path.is_junction() for path in (entry, binary, receipt)):
        raise ValueError("Linked path in the SDK cache")
    observed = {**identity, "sha256": sha256(binary.read_bytes())}
    if json.loads(receipt.read_bytes()) != observed:
        raise ValueError("The cached mixin tool differs from the pinned source build")
    return binary, identity
