"""Exercise the 21 playable native Orbital Laser 0.5.17 abilities in disposable worlds."""

import argparse
from concurrent.futures import ThreadPoolExecutor
from contextlib import ExitStack
from dataclasses import dataclass
import json
from math import floor
from pathlib import Path
import re
import subprocess
import time

from PIL import Image, ImageChops

from native_build import load, sha, write
import smoke_orbital as orbital
from smoke_mod_hud import capture_root
from smoke_multiplayer import Client, GUEST, cleanup_game, guest_modes, join, open_from_menu, until


SOURCE = "https://github.com/Stink-O/orbital-laser/tree/6311d5f9ba8610deeab35a3ef46b43b9ab7b01e8"
SEED = 137248
DESIGNATOR_SEED = 7
HOST = "OrbitalQA"
WIP = {"fold": "The Fold", "earthquake": "The Earthquake"}
AIR = "minecraft:air"
GOLD = "minecraft:gold_block"
STONE = "minecraft:stone"
GRASS = "minecraft:grass_block"
TSUNAMI_GROUND = (AIR, "minecraft:mud", "minecraft:packed_mud", "minecraft:coarse_dirt",
                  "minecraft:gravel", "minecraft:sand", "minecraft:clay", "minecraft:water",
                  "minecraft:water[level=0]", "minecraft:dirt")
RENDER_ERROR = re.compile(
    orbital.SHADER_FAILURE.pattern
    + r"|(?:world warp|post shader|native post|GPU shader).{0,80}(?:failed|did not compile|invalid)"
    + r"|thread .* panicked|device (?:is )?lost|out of GPU memory",
    re.IGNORECASE,
)
REMOTE = re.compile(r"\[mod orbital\] remote visual applied .*shader=(\d+) generation=(\d+)")


@dataclass(frozen=True)
class Probe:
    offset: tuple[int, int, int]
    before: str
    after: tuple[str, ...] = ()


CENTER = (Probe((0, 0, 0), GOLD, (AIR,)), Probe((1, -3, 0), STONE, (AIR,)),
          Probe((-2, -5, 1), STONE, (AIR,)))


@dataclass(frozen=True)
class Ability:
    title: str
    seconds: float
    moments: tuple[float, ...]
    aim_height: int
    shader: int
    visible: str
    terrain: str
    probes: tuple[Probe, ...] = CENTER
    changed_count: int = 2


# Seconds since firing, sampled during sustained parts of the effects. See upstream
# docs/HOW-IT-WORKS.md and mod/src/{lasers,strike}.rs at SOURCE. Shader IDs are the
# post-pass IDs in the authored network_pass mapping, not wheel indexes.
ABILITIES = {
    "plasma": Ability("Plasma", 26, (4.7, 8, 15), 28, 0, "cyan beam and branching lightning",
                      "fused obsidian, glass and magma crater"),
    "godly": Ability("Godly", 26, (5, 9, 15), 36, 0, "golden pillar, halo rings and wings",
                      "quartz and gold crater, pillars and flowers"),
    "blackhole": Ability("Black Hole", 26, (6, 11, 17), 32, 0, "violet accretion disk and distorted sky",
                          "bedrock bore and sculk spiral"),
    "meteor": Ability("Meteor Storm", 38, (8, 18, 28), 42, 0, "meteor trails, then large fiery impact",
                       "multiple pits and the central crater"),
    "mothership": Ability("Mothership", 34, (4, 11, 20), 139, 0, "ship and sweeping green beam",
                            "bore and molten trench"),
    "downfall": Ability("Downfall", 64, (9, 32, 50), 52, 1, "falling ship and black hole at the wreck",
                          "buried hull trench and singularity bore"),
    "planetkiller": Ability("Planet Killer", 30.5, (8, 17, 24), 103, 2, "lightning ring, central beam and fireball",
                              "deep central crater and radial fissures"),
    "crystalbloom": Ability("Crystal Bloom", 60, (15, 22.5, 32, 45), 78, 3, "rising spires and colored shards",
                              "geode crater, crystal remnants and shards"),
    "sinkhole": Ability("Sinkhole", 40, (6.8, 10, 14), 6, 4, "dust and collapsing slabs",
                          "deep open pit and rubble"),
    "payload": Ability("Payload", 70, (8.5, 15, 25), 45, 5, "falling charge, smoke and dust front",
                         "terraced pit and scorched ground"),
    "nuke": Ability("Nuke", 90, (6, 13, 25), 58, 6, "falling bomb and mushroom cloud",
                      "blast crater and scorched ground"),
    "rod": Ability("Rod from God", 60, (5.4, 9, 15), 40, 7, "golden fall, blast and standing rod",
                     "rod raised above a quartz and gold crater",
                     (Probe((0, 12, 0), AIR, ("minecraft:polished_blackstone", "minecraft:gold_block",
                                               "minecraft:chiseled_polished_blackstone")),
                      Probe((12, 0, 0), GOLD, (AIR,)), Probe((-12, -3, 0), STONE, (AIR,)))),
    "supercell": Ability("Supercell", 42, (8, 19, 29.5), 28, 8, "storm cloud, funnels and lightning finale",
                           "small lightning pits and fused central crater"),
    "hurricane": Ability("Hurricane", 64, (14, 24, 37), 43, 9, "spiral rain bands and rotating eyewall",
                           "top blocks and cover torn from the eyewall band",
                           tuple(Probe((x, 0, z), GRASS)
                                 for x, z in ((64, 0), (-64, 0), (0, 64), (0, -64), (85, 0), (-85, 0)))),
    "tsunami": Ability("Tsunami", 42, (7.2, 10, 14), 60, 12, "high wall of moving water and spray",
                        "scoured ground, debris and water deposits",
                        tuple(Probe((x, 0, z), GRASS, TSUNAMI_GROUND)
                              for x, z in ((4, 25), (-7, -25), (17, 25), (32, 12), (-21, -39), (-9, 24)))),
    "supervolcano": Ability("Supervolcano", 88, (6, 23, 38, 58), 188, 11,
                              "satellite beam, plume, volcanic lightning and ash front",
                              "shifted vent, caldera and volcanic deposits"),
    "skybreak": Ability("The Sky Breaks", 49.5, (7, 10.5, 18), 69, 13, "cracked sky, impact fist and falling panes",
                         "impact bowl and lodged panes"),
    "carpetbomb": Ability("Carpet Bomb", 37.5, (8.5, 15, 23), 28, 14, "bomb line and sequential blasts",
                            "overlapping craters along the line",
                            (*CENTER, Probe((0, 0, -22), GOLD), Probe((0, 0, 22), GOLD))),
    "worldeater": Ability("World Eater", 90, (1.5, 3, 8.5, 13, 21, 39), 77, 15, "wormsign, emerging worm and dive",
                            "first breach hole and collapsed tunnels"),
    "convergence": Ability("Convergence", 60, (8, 14, 27.5), 65, 16, "opposing light columns and their collision",
                             "two-tone Nether and pale crater, fissures and islands"),
    "sixstars": Ability("The Six Stars", 58, (4.7, 10, 15, 19.5), 72, 18,
                        "wanted stars, falling star, and impact cloud",
                        "star-shaped crater and the five radial trenches"),
}


def coordinates(point):
    return " ".join(map(str, point))


def translated(point, offset):
    return tuple(a + b for a, b in zip(point, offset, strict=True))


def client_state(client):
    if client.log.stat().st_size > 16 * 1024 * 1024:
        raise ValueError(f"{client.root.name} log exceeded the 16 MiB QA limit")
    return client("state", timeout=20)


def wait_until_tick(client, tick, timeout, evidence):
    wait = evidence["tickWait"] = {"target": tick, "lastTick": None, "screen": None}
    changed_at = time.monotonic()

    def reached():
        nonlocal changed_at
        state = client_state(client)
        ticks = state["ticks"]
        now = time.monotonic()
        if ticks != wait["lastTick"]:
            changed_at = now
        wait.update(lastTick=ticks, screen=state.get("screen"), secondsWithoutAdvance=round(now - changed_at, 2))
        if wait["screen"] in {"title", "disconnected"}:
            raise RuntimeError(f"World closed at tick {ticks} on screen {wait['screen']!r}, waiting for {tick}")
        return ticks if ticks >= tick else None

    try:
        return until(reached, f"World did not reach tick {tick}", timeout)
    except TimeoutError as error:
        raise TimeoutError(f"{error}; last tick {wait['lastTick']} on screen {wait['screen']!r}") from error


def client_failure_status(client):
    process = client.process
    result = {"pid": process.pid, "exitCodeBeforeStatus": process.poll()}
    if result["exitCodeBeforeStatus"] is None:
        try:
            status = client("status", timeout=2)
            result.update({name: status.get(name) for name in ("screen", "ticks", "frames", "fps")})
            renderer = status.get("renderer") or {}
            result["renderer"] = {name: renderer.get(name) for name in
                                  ("backend", "size", "resolution_scale", "drawn_sections")}
        except (OSError, RuntimeError, ValueError, subprocess.SubprocessError) as error:
            result["statusError"] = f"{type(error).__name__}: {error}"
    result["exitCodeAfterStatus"] = process.poll()
    return result


def capture_failure_status(clients):
    with ThreadPoolExecutor(max_workers=len(clients)) as executor:
        requests = {role: executor.submit(client_failure_status, client) for role, client in clients.items()}
        return {role: request.result() for role, request in requests.items()}


def marker_reads(client, points):
    values = [client("block", *point, timeout=25) for point in points]
    return [value["block"] for value in values] if all(value["loaded"] for value in values) else None


def client_log(client, offset=0):
    with client.log.open("rb") as stream:
        stream.seek(offset)
        return stream.read().decode("utf-8", errors="replace")


def strike_event(actor, offset, key, expected):
    def pending():
        strikes = orbital.STRIKE.findall(client_log(actor, offset))
        if not strikes:
            return None
        actual = [(name, tuple(map(int, point))) for name, *point in strikes]
        if actual != [(key, expected)]:
            raise ValueError(f"Designator fired {actual!r}, expected exactly {(key, expected)!r}")
        return {"ability": key, "block": list(expected)}

    return until(pending, f"No Designator strike logged for {key} at {expected}", 12)


def move_clients(host, clients, position, *, observation=False):
    x, y, z = position
    for role, client in clients.items():
        offset_x = 0 if role == "actor" else 10
        if client is host:
            host("tp", x + offset_x, y, z)
        else:
            host("command", f"tp {GUEST} {x + offset_x:g} {y:g} {z:g}")
    for client in clients.values():
        client("wait-for", "loaded", "--timeout", "90")
    if observation:
        orbital.wait_for_world_ticks(clients["actor"], 12)


def look(client, point, height):
    x, y, z = point
    client("look-at", x + 0.5, y + height, z + 0.5)


def terrain_fixture(host, clients, key, ability):
    x, y, z = map(floor, host("state")["player"]["position"])
    y = min(y + 5, 252)
    distance = 96 if key in ("plasma", "godly", "blackhole", "meteor") else 112
    target = (x, y, z)
    camera = (x + 0.5, y + 38, z + distance + 0.5)
    # The author displaces the vent along the horizontal aiming ray, even
    # though the Designator must still strike a real loaded block nearby.
    strike = (x, y, z + distance - 1000) if key == "supervolcano" else target
    # Downfall does not move the recorded hit. Its authored crater is centered
    # at the ship's stern, 360 blocks ahead of our stationary Designator.
    site = (x, y, z + distance - 360) if key == "downfall" else strike
    if site != target:
        move_clients(host, clients, (site[0] + 0.5, y + 32, site[2] + 0.5))

    sx, sy, sz = site
    points = [translated(site, probe.offset) for probe in ability.probes]
    commands = [f"fill {sx - 4} {sy - 6} {sz - 4} {sx + 4} {sy - 1} {sz + 4} minecraft:stone"]
    for point, probe in zip(points, ability.probes, strict=True):
        if probe.before == GRASS:
            px, py, pz = point
            # Both effects survey the top of each column. Keep these marked
            # surfaces exposed even on an unusually high or wooded spawn.
            commands.extend((f"fill {px} {py + 1} {pz} {px} 319 {pz} minecraft:air",
                             f"fill {px} {py - 3} {pz} {px} {py - 1} {pz} minecraft:dirt"))
        commands.append(f"setblock {coordinates(point)} {probe.before}")
    expected = [probe.before for probe in ability.probes]

    def prepare_markers():
        if marker_reads(host, points) == expected:
            return True
        # A teleport may report loaded while chunks at the new site are still
        # entering the server's view. Retry the same writes until they appear.
        for command in commands:
            host("command", command)
        orbital.wait_for_world_ticks(host, 10)
        return False

    try:
        for command in commands:
            host("command", command)
        until(prepare_markers, "Fixture blocks were not prepared", 60)
    except TimeoutError as error:
        raise FixtureError(f"Expected {expected!r} at {points!r}, found {marker_reads(host, points)!r}") from error

    move_clients(host, clients, camera)
    cy, cz = floor(camera[1]), floor(camera[2])
    host("command", f"fill {x - 2} {cy - 1} {cz - 2} {x + 12} {cy - 1} {cz + 2} minecraft:smooth_stone")
    host("command", f"fill {x - 2} {cy} {cz - 2} {x + 12} {cy + 3} {cz + 2} minecraft:air")
    host("command", f"fill {x} {y + 1} {z} {x} {cy + 2} {cz - 1} minecraft:air")
    host("command", f"fill {x} {y} {z + 1} {x} {y} {z + 3} minecraft:air")
    host("command", f"setblock {coordinates(target)} {GOLD}")
    move_clients(host, clients, camera, observation=True)
    return target, strike, site, camera, points


def region(image, *, top=0.12):
    width, height = image.size
    return image.crop((round(width * 0.07), round(height * top),
                       round(width * 0.92), round(height * 0.76))).resize((320, 180), Image.Resampling.BOX)


def pixel_change(before, after):
    red, green, blue = ImageChops.difference(before, after).split()
    peak = ImageChops.lighter(ImageChops.lighter(red, green), blue)
    return sum(peak.histogram()[26:]) / (peak.width * peak.height)


def capture(client, name, *, keep_png, top):
    frame = orbital.photograph(client, client.root, name)
    path = client.root / frame["file"]
    try:
        with Image.open(path) as source:
            image = source.convert("RGB")
            pixels = region(image, top=top)
            if not keep_png:
                image.thumbnail((768, 432), Image.Resampling.LANCZOS)
                retained = path.with_suffix(".jpg")
                image.save(retained, quality=78, optimize=True)
                frame["retained"] = {"file": retained.name, "sha256": sha(retained), "size": list(image.size)}
        return frame, pixels
    finally:
        if not keep_png:
            path.unlink(missing_ok=True)


def capture_both(executor, clients, name, options):
    jobs = {role: executor.submit(capture, client, name, **options) for role, client in clients.items()}
    return {role: job.result() for role, job in jobs.items()}


def use_command(actor, host, text, expected=None):
    if actor is host:
        if expected is None:
            actor("command", text)
        else:
            orbital.command(actor, text, expected)
    else:
        actor.typed_command(text)
        if expected is not None:
            orbital.wait_client_message(actor, expected)


def verify_terrain(host, clients, ability, mode, points, before, deadline):
    expected = [probe.before for probe in ability.probes]
    actual = {}
    changed = []
    previous = None

    def observed():
        nonlocal previous
        for role, client in clients.items():
            actual[role] = marker_reads(client, points)
        if any(values is None for values in actual.values()) or any(
                values != actual["actor"] for values in actual.values()):
            previous = None
            return False
        stable = previous == actual["actor"]
        previous = actual["actor"]
        if mode == "off":
            if actual["actor"] != expected:
                raise ValueError(f"Terrain off changed fixture blocks: {actual!r}, expected {expected!r}")
            return stable
        changed[:] = [index for index, (probe, now) in enumerate(zip(ability.probes, actual["actor"], strict=True))
                      if now != before[index] and (not probe.after or now in probe.after)]
        return stable and len(changed) >= ability.changed_count

    try:
        until(observed, "Actor and observer did not agree on the expected terrain change", deadline)
    except TimeoutError as error:
        raise TimeoutError(f"{error}: before={before!r}, after={actual!r}, matching={changed!r}") from error
    for point, block in zip(points, actual["actor"], strict=True):
        orbital.command(host, f"execute if block {coordinates(point)} {block}", "Test passed")
    return {"positions": points, "before": before, "after": actual, "serverBlockChecks": len(points),
            "stableAgreementSamples": 2,
            "allowedAfter": [probe.after for probe in ability.probes],
            "matchingChangedIndices": changed,
            "minimumChanged": ability.changed_count if mode == "on" else 0}


class FixtureError(ValueError):
    """The physical QA setup cannot support the requested observation."""


def stabilize_baseline(executor, clients, mode, options, evidence):
    actor = clients["actor"]
    for attempt in range(1, 6):
        reference = capture_both(executor, clients, f"{mode}-baseline", options)
        orbital.wait_for_world_ticks(actor, 24)
        idle = capture_both(executor, clients, f"{mode}-idle", options)
        changes = {role: pixel_change(reference[role][1], idle[role][1]) for role in clients}
        evidence["baseline"] = {role: {"frame": reference[role][0], "idleFrame": idle[role][0],
                                       "idleChange": changes[role], "attempt": attempt} for role in clients}
        if all(change <= 0.06 for change in changes.values()):
            return reference, idle, changes
        orbital.wait_for_world_ticks(actor, 24)
    raise FixtureError(f"Stationary scene is still changing before the strike: {changes!r}")


def wait_for_tsunami_tail(actor, offset, timeout, evidence):
    tail = evidence["terrainTail"] = {"status": "WAITING"}

    def completed():
        lines = [line.strip() for line in client_log(actor, offset).splitlines() if "[mod orbital] tsunami:" in line]
        if any("deadline" in line for line in lines):
            tail.update(status="FAIL", logs=lines[-8:])
            raise ValueError(f"Tsunami reached its terrain deadline: {lines[-8:]!r}")
        if any("terrain edits submitted; released chunk tickets" in line for line in lines):
            authored = next((line for line in lines if "chunks changed" in line and "columns swept" in line), None)
            if authored is None:
                raise ValueError("Tsunami released its temporary chunks without a completed terrain plan")
            tail.update(status="PASS", logs=lines[-8:])
            return True
        state = client_state(actor)
        tail.update(lastTick=state["ticks"], screen=state.get("screen"))
        if tail["screen"] in {"title", "disconnected"}:
            raise RuntimeError(f"World closed before Tsunami finished its terrain tail at tick {tail['lastTick']}")
        return False

    until(completed, "Tsunami did not finish its terrain tail naturally", timeout)


def cast(executor, host, clients, key, ability, mode, fixture, args, evidence):
    actor = clients["actor"]
    target, strike, site, camera, points = fixture
    move_clients(host, clients, camera, observation=True)
    use_command(actor, host, f"/orbital terrain {mode}", f"Terrain damage {mode}")
    title = "SIX STARS" if key == "sixstars" else ability.title.upper()
    use_command(actor, host, f"/orbital {key}", f"{title} loaded")
    evidence["expectedVisual"] = ability.visible
    evidence["expectedShader"] = ability.shader
    evidence["expectedTerrain"] = ability.terrain
    evidence["fixture"] = {"aimBlock": target, "strikeBlock": strike, "terrainCenter": site,
                           "cameraPosition": camera, "probes": points}
    evidence["phase"] = "baseline"
    options = {"keep_png": args.keep_frames, "top": 0.025 if key == "sixstars" else 0.12}
    for client in clients.values():
        look(client, site, ability.aim_height)
    orbital.wait_for_world_ticks(actor, 24)
    reference, idle, idle_change = stabilize_baseline(executor, clients, mode, options, evidence)

    evidence["phase"] = "designator"
    offsets = {role: client.log.stat().st_size for role, client in clients.items()}
    orbital.camera(actor, target)
    orbital.hold_designator(actor)
    evidence["strike"] = strike_event(actor, offsets["actor"], key, strike)
    # Designator holds for 20 world ticks, acquiring the target at tick 14.
    start_tick = client_state(actor)["ticks"] - 6
    evidence["estimatedStartTick"] = start_tick
    look(actor, site, ability.aim_height)
    evidence["frames"] = []
    evidence["phase"] = "render"
    for moment in ability.moments:
        evidence["pendingFrameSeconds"] = moment
        tick = wait_until_tick(actor, start_tick + round(moment * 20), args.tick_timeout, evidence)
        frames = capture_both(executor, clients, f"{mode}-{moment:g}s", options)
        evidence["frames"].append({"requestedSeconds": moment, "observedSeconds": (tick - start_tick) / 20,
                                   "diagnostic": key == "worldeater" and moment < 8.5,
                                   "clients": {role: {"frame": frame,
                                                      "pixelChange": min(pixel_change(reference[role][1], image),
                                                                         pixel_change(idle[role][1], image))}
                                               for role, (frame, image) in frames.items()}})
    evidence.pop("pendingFrameSeconds")

    # Let the complete authored strike and its queued edits run before taking
    # final evidence, especially for distant terrain and the terrain-off control.
    evidence["phase"] = "strike completion"
    last_tick = wait_until_tick(actor, start_tick + round((ability.seconds + 3) * 20), args.tick_timeout, evidence)
    evidence["observedStrikeSeconds"] = (last_tick - start_tick) / 20

    evidence["phase"] = "render verification"
    evidence["render"] = {}
    errors = []
    for role, client in clients.items():
        tail = client_log(client, offsets[role])
        shader_errors = [line for line in tail.splitlines() if RENDER_ERROR.search(line)][:12]
        changed = max(frame["clients"][role]["pixelChange"] for frame in evidence["frames"] if not frame["diagnostic"])
        threshold = max(0.012, idle_change[role] * 2 + 0.005)
        evidence["render"][role] = {"peakPixelChange": changed, "threshold": threshold,
                                    "shaderErrors": shader_errors}
        if role == "observer":
            remote = sorted({(int(shader), int(generation)) for shader, generation in REMOTE.findall(tail)})
            evidence["render"][role]["remoteShaderGenerations"] = remote
            evidence["render"][role]["remoteVisualCleared"] = "remote visual cleared" in tail
            if not any(shader == ability.shader for shader, _ in remote):
                errors.append(f"Observer did not apply the {ability.title} post pass: {remote!r}")
        else:
            evidence["render"][role]["endOfStrikeLogged"] = "memory: strike's peak" in tail
        if shader_errors or changed < threshold:
            errors.append(f"{role} lacks visible clean {ability.title} evidence: {evidence['render'][role]!r}")
    evidence["renderErrors"] = errors

    natural_tail = key == "tsunami" and mode == "on"
    if natural_tail:
        evidence["phase"] = "terrain tail"
        wait_for_tsunami_tail(actor, offsets["actor"], args.tick_timeout, evidence)
    evidence["phase"] = "terrain"
    expected = [probe.before for probe in ability.probes]
    if site != target:
        x, y, z = site
        move_clients(host, clients, (x + 0.5, y + 92, z + 24.5), observation=True)
    evidence["terrain"] = verify_terrain(host, clients, ability, mode, points, expected, args.terrain_timeout)
    # Reset residual visual settings only after sampling, so this QA cannot
    # manufacture its own evidence of natural strike completion.
    if not natural_tail:
        use_command(actor, host, "/orbital stop")
        orbital.wait_for_world_ticks(actor, 12)
    evidence["phase"] = "complete"
    evidence["status"] = "PASS" if not errors else "FAIL"


def run_session(args, root, key, caster, result):
    ability = ABILITIES[key]
    other = "guest" if caster == "host" else "host"
    roots = {caster: root / "actor"}
    if not args.actor_only:
        roots[other] = root / "observer"
    try:
        with ExitStack() as contexts, ThreadPoolExecutor(max_workers=len(roots)) as executor:
            named_clients = {}
            for role in sorted(roots, key=lambda name: name != "host"):
                path = roots[role]
                client = Client(args.candidate, args.controller, args.resources, path, HOST if role == "host" else GUEST)
                if args.resolution_scale is not None:
                    (client.game / "horizon-graphics.txt").write_text(
                        f"resolutionScale:{args.resolution_scale}\ndynamicResolution:false\n",
                        encoding="utf-8", newline="\n")
                named_clients[role] = contexts.enter_context(client)
            host, guest = named_clients["host"], named_clients.get("guest")
            host("open", f"Orbital-{key}", str(SEED), "creative")
            host("wait-for", "loaded", "--timeout", "90")
            orbital.wait_for_world_ticks(host, 8)
            if guest is not None:
                join(guest, open_from_menu(host)["port"])
            if caster == "guest":
                result["guestPermissions"] = guest_modes(guest)
            actor = named_clients[caster]
            clients = {"actor": actor}
            if other in named_clients:
                clients["observer"] = named_clients[other]
            result["renderer"] = {role: client("status")["renderer"] for role, client in clients.items()}
            if any(not row or not row["backend"] for row in result["renderer"].values()):
                raise ValueError("All clients must use the real renderer")
            if args.resolution_scale is not None and any(
                    row.get("resolution_scale") != args.resolution_scale or row.get("dynamic_resolution") is not False
                    for row in result["renderer"].values()):
                raise FixtureError(f"The renderer did not apply static {args.resolution_scale}% world scale")
            orbital.check_render_mod_switches(actor.game)
            use_command(actor, host, "/orbital give", "Orbital Laser Designator added to your inventory")
            orbital.item_present(actor)
            actor("select", orbital.DESIGNATOR)
            for command in ("gamerule minecraft:advance_time false", "gamerule minecraft:advance_weather false",
                            "gamerule minecraft:spawn_mobs false", "time set noon", "weather clear"):
                host("command", command)
            use_command(actor, host, f"/orbital seed {DESIGNATOR_SEED}", f"Seed: held at {DESIGNATOR_SEED}")
            use_command(actor, host, "/orbital knockback off", "Knockback off")
            use_command(actor, host, "/orbital impactframes off")
            for client in clients.values():
                client("key", "key.keyboard.f1", "tap")
            fixture = terrain_fixture(host, clients, key, ability)
            for mode in ("off", "on") if args.terrain == "both" else (args.terrain,):
                evidence = result["terrainModes"][mode] = {"status": "FAIL"}
                try:
                    cast(executor, host, clients, key, ability, mode, fixture, args, evidence)
                except (AssertionError, OSError, RuntimeError, ValueError, subprocess.SubprocessError):
                    evidence["failureBeforeTeardown"] = capture_failure_status(clients)
                    raise
                if evidence["status"] != "PASS":
                    print(f"{key}/{caster}/{mode}: {'; '.join(evidence['renderErrors'])}", flush=True)
                    if args.fail_fast:
                        break
            if all(row["status"] == "PASS" for row in result["terrainModes"].values()):
                result["status"] = "PASS"
    finally:
        for role, path in roots.items():
            game = path / "game"
            try:
                cleanup_game(game, root, args.resources)
            except (OSError, RuntimeError) as error:
                result.setdefault("cleanupErrors", {})[role] = str(error)
        result["disposableWorldsRemoved"] = not result.get("cleanupErrors")
        if not result["disposableWorldsRemoved"]:
            result["status"] = "FAIL"


def run_ability(args, root, key, result):
    casters = ("host", "guest") if args.cast_from == "both" else (args.cast_from,)
    result["casts"] = {caster: {"status": "NOT RUN"} for caster in casters}
    for caster in casters:
        cast_result = result["casts"][caster] = {"status": "FAIL", "terrainModes": {}}
        try:
            run_session(args, root / caster, key, caster, cast_result)
        except FixtureError as error:
            cast_result["status"] = "FIXTURE ERROR"
            cast_result["error"] = str(error)
        except (AssertionError, OSError, RuntimeError, ValueError, subprocess.SubprocessError) as error:
            cast_result["status"] = "FAIL"
            cast_result["error"] = f"{type(error).__name__}: {error}"
        print(f"{key}/{caster}: {cast_result['status']} {cast_result.get('error', '')}".rstrip(), flush=True)
        if cast_result.get("cleanupErrors") or (args.fail_fast and cast_result["status"] != "PASS"):
            break
    result["status"] = "PASS" if all(row["status"] == "PASS" for row in result["casts"].values()) else "FAIL"


def update_report(path, report):
    pending = path.with_suffix(".partial")
    try:
        write(pending, report)
        pending.replace(path)
    finally:
        pending.unlink(missing_ok=True)


def run(args):
    if args.actor_only and args.cast_from != "host":
        raise ValueError("--actor-only requires --cast-from host")
    if args.resolution_scale is not None and not 50 <= args.resolution_scale <= 200:
        raise ValueError("--resolution-scale must be a Horizon graphics percentage in 50..200")
    for field in ("candidate", "controller", "resources"):
        setattr(args, field, getattr(args, field).resolve(strict=True))
    qa = capture_root(args.qa)
    client_sha = sha(args.candidate / "native/horizon-client.exe")
    receipt = load(args.candidate / "build-receipt.json")
    if client_sha != args.client_sha256.lower() or receipt["clientSha256"] != client_sha:
        raise ValueError("Client SHA-256 does not match its build receipt and expected tested build")
    pin = load(Path(__file__).with_name("orbital_compat") / "upstream-0.5.17.json")
    asset = args.candidate / "native/horizon-mods/orbital.hmod"
    pack = pin["contentCompanion"]
    if "orbital" not in receipt["ids"] or asset.stat().st_size != pack["size"] or sha(asset) != pack["sha256"]:
        raise ValueError("Client does not contain the pinned Orbital Laser build and content companion")
    keys = list(dict.fromkeys(args.ability or ABILITIES))
    report = {"schemaVersion": 1, "clientSha256": client_sha, "controllerSha256": sha(args.controller),
              "horizonRevision": receipt["horizonRevision"], "orbitalVersion": "0.5.17", "authorSource": SOURCE,
              "seed": SEED, "designatorSeed": DESIGNATOR_SEED, "observer": not args.actor_only,
              "requested": keys, "terrainModes": args.terrain, "castFrom": args.cast_from,
              "requestedResolutionScale": args.resolution_scale,
              "orbitalContentSha256": pack["sha256"], "unavailableNative": WIP,
              "result": "FAIL", "abilities": {key: {"status": "NOT RUN"} for key in keys}}
    qa.mkdir(parents=True)
    path = qa / "report.json"
    update_report(path, report)
    for key in keys:
        result = report["abilities"][key] = {"status": "FAIL", "title": ABILITIES[key].title}
        print(f"{key}: START", flush=True)
        try:
            run_ability(args, qa / key, key, result)
        except (AssertionError, OSError, RuntimeError, TimeoutError, ValueError) as error:
            result["status"] = "FAIL"
            result["error"] = f"{type(error).__name__}: {error}"
            print(f"{key}: {result['error']}", flush=True)
        finally:
            update_report(path, report)
        print(f"{key}: {result['status']}", flush=True)
        if result["status"] == "FAIL" and (args.fail_fast or any(
                row.get("cleanupErrors") for row in result.get("casts", {}).values())):
            break
    report["result"] = "PASS" if all(row["status"] == "PASS" for row in report["abilities"].values()) else "FAIL"
    update_report(path, report)
    print(f"{report['result']} · {path}", flush=True)
    return 0 if report["result"] == "PASS" else 1


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for field in ("candidate", "controller", "resources", "qa"):
        parser.add_argument(f"--{field}", required=True, type=Path)
    parser.add_argument("--client-sha256", required=True)
    parser.add_argument("--ability", action="append", choices=ABILITIES, help="repeat for a subset, default all")
    parser.add_argument("--terrain", choices=("both", "off", "on"), default="both")
    parser.add_argument("--cast-from", choices=("host", "guest", "both"), default="host",
                        help="exercise the host, the authorized guest, or both in separate disposable worlds")
    parser.add_argument("--actor-only", action="store_true", help="omit the local TCP observer for diagnostics")
    parser.add_argument("--resolution-scale", type=int, help="fixed 50..200%% world scale in this QA profile")
    parser.add_argument("--keep-frames", action="store_true", help="retain full PNGs instead of compact JPEG proofs")
    parser.add_argument("--fail-fast", action="store_true")
    parser.add_argument("--tick-timeout", type=int, default=240)
    parser.add_argument("--terrain-timeout", type=int, default=100)
    return run(parser.parse_args())


if __name__ == "__main__":
    raise SystemExit(main())
