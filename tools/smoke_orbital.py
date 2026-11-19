"""Exercise Orbital Laser in a disposable, rendered Horizon smoke world."""

from math import floor
import re
import struct
import time

from native_build import sha


ID = "orbital"
DESIGNATOR = "orbital:laser_designator"
SETTINGS = "horizon-mods/.horizon-mod-data/orbital/settings"
SETTINGS_EXPECTED = {"laser": "plasma", "terrain": "0", "seed": "7"}
RENDER_MODS = {"motion_blur", "horizon_physics"}
WHEEL_KEY = "insert"
WHEEL_MAPPING = "key_orbital.wheel"
STRIKE = re.compile(r"\[mod orbital\] strike \[(\w+)\] at (-?\d+) (-?\d+) (-?\d+)")
SHADER_FAILURE = re.compile(
    r"post pass (?:did not compile|over \d+ KiB)|world draws smaller than the window: no post pass"
    r"|wgpu.*(?:Validation Error|DeviceLost)|mod orbital did not start",
    re.IGNORECASE,
)


def command(control, text, expected):
    response = control("command", text)
    if not any(expected in line for line in response["chat"]):
        raise ValueError(f"{text!r} did not answer {expected!r}: {response!r}")


def wait_client_message(control, expected, timeout=30):
    # Native mod feedback is ChatSource::Client. The current controller's event
    # stream tracks server chat; its styled view also includes the rendered
    # client lines. Observe the actual UI instead of waiting for a server event.
    deadline = time.monotonic() + timeout
    while True:
        reply = control("messages", "50")
        lines = [*reply.get("chat", []), *(row.get("text", "") for row in reply.get("styled", []))]
        if any(expected in line for line in lines):
            return
        if time.monotonic() >= deadline:
            raise ValueError(f"Native client feedback did not display {expected!r}: {reply!r}")
        time.sleep(0.1)


def item_present(control, timeout=30):
    deadline = time.monotonic() + timeout
    count = 0
    while time.monotonic() < deadline:
        count = control("inventory", timeout=max(1, deadline - time.monotonic()))["counts"].get(DESIGNATOR, 0)
        if count == 1:
            return
        if count > 1:
            break
        time.sleep(0.25)
    raise ValueError(f"Expected exactly one Orbital Laser Designator in the synchronized inventory, found {count}")


def sample(control, points):
    result = []
    for position in points:
        block = control("block", *map(str, position))
        if not block["loaded"]:
            raise ValueError(f"Orbital QA fixture is outside the loaded chunks at {position!r}")
        result.append(block["block"])
    return result


def wait_for_crater(control, points, before, timeout=120):
    deadline = time.monotonic() + timeout
    after = before
    while time.monotonic() < deadline:
        blocks = [control("block", *map(str, point), timeout=max(1, min(10, deadline - time.monotonic())))
                  for point in points]
        if all(block["loaded"] for block in blocks):
            after = [block["block"] for block in blocks]
            if all(old != new for old, new in zip(before, after, strict=True)):
                return after
        time.sleep(min(0.5, max(0, deadline - time.monotonic())))
    raise ValueError(f"Terrain-on Plasma did not carve both sampled layers within {timeout} seconds: {after!r}")


def wait_for_world_ticks(control, count, timeout=90):
    # Initial material pipelines may spend over ten seconds without ticking
    # after the controller's loaded condition becomes true.
    initial = control("state")["ticks"]
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        state = control("state", timeout=max(1, deadline - time.monotonic()))
        if state["ticks"] >= initial + count:
            return
        time.sleep(0.25)
    raise TimeoutError(f"The rendered world did not advance {count} ticks within {timeout} seconds")


def camera(control, target):
    x, y, z = target
    # Use the long-range Designator ray, outside vanilla block-interaction range.
    control("look-at", str(x + 0.5), str(y + 0.5), str(z + 0.5))


def hold_designator(control):
    control("mouse", "right", "down")
    try:
        # The designator requires 14 whole game ticks to acquire a target.
        wait_for_world_ticks(control, 20)
    finally:
        control("mouse", "right", "up")


def photograph(control, qa, name):
    path = qa / f"orbital-{name}.png"
    control("screenshot", str(path))
    with path.open("rb") as image:
        header = image.read(24)
    if len(header) != 24 or header[:8] != b"\x89PNG\r\n\x1a\n" or header[12:16] != b"IHDR":
        raise ValueError(f"The rendered Orbital QA frame is not a PNG: {path}")
    size = struct.unpack(">II", header[16:24])
    if min(size) < 100:
        raise ValueError(f"The rendered Orbital QA frame is too small: {size!r}")
    return {"file": path.name, "sha256": sha(path), "size": list(size)}


def check_render_mod_switches(game):
    path = game / "horizon-mods.txt"
    if path.exists():
        lines = path.read_text(encoding="utf-8").splitlines()
        if any(f"mod.{mod}:false" in lines for mod in RENDER_MODS | {ID}):
            raise ValueError("An Orbital or rendering mod is disabled in the isolated QA instance")


def check_separate_controls(control, qa, target):
    x, y, z = target
    name = "orbital-key-smoke"
    command(control, f"/hm pos1 {x} {y} {z}", "Corner 1 at")
    command(control, f"/hm pos2 {x + 1} {y} {z}", "Corner 2 at")
    command(control, f"/hm save {name}", f"Saving {name}.litematic")
    wait_client_message(control, f"Saved {name}")
    reply = control("command", f"/hm load {name}")
    if not any("Loading" in line or f"Loaded {name}" in line for line in reply["chat"]):
        raise ValueError(f"Horizonmatica did not start or complete loading: {reply!r}")
    wait_client_message(control, f"Loaded {name}")
    command(control, "/hm info", "rotation none,")

    control("key", "n", "tap")
    control("key", "r", "tap")
    control("wait-for", "screen", "playing", "--timeout", "10")
    command(control, "/hm info", "rotation cw_90,")
    # The control socket accepts every canonical game key; its short aliases
    # cover only a subset and do not include Insert.
    control("key", f"key.keyboard.{WHEEL_KEY}", "tap")
    control("wait-for", "screen", "mod", "--timeout", "10")
    wheel = photograph(control, qa, "wheel")
    control("key", "esc", "tap")
    control("wait-for", "screen", "playing", "--timeout", "10")
    reply = control("command", "/hm info")["chat"]
    rotation = next((line for line in reply if " rotation " in line), "")
    if "rotation cw_90," not in rotation:
        raise ValueError(f"Opening the Orbital wheel changed the schematic rotation: {reply!r}")
    command(control, "/hm unload all", "Unloaded 1 placement")
    command(control, "/hm area clear", "Area selection cleared")
    return wheel


def exercise(control, qa):
    game = qa / "game"
    check_render_mod_switches(game)
    renderer = control("status")["renderer"]
    if not renderer or not renderer["backend"]:
        raise ValueError("Orbital Laser QA requires the actual Windows renderer")
    command(control, "/orbital give", "Orbital Laser Designator added to your inventory")
    wait_for_world_ticks(control, 4)
    item_present(control)
    control("select", DESIGNATOR)

    control("command", "time set noon")
    control("command", "weather clear")
    x, y, z = map(floor, control("state")["player"]["position"])
    y = min(y + 5, 252)
    target = (x, y, z)
    points = [target, (x + 1, y - 3, z)]
    fixture_blocks = ["minecraft:gold_block", "minecraft:stone"]
    for point, block in zip(points, fixture_blocks, strict=True):
        control("command", f"setblock {' '.join(map(str, point))} {block}")
    control("wait", "4")
    frames = {"wheel": check_separate_controls(control, qa, target)}
    # A raised stationary viewpoint sees the beam and stays beyond the crater rim.
    camera_y = y + 38
    camera_z = z + 72
    control("command", f"fill {x - 2} {camera_y - 1} {camera_z - 2} "
            f"{x + 2} {camera_y - 1} {camera_z + 2} minecraft:smooth_stone")
    control("command", f"fill {x - 2} {camera_y} {camera_z - 2} "
            f"{x + 2} {camera_y + 3} {camera_z + 2} minecraft:air")
    control("tp", str(x + 0.5), str(camera_y), str(camera_z + 0.5))
    control("wait-for", "loaded", "--timeout", "90")
    # The long-range ray must hit the gold fixture, even when a tree grew in front of it.
    control("command", f"fill {x} {y} {z + 1} {x} {camera_y + 2} {camera_z - 1} minecraft:air")
    control("wait", "20")
    if sample(control, points) != fixture_blocks:
        raise ValueError("Orbital terrain fixture could not be prepared")

    command(control, "/orbital seed 7", "Seed: held at 7")
    command(control, "/orbital terrain off", "Terrain damage off")
    command(control, "/orbital knockback off", "Knockback off")
    control("command", "/orbital plasma")
    camera(control, target)
    frames["baseline"] = photograph(control, qa, "baseline")
    hold_designator(control)
    control("wait", "90")
    frames["plasma"] = photograph(control, qa, "plasma")
    command(control, "/orbital stop", "Strike stopped")
    if sample(control, points) != fixture_blocks:
        raise ValueError("The terrain-off Plasma strike changed the fixture")

    # Hurricane is compiled as its own post pass, independent of Plasma's pass.
    control("command", "/orbital hurricane")
    camera(control, target)
    hold_designator(control)
    control("wait", "290")
    frames["hurricane"] = photograph(control, qa, "hurricane")
    command(control, "/orbital stop", "Strike stopped")
    if sample(control, points) != fixture_blocks:
        raise ValueError("The terrain-off Hurricane strike changed the fixture")

    control("command", "/orbital plasma")
    command(control, "/orbital terrain on", "Terrain damage on")
    camera(control, target)
    hold_designator(control)
    changed = wait_for_crater(control, points, fixture_blocks)
    frames["crater"] = photograph(control, qa, "crater")
    command(control, "/orbital terrain off", "Terrain damage off")
    return {"target": list(target), "fixture": [list(point) for point in points],
            "blocksBefore": fixture_blocks, "blocksAfter": changed, "terrainOffUnchanged": True,
            "controls": {"orbitalWheel": WHEEL_KEY, "horizonmaticaMoveMode": "n", "rotation": "r"},
            "renderer": {name: renderer[name] for name in ("gpu", "backend", "size")},
            "frames": frames}


def verify_reopened(control, game, evidence):
    item_present(control)
    # An all-air section can be absent from the client's chunk cache on reopen.
    for point, block in zip(evidence["fixture"], evidence["blocksAfter"], strict=True):
        command(control, f"execute if block {' '.join(map(str, point))} {block}", "Test passed")
    command(control, "/orbital seed", "Seed: held at 7")
    settings = dict(line.split("=", 1) for line in (game / SETTINGS).read_text(encoding="utf-8").splitlines()
                    if "=" in line)
    if {key: settings.get(key) for key in SETTINGS_EXPECTED} != SETTINGS_EXPECTED:
        raise ValueError("Orbital Laser did not retain the expected game-local settings")
    check_render_mod_switches(game)
    evidence["reopened"] = {"inventory": DESIGNATOR, "terrain": evidence["blocksAfter"],
                            "settings": SETTINGS_EXPECTED}


def verify_logs(first, reopened, registered_native, evidence):
    target = tuple(evidence["target"])
    strikes = [(laser, tuple(map(int, (x, y, z)))) for laser, x, y, z in STRIKE.findall(first)]
    expected = [(laser, target) for laser in ("plasma", "hurricane", "plasma")]
    if strikes != expected or STRIKE.search(reopened):
        raise ValueError(f"Unexpected Orbital strike events: {strikes!r}, expected {expected!r}")
    if missing := (RENDER_MODS | {ID}) - registered_native:
        raise ValueError(f"Orbital or a rendering mod did not register in this client: {sorted(missing)!r}")
    errors = [line for line in (first + "\n" + reopened).splitlines() if SHADER_FAILURE.search(line)]
    if errors:
        raise ValueError(f"The Windows Orbital render test logged a shader/device failure: {errors[:5]!r}")
    evidence["strikeEvents"] = [{"laser": laser, "target": list(at)} for laser, at in strikes]
    evidence["concurrentRenderMods"] = sorted(RENDER_MODS)
    evidence["shaderErrors"] = []
