"""Use Horizon f00125f's public camera and sound-instance APIs."""


def route_source(text: str) -> str:
    # The camera enum moved to the public vocabulary without changing its states.
    text = text.replace("crate::game::perspective::CameraType", "horizon_client::camera::CameraType")
    text = text.replace(".camera_type.first_person()", ".camera_type.is_first_person()")
    text = text.replace("super::native_hooks::NativeShake", "mod_api::game::Shake")

    # Only the unseeded random factory moved. Keep using the game's live engine for the loop.
    text = text.replace("SoundEngine::unseeded()", "horizon_client::random_source::create()")
    return text.replace("Attenuation, NEUTRAL, SoundEngine, SoundInstance, Sounds, Ticking,",
                        "Attenuation, NEUTRAL, SoundInstance, Sounds, Ticking,")
