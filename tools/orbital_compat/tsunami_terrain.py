"""Keep Orbital 0.5.17's authored Tsunami terrain work alive after the visual pass."""

import hashlib
from pathlib import Path

from . import replace


AUTHOR_TERRAIN = "fc099655f80188623c4cfe7888608774945b3861f08bb5289f791f7cd1f9817c"


def transform_terrain(author: bytes) -> str:
    if hashlib.sha256(author).hexdigest() != AUTHOR_TERRAIN:
        raise ValueError("Unexpected Orbital Laser 0.5.17 Tsunami terrain source")
    source = author.decode("utf-8")
    for before, after in (
        ("const TS_LAND_BY: f64 = 38.0;", """// Allow several ordered terrain-read batches to finish over a slow server.
const TS_LAND_BY: f64 = 150.0;
const TS_PLAN_END: f64 = TS_LAND_BY + 6.0;
pub(crate) const TS_TERRAIN_STOP: f32 = (TS_PLAN_END + 6.0) as f32;"""),
        ("fn ts_work(q: &mut Plan, w: Rc<RefCell<Work>>, now: f64) {",
         "fn ts_work(q: &mut Plan, w: Rc<RefCell<Work>>, now: f64) {\n    let now = now.max(f64::from(super::clock()));"),
        ("while wk.asked < wk.tags.len() && wk.asked < wk.taken + 3 {",
         "while now < TS_LAND_BY && wk.asked < wk.tags.len() && wk.asked < wk.taken + 3 {"),
        ("if (read || now > TS_LAND_BY) && !wk.tiles.as_ref().is_some_and(Vec::is_empty) && crate::meter::spent() < TS_STAGE {",
         "if read && !wk.tiles.as_ref().is_some_and(Vec::is_empty) && crate::meter::spent() < TS_STAGE {"),
        ("        !wk.done && now < TS_LAND_BY + 6.0",
         """        if !wk.done && now >= TS_PLAN_END {
            crate::sdk::log::write(crate::sdk::log::Level::Warn, &format!(
                "tsunami: terrain planning deadline, {} of {} column groups received, {} chunks changed",
                wk.taken, wk.tags.len(), wk.changed));
        }
        !wk.done && now < TS_PLAN_END"""),
        ("    let len = laser.length;\n", ""),
        ("    // ---- the land: forceloaded and read",
         "    p.ev.retain(|(time, _)| *time <= TS_LEN);\n\n    // ---- the land: forceloaded and read"),
        ("p.ev.push((len - 0.5, Act::Spare(format!(\"forceload remove {x0} {z0} {x1} {z1}\"))));",
         """// Keep the release rows in the plan for manual stop and world recovery.
            // Normal completion runs them after its remaining stages and edits.
            p.ev.push((TS_TERRAIN_STOP + 0.05, Act::Spare(format!("forceload remove {x0} {z0} {x1} {z1}"))));"""),
        ("        let played = sim::play(plan, 42.0, &|x, z, b, a| land(x, z, b, a));",
         """        let adds = plan.iter().filter(|(_, a)| matches!(a, Act::Cmd(c) if c.starts_with("forceload add"))).count();
        let removes = plan.iter().filter(|(t, a)| *t > TS_TERRAIN_STOP
            && matches!(a, Act::Spare(c) if c.starts_with("forceload remove"))).count();
        assert!(adds > 0);
        assert_eq!(adds, removes);
        let played = sim::play(plan, 42.0, &|x, z, b, a| land(x, z, b, a));"""),
        ("""        // (the chunks it forceloaded are let go before the strike ends)
        let adds = played.cmds.iter().filter(|(_, c)| c.starts_with("forceload add")).count();
        let removes = played.cmds.iter().filter(|(t, c)| c.starts_with("forceload remove") && *t < TS_LEN).count();
        assert_eq!(adds, removes);
""", ""),
    ):
        source = replace(source, before, after)
    return source


def transform_strike(source: str) -> str:
    return replace(source, "pub(crate) use tsunami::{TS_DRAIN, TS_HOLD, TS_LEN, TsGeo};",
                   "pub(crate) use tsunami::{TS_DRAIN, TS_HOLD, TS_LEN, TS_TERRAIN_STOP, TsGeo};")


def transform_engine(source: str) -> str:
    for before, after in (
        ("    length: f32,\n    /// What the post pass reads;",
         "    length: f32,\n    /// Terrain-on Tsunami only: whether its post pass has already ended.\n"
         "    tsunami_visual_ended: Option<bool>,\n    /// What the post pass reads;"),
        ("        self.strike = Some(Strike { plan, next, more: Default::default(), given: 0, ticks, length, params,",
         "        let tsunami_visual_ended = (laser.key == \"tsunami\" && self.terrain).then_some(false);\n"
         "        self.strike = Some(Strike { plan, next, more: Default::default(), given: 0, ticks, length, tsunami_visual_ended, params,"),
        ("        self.hold_forceloads(&dimension, clock, length + wait as f32 * 0.05);",
         "        let ticket_lifetime = if tsunami_visual_ended.is_some() { strike::TS_TERRAIN_STOP } else { length };\n"
         "        self.hold_forceloads(&dimension, clock, ticket_lifetime + wait as f32 * 0.05);"),
        ("""        if now >= s.length {
            self.end_strike();
        }
""", """        if now >= s.length {
            if let Some(visual_ended) = &mut s.tsunami_visual_ended {
                if !*visual_ended {
                    *visual_ended = true;
                    render::clear_post();
                    self.surging = None;
                }
                let drained = s.more.is_empty() && s.waiting.is_empty() && s.urgent.is_empty()
                    && self.edits.is_empty() && self.carving.is_empty() && self.spare.is_empty();
                if drained || now >= strike::TS_TERRAIN_STOP {
                    self.finish_strike(if drained { "tsunami: terrain edits submitted; released chunk tickets" }
                                       else { "tsunami: terrain work deadline; released chunk tickets" });
                }
            } else {
                self.end_strike();
            }
        }
"""),
        ("    fn stop_strike(&mut self) {\n        let Some(s)",
         "    fn stop_strike(&mut self) {\n        self.finish_strike(\"strike stopped\");\n    }\n\n"
         "    fn finish_strike(&mut self, message: &str) {\n        let Some(s)"),
        ("        log::write(log::Level::Info, \"strike stopped\");", "        log::write(log::Level::Info, message);"),
        ("length: 1000.0, params: [0.0; 16]", "length: 1000.0, tsunami_visual_ended: None, params: [0.0; 16]"),
        ("                assert!(ticks < 1400, \"it never ended\");\n            }\n            let edits = fake::with(|w| w.edits);\n            eprintln!(\"chunks x{slow}: worst tick {:.2}M ({}), {edits} edits, {ticks} ticks\", s.worst.0 as f64 / 1e6, s.worst.1);\n            assert!(s.worst.0 <= CAP, \"a tick of {:.2}M: {}\", s.worst.0 as f64 / 1e6, s.worst.1);\n            if slow > 0 {\n                assert!(edits > 50, \"the land swept as it loaded ({edits} edits)\");",
         "                assert!(ticks <= (strike::TS_TERRAIN_STOP * 20.0) as u32, \"it never ended\");\n            }\n            let edits = fake::with(|w| w.edits);\n            eprintln!(\"chunks x{slow}: worst tick {:.2}M ({}), {edits} edits, {ticks} ticks\", s.worst.0 as f64 / 1e6, s.worst.1);\n            assert!(s.worst.0 <= CAP, \"a tick of {:.2}M: {}\", s.worst.0 as f64 / 1e6, s.worst.1);\n            if slow > 0 {\n                assert!(edits > 50, \"the land swept as it loaded ({edits} edits)\");"),
        ("    /// A Hurricane called in on land far off,", DELAYED_RESPONSE_TEST + "    /// A Hurricane called in on land far off,"),
    ):
        source = replace(source, before, after)
    return source


DELAYED_RESPONSE_TEST = '''    #[test]
    fn tsunami_finishes_late_terrain_in_order_or_cancels_it() {
        let laser = LASERS.iter().position(|l| l.key == "tsunami").unwrap();
        for cancel in [false, true] {
            let mut s = Soak::new(7);
            s.answers = Answers::Bunched(960);
            fake::with(|w| w.keep_chunks = true);
            s.fire(laser, s.home);
            for _ in 0..(43 * 20) {
                s.step("waiting past the visual end");
            }
            assert_eq!(s.o.strike.as_ref().unwrap().tsunami_visual_ended, Some(true));
            assert!(!s.loaded.forced.is_empty());
            assert_eq!(fake::with(|w| w.edits), 0);
            s.answers = Answers::Next;
            if cancel {
                s.stop();
                for _ in 0..140 {
                    s.step("discarding late replies after stop");
                }
                assert_eq!(fake::with(|w| w.edits), 0);
            } else {
                for _ in 0..(strike::TS_TERRAIN_STOP as usize * 20) {
                    if s.o.strike.is_none() { break; }
                    s.step("draining the authored wave over late terrain replies");
                }
                assert!(s.o.strike.is_none());
                let target_chunk = (s.home.0 >> 4, s.home.1 >> 4);
                assert!(fake::with(|w| w.chunks.contains_key(&target_chunk)));
                s.step("releasing the owned temporary chunks");
            }
            assert!(s.loaded.forced.is_empty());
        }
    }

'''


def apply(bundle: Path) -> None:
    src = bundle / "engine/src"
    terrain = src / "strike/tsunami.rs"
    strike = src / "strike.rs"
    engine = src / "lib.rs"
    sources = {terrain: transform_terrain(terrain.read_bytes()),
               strike: transform_strike(strike.read_text(encoding="utf-8")),
               engine: transform_engine(engine.read_text(encoding="utf-8"))}
    for path, content in sources.items():
        path.write_text(content, encoding="utf-8", newline="\n")
