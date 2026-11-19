import tomllib
import unittest

from legacy_native_hooks import LEGACY_ROUTES, RENDER_ROUTES, route_manifest


class LegacyNativeRoutesTests(unittest.TestCase):
    def test_named_route_keeps_helper_and_bindings(self):
        pinned = '''[[injection]]
id = "client-tick"
kind = "HEAD"
hook = "tick"
hook_version = 1
helper = "tick.rs"
bindings = [{ name = "game", ty = "&mut crate::game::Game<'_>", value = "&mut *self" }]
'''
        expected = tomllib.loads(pinned)["injection"][0]
        del expected["hook"], expected["hook_version"]
        expected["target"] = LEGACY_ROUTES["tick"]
        self.assertEqual(tomllib.loads(route_manifest(pinned))["injection"], [expected])

    def test_unported_hook_contract_stays_visible_to_mixin_validator(self):
        pinned = '''[[injection]]
id = "frame"
kind = "HEAD"
hook = "frame"
hook_version = 2
helper = "frame.rs"
'''
        self.assertEqual(route_manifest(pinned), pinned)

    def test_native_renderer_uses_the_current_slot_wrapper(self):
        pinned = '''[[module]]
id = "bridge"
file = "crates/horizon-client-render/src/gfx/native_render.rs"
name = "render_bridge"
source = "bridge.rs"
[[injection]]
id = "draw"
kind = "HEAD"
hook = "world_drawn"
hook_version = 1
helper = "draw.rs"
bindings = [{ name = "world", ty = "&mut crate::gfx::native_render::WorldDrawn<'_>", value = "world" }]
'''
        routed = tomllib.loads(route_manifest(pinned))
        self.assertEqual(routed["module"][0]["file"], "crates/horizon-client-render/src/render_slots.rs")
        injection = routed["injection"][0]
        self.assertEqual(injection["target"], RENDER_ROUTES["world_drawn"])
        self.assertEqual(injection["bindings"][0]["ty"], "&mut crate::render_slots::WorldDrawn<'_>")
