# Candidate checkpoint, 8 November 2026

This is an unfinished source checkpoint, not a published release. Continue in this clean worktree with the pack AGENTS.md and the LANMinecraft storage policy. Do not replace missing runtime evidence with old receipts or disable the release gates.

## Frozen candidate

- Horizon: `2ebf00a0281c592f79191e76fce0e7d522c6f425`.
- Selected native mods: 37. Only Speedrun Timer, Keystrokes and Server Atlas were removed from the preceding 40-mod selection by the player's instruction.
- Modded Windows client: SHA-256 `66d74e951a05e2584ec0d8a5b332b15660eab6bc6f83f5819f0be159b41eecb7`, 101605888 bytes; exact build receipt is `tools/qa/final-build.json`.
- Candidate bootstrap: SHA-256 `ace261a88a6a5438ede83e9571be2d1189ec7d950948d751460071af446b0c6d`, 2923008 bytes, source `ea131ad0395b61b801c0d07147a9374aa545cf8e`.
- Currently accepted public Edition base is still `3bf42761e1f9302ed19820d2a010b78e2c3748db`. It does not implement the new environment-defaults contract. This mismatch intentionally blocks release until the updated base is published and independently read back.

## Verified and pending

The locked 37-mod build, input/license/page audit and source/component checks passed. Earlier diagnostic gameplay and multiplayer runs identified and exercised the repairs, but do not substitute for final acceptance of this exact staged presentation and bootstrap.

Windows Application Error identified an injected `owclient.dll` crash. Three diagnostic launches of the same client succeeded with the Overwolf Vulkan layer's documented process-local disable variable. The pack now owns that default in `launcher/environment-defaults.json`; the common bootstrap preserves explicit inherited values. No registry, global environment, foreign processes, backend, Steam layer or game-distance settings were changed. This was observed on Intel Arc B390/Vulkan and is not a confirmed fix for ASSin's Radeon crash.

At this checkpoint the laptop was at 3%, discharging, and a Windows low-battery dialog blocked genuine desktop input. All root-owned game processes were stopped. Resume GPU and desktop acceptance only after physical power and a clear foreground are confirmed with the Edition chat.

Required before release:

1. Accept and independently restore the newly published Edition base, updating its source/distribution/bootstrap pins. Keep the modded client above; do not substitute Edition's executable.
2. Restage through `stage_mod_pack.py`. Verify the exact policy/presentation and bootstrap feature contract. Final tests must not inherit a manually supplied diagnostic mask.
3. Run full `smoke_runtime.py --bootstrap`, backup guard, complete concurrent-effects multiplayer, native notices and the six ordinary-start server-policy runs with genuine desktop key acknowledgements.
4. Update actual loaded-mod inventory and preview from the exact client. Run updater/personal-data preservation acceptance and all strict release gates against real final reports.
5. Merge current upstream metadata, preserving the later approved five-sentence library description, stable ID/path/title, no native-project websiteUrl, and official individual mod URLs.
6. Recheck latest upstream revision, publish archives before manifest, then read back every public SHA-256 before promoting the manifest.

Steam overlay/two-account acceptance remains NOT RUN by the player's explicit deferral. Radeon-specific reproduction and historical removed-block migration are also unverified.

All recoverable runtime data belongs under the registered LANMinecraft `artifacts/agent-runs/rust-engine-6560641-20261107` run, owner `codex/rust-engine-6560641`, deletion deadline 8 November 2026. These local files are conveniences, not the sole source of the implementation. `restore_public_base.py` reproduces an accepted public base from pinned remote assets. Preserve unique source, worlds and personal settings during cleanup.
