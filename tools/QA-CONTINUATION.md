# Published release acceptance, 8 November 2026

Gameplay acceptance revision: `4b897f5463a36057ca6633a234779190ecc4008a`. Current summary-only publication: `58cdea81da2e2f11f63e7170146df344d3c97bf5`; the short summary is one sentence, and the detailed About text is preserved. See `qa/library-summary.json`. All exact-client runtime acceptance reports, strict release gates, installed update acceptance and public delivery passed. Later QA-only commits do not change that manifest revision. Continue with the pack AGENTS.md and the LANMinecraft storage policy; do not replace current evidence with historical receipts or disable release gates.

## Exact candidate and accepted base

- Horizon: `2ebf00a0281c592f79191e76fce0e7d522c6f425`, Minecraft 26.3, Windows x64.
- Selected native mods: 37. Only Speedrun Timer, Keystrokes and Server Atlas leave the preceding 40-mod selection by the player's instruction.
- Modded client: SHA-256 `66d74e951a05e2584ec0d8a5b332b15660eab6bc6f83f5819f0be159b41eecb7`, 101605888 bytes. See `qa/final-build.json` and `build-lock.json`.
- Bootstrap: SHA-256 `ace261a88a6a5438ede83e9571be2d1189ec7d950948d751460071af446b0c6d`, 2923008 bytes; source `ea131ad0395b61b801c0d07147a9374aa545cf8e`.
- Accepted public Edition base: `34d18e6956912ba5412e17eefdd5889f814e6cee`. Its manifest SHA-256 is `c6da36d897ade557ad8027192484f16f9f2e870367869fe21df2dc7a2ba30495`; distribution-lock SHA-256 is `07087721379b2e48856c123fabad060a0cff74f158bd96f67deed2b303e11f0f`.

The base was independently restored through `restore_public_base.py`; public archive hashes and all 479 managed files passed verification. `qa/base-public-readback.json` records that evidence. Final staging preserved the client, bootstrap and presentation bytes; only the approved library text and native base provenance changed. See `qa/final-base-promotion.json`. The actual inventory contains 37 mods, with official mod URLs, the stable `minecraft-10` ID/path/title, no native-project websiteUrl, and the approved short summary and five-sentence description.

## Actual PASS evidence

- `qa/orbital-startup.json`: full exact-client gameplay through the packaged bootstrap, world save/reopen, Physics clicks, first-screen narrator off, all 65 control mappings, game-local Tectonic settings, Orbital terrain checks, pause-menu closing and actual numeric publication port.
- `qa/backup-guard.json`: first-open backup bytes verified, no duplicate on reopening, and personal settings preserved. This uses a disposable current-client world.
- `qa/multiplayer.json`: complete non-diagnostic local host/guest run, positive and negative guest permissions, JEI Survival/Creative/search/recipes/uses, guest terrain changes, and concurrent own/remote Orbital effects. Both clients exited with code 0 and their worlds were removed.
- `qa/server-policy.json`: six ordinary launches, nine view/navigation observations per launch and six genuine desktop Escape actions against the owned PID and title. Fresh Steam view stays empty; reconstructed legacy and manual unknown-field NBT remain byte-identical. All six clients exited with code 0 and fixtures were removed.
- `qa/native-notices.json`: exact-candidate native notices passed all 12 checks with 13 actual captures, including fullscreen, DPI, input, expiry, visible pixels and fresh heartbeat evidence. Foreground before/after input remained the same owned window (`4590276`), fixture cleanup passed, and the published-base validator accepted the report. Earlier preliminary adapter failures are diagnostic history superseded by this complete final report.

The build, input/license/official-page checks, bootstrap source contract and these report validators passed. `qa/update-preservation.json` records the real final baseline and 48 regression rejection checks. The distribution lock contains 520 managed files and the exact accepted build receipt. `qa/upstream-public-check.json` confirms the target was the latest published Nightly before release.

## Installed update and public delivery

`qa/update-engine.json` records the actual `25b37b08` to `4b897f54` update: 520 managed files verified, 12 personal files preserved, explicit 65-key preset and WPF library presentation passed. A repeated update changed and downloaded zero files. The disposable installation was removed.

The source was committed cleanly, exported and verified for the exact published revision. All four archive GETs passed size/SHA-256 checks, safe ZIP member maps and every managed member hash before the manifest was promoted. The manifest was then freshly downloaded and matched all prepared bytes: 161282 bytes, SHA-256 `cdc3a831bc091d4c597def0f0a6d2657d06a9d61e221169a652b0fdba9f36ad1`. See `qa/public-assets-4b897f5.json`, `qa/public-delivery-4b897f5.json` and the reproducible `verify_public_delivery.py` tool. Existing release archives were preserved.

The pack owns `DISABLE_VULKAN_OW_OVERLAY_LAYER=1` in `launcher/environment-defaults.json`; final QA derives it from the staged presentation without a manual inherited mask. The generic bootstrap preserves explicit inherited values, including empty values. The confirmed `owclient.dll` startup crash and successful policy observations were on Intel Arc B390/Vulkan; they do not establish a fix for ASSin's Radeon crash.

Live Steam Shift+Tab, invitations/two-account joining and world transfer remain NOT RUN by the player's deferral. Radeon-specific reproduction and historical removed-block migration also remain NOT RUN. Local TCP and the current-world backup test must not be presented as those scenarios.

Cleanup passed: 8036360278 bytes of recoverable caches, builds, candidates and distributions were removed. Only 43.294 MiB of compact evidence remains in registered LANMinecraft `artifacts/agent-runs/rust-engine-6560641-20261107`, owner `codex/rust-engine-6560641`, deletion deadline 9 November 2026; no game fixtures remain active. Final notice/MP images and reports were preserved byte-for-byte. See `qa/storage-cleanup-4b897f5.json`. Unique source, Git repositories, user worlds and personal settings were not touched. Public pinned inputs and committed tools remain sufficient for continuation on another PC.
