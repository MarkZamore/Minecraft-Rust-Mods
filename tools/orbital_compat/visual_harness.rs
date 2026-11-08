//! Standalone endpoint regression harness; run with rustc --edition 2024 --test.
//! These mocks verify protocol/lifecycle, not the Windows client or Steam.
#![allow(dead_code)]
extern crate self as horizon_client_render;
extern crate self as orbital_laser;
extern crate self as log;
#[macro_export]
macro_rules! info { ($($a:tt)*) => { let _ = format_args!($($a)*); }; }
pub fn network_atlas_version() -> u64 { 1 }
pub fn network_repack(_: u8, _: &mut [u32]) {}
pub fn network_pass_index(_: &str) -> Option<u8> { Some(0) }
pub fn network_pass(index: u8) -> Option<(std::sync::Arc<str>, u64)> {
    (index < 10).then(|| (std::sync::Arc::from("known"), u64::from(index) + 1))
}
pub mod orbital_post {
    use std::{sync::Arc, cell::RefCell};
    #[derive(Clone)]
    pub struct Pass { pub source: Arc<str>, pub version: u64, pub params: [f32; 16],
        pub data: Option<(Arc<[u32]>, u64)>, pub day_time: u64 }
    thread_local! { static PASSES: RefCell<Vec<(u128, Pass)>> = const { RefCell::new(Vec::new()) }; }
    pub fn set_remote(passes: Vec<(u128, Pass)>) { PASSES.with(|p| *p.borrow_mut() = passes); }
    pub fn snapshot() -> Vec<(u128, Pass)> { PASSES.with(|p| p.borrow().clone()) }
}
    use std::marker::PhantomData;
    #[path = "visual_wire.rs"] pub mod orbital_visual_wire;
    #[path = "visual.rs"] pub mod orbital_visual;
    pub mod native_net { pub const PER_TICK: u32 = 32; }
    pub struct World { pub game_time: i64 }
    pub struct Clock { pub game_time: i64, pub day_time: i64 }
    #[derive(Clone)]
    pub struct Player { pub uuid: u128, pub dimension: String, pub permission: u8 }
    #[derive(Default)]
    pub struct Audio { pub started: Vec<String>, pub stopped: Vec<String> }
    impl Audio {
        pub fn play(&mut self, event: &str, _: Option<[f64; 3]>, _: f32, _: f32) { self.started.push(event.into()); }
        pub fn stop_sounds(&mut self, event: Option<&str>, _: Option<()>) { self.stopped.push(event.unwrap_or("").into()); }
    }
    pub struct Game<'a> { pub ticks: u64, pub world: World, pub audio: Audio, pub window: bool,
        pub players: Vec<Player>, pub sent: Vec<(u128, Vec<u8>)>, pub capacity: usize, pub marker: PhantomData<&'a ()> }
    impl Game<'_> {
        pub fn new(window: bool) -> Self { Self { ticks: 1, world: World { game_time: 40 },
            audio: Audio::default(), window, players: Vec::new(), sent: Vec::new(), capacity: usize::MAX, marker: PhantomData } }
        pub fn native_window(&self) -> bool { self.window }
        pub fn native_on(&self, _: &str) -> bool { true }
        pub fn native_player(&self) -> Option<()> { self.window.then_some(()) }
        pub fn native_clock(&self) -> Option<Clock> { Some(Clock { game_time: self.world.game_time, day_time: 1 }) }
        pub fn native_server_players(&self) -> Vec<Player> { self.players.clone() }
        pub fn native_net_send_to_server(&mut self, _: &str, data: &[u8]) -> Result<(), ()> { self.native_net_send_to_player(0, "", data) }
        pub fn native_net_send_to_player(&mut self, id: u128, _: &str, data: &[u8]) -> Result<(), ()> {
            if self.sent.len() >= self.capacity { Err(()) } else { self.sent.push((id, data.to_vec())); Ok(()) }
        }
    }

#[cfg(test)]
mod tests {
    use super::{Game, Player, native_net, orbital_visual as v, orbital_visual_wire::{self as w, Packet, Meta}, orbital_post};
    fn state(generation: u64) -> Vec<u8> {
        Packet::State(Meta { generation, shader: 0, params: [0.0; 16], revision: 1, words: 0, clock: 40 }).encode()
    }
    fn envelope(id: u128, data: Vec<u8>) -> Vec<u8> { let mut b = id.to_le_bytes().to_vec(); b.extend(data); b }
    fn players() -> Vec<Player> { vec![Player { uuid: 1, dimension: "overworld".into(), permission: 2 },
        Player { uuid: 2, dimension: "overworld".into(), permission: 0 }] }
    #[test] fn only_placed_operator_can_publish_and_uuid_is_server_owned() {
        let mut server = Game::new(false); server.players = players();
        assert!(v::from_client(&mut server, 2, w::CHANNEL, &state(1)));
        v::tick_server(&mut server, "overworld"); assert!(server.sent.is_empty());
        assert!(v::from_client(&mut server, 1, w::CHANNEL, &state(1)));
        v::tick_server(&mut server, "overworld");
        assert!(server.sent.iter().all(|(to, b)| *to == 2 && b[..16] == 1u128.to_le_bytes()));
        assert!(!server.sent.is_empty());
    }
    #[test] fn dimension_transition_clears_cached_effect_instead_of_replaying_in_destination() {
        let mut server = Game::new(false); server.players = players();
        v::from_client(&mut server, 1, w::CHANNEL, &state(1)); v::tick_server(&mut server, "overworld");
        server.sent.clear(); server.players.iter_mut().for_each(|p| p.dimension = "nether".into());
        v::tick_server(&mut server, "nether");
        assert!(server.sent.iter().all(|(_, b)| matches!(Packet::decode(&b[16..]), Some(Packet::State(m)) if m.shader == u8::MAX)));
    }
    #[test] fn concurrent_actors_survive_independent_clear_and_sound_stop() {
        let mut client = Game::new(true);
        v::from_server(&mut client, w::CHANNEL, &envelope(1, state(1)));
        v::from_server(&mut client, w::CHANNEL, &envelope(2, state(1)));
        v::refresh(&mut client); assert_eq!(orbital_post::snapshot().len(), 2);
        let sound = Packet::Sound { event: "orbital:blast".into(), at: None, volume: 1.0, pitch: 1.0 }.encode();
        v::from_server(&mut client, w::CHANNEL, &envelope(1, sound.clone()));
        v::from_server(&mut client, w::CHANNEL, &envelope(2, sound));
        v::from_server(&mut client, w::CHANNEL, &envelope(1, Packet::Stop("orbital:blast".into()).encode()));
        assert!(client.audio.stopped.is_empty());
        let mut clear = if let Some(Packet::State(m)) = Packet::decode(&state(2)) { m } else { unreachable!() }; clear.shader = u8::MAX;
        v::from_server(&mut client, w::CHANNEL, &envelope(1, Packet::State(clear).encode()));
        v::refresh(&mut client); let passes = orbital_post::snapshot(); assert_eq!(passes.len(), 1); assert_eq!(passes[0].0, 2);
    }
    #[test] fn reset_data_revision_on_next_strike_gets_new_gpu_upload_token() {
        let mut client = Game::new(true);
        let mut first = if let Some(Packet::State(m)) = Packet::decode(&state(1)) { m } else { unreachable!() }; first.words = 2;
        v::from_server(&mut client, w::CHANNEL, &envelope(1, Packet::State(first.clone()).encode()));
        v::from_server(&mut client, w::CHANNEL, &envelope(1, Packet::Data { generation: 1, revision: 1, offset: 0, words: vec![10, 11] }.encode()));
        v::refresh(&mut client);
        let a = orbital_post::snapshot()[0].1.data.as_ref().unwrap().1;
        first.generation = 2;
        v::from_server(&mut client, w::CHANNEL, &envelope(1, Packet::State(first).encode()));
        v::from_server(&mut client, w::CHANNEL, &envelope(1, Packet::Data { generation: 2, revision: 1, offset: 0, words: vec![20, 21] }.encode()));
        v::refresh(&mut client);
        let b = orbital_post::snapshot()[0].1.data.as_ref().unwrap().1; assert_ne!(a, b);
        assert_eq!(&*orbital_post::snapshot()[0].1.data.as_ref().unwrap().0, &[20, 21]);
    }
    #[test] fn forged_clock_does_not_allocate_or_relay_an_effect() {
        let mut server = Game::new(false); server.players = players();
        let mut m = if let Some(Packet::State(m)) = Packet::decode(&state(1)) { m } else { unreachable!() }; m.clock = i64::MIN;
        v::from_client(&mut server, 1, w::CHANNEL, &Packet::State(m).encode()); v::tick_server(&mut server, "overworld");
        assert!(server.sent.is_empty());
    }
    #[test] fn queued_sounds_do_not_follow_recipient_into_another_dimension() {
        let mut server = Game::new(false); server.players = players(); server.capacity = 0;
        let sound = Packet::Sound { event: "orbital:blast".into(), at: None, volume: 1.0, pitch: 1.0 }.encode();
        v::from_client(&mut server, 1, w::CHANNEL, &sound);
        server.players[1].dimension = "nether".into(); server.capacity = usize::MAX;
        v::tick_server(&mut server, "nether"); assert!(server.sent.is_empty());
    }
    #[test] fn backpressure_retries_metadata_before_data_without_advancing_cursor() {
        let mut server = Game::new(false); server.players = players(); server.capacity = 0;
        let mut meta = if let Some(Packet::State(m)) = Packet::decode(&state(1)) { m } else { unreachable!() }; meta.words = 2;
        v::from_client(&mut server, 1, w::CHANNEL, &Packet::State(meta).encode());
        v::from_client(&mut server, 1, w::CHANNEL, &Packet::Data { generation: 1, revision: 1, offset: 0, words: vec![3, 4] }.encode());
        v::tick_server(&mut server, "overworld"); assert!(server.sent.is_empty());
        server.capacity = 1; v::tick_server(&mut server, "overworld");
        assert!(matches!(Packet::decode(&server.sent[0].1[16..]), Some(Packet::State(_))));
        server.sent.clear(); server.capacity = 2; v::tick_server(&mut server, "overworld");
        assert!(matches!(Packet::decode(&server.sent[0].1[16..]), Some(Packet::Data { offset: 0, words, .. }) if words == vec![3, 4]));
    }
    #[test] fn invented_sound_names_cannot_grow_per_owner_tracking_without_bound() {
        let mut client = Game::new(true);
        for index in 0..512 {
            let sound = Packet::Sound { event: format!("orbital:s{index}"), at: None, volume: 1.0, pitch: 1.0 }.encode();
            v::from_server(&mut client, w::CHANNEL, &envelope(1, sound));
        }
        assert_eq!(client.audio.started.len(), 256);
        let sound = Packet::Sound { event: "orbital:another_owner".into(), at: None, volume: 1.0, pitch: 1.0 }.encode();
        v::from_server(&mut client, w::CHANNEL, &envelope(2, sound));
        assert_eq!(client.audio.started.len(), 257, "owner limits are independent");
    }
    #[test] fn continuous_work_answers_and_sounds_cannot_starve_metadata_or_maximum_snapshot() {
        use std::sync::Arc;
        let mut sender = Game::new(true);
        let mut server = Game::new(false); server.players = players();
        let mut observer = Game::new(true);
        sender.capacity = native_net::PER_TICK as usize;
        server.capacity = native_net::PER_TICK as usize;
        let data: Arc<[u32]> = Arc::from((0..w::MAX_WORDS as u32).collect::<Vec<_>>());
        let mut first_meta = None;
        let mut complete = None;
        for tick in 1..=20 {
            sender.ticks = tick; server.ticks = tick; observer.ticks = tick;
            let clock = 40 + tick as i64;
            sender.world.game_time = clock; server.world.game_time = clock; observer.world.game_time = clock;
            sender.sent.clear(); server.sent.clear();
            let mut params = [0.0; 16]; params[9] = 1.0; params[3] = tick as f32 * 0.05;
            v::capture(&sender, Some(&orbital_post::Pass { source: Arc::from("known"), version: 1,
                params, data: Some((data.clone(), 1)), day_time: 1 }));
            // Continuous sound production also shares the bounded visual half.
            for _ in 0..32 { v::sound(&sender, "orbital:blast", None, 1.0, 1.0); }
            v::tick_client(&mut sender);
            let visual_sent = sender.sent.len();
            assert!(visual_sent <= native_net::PER_TICK as usize / 2);
            // An infinite work backlog runs AFTER visual output, as in glue.rs.
            let mut work = 0;
            while sender.native_net_send_to_server("orbital:work", &[255]).is_ok() { work += 1; }
            assert!(work >= native_net::PER_TICK as usize / 2, "world requests get half the native quota every tick");
            for (_, bytes) in &sender.sent {
                if matches!(Packet::decode(bytes), Some(Packet::State(_))) { first_meta.get_or_insert(tick); }
                if Packet::decode(bytes).is_some() { v::from_client(&mut server, 1, w::CHANNEL, bytes); }
            }
            v::tick_server(&mut server, "overworld");
            assert!(server.sent.len() <= native_net::PER_TICK as usize / 2);
            // An infinite column-answer backlog runs AFTER visual relay.
            let mut answers = 0;
            while server.native_net_send_to_player(2, "orbital:answer", &[255]).is_ok() { answers += 1; }
            assert!(answers >= native_net::PER_TICK as usize / 2, "world answers get half the native quota every tick");
            for (to, bytes) in &server.sent {
                if *to == 2 && bytes.len() >= 16 { v::from_server(&mut observer, w::CHANNEL, bytes); }
            }
            v::refresh(&mut observer);
            if let Some((_, pass)) = orbital_post::snapshot().first()
                && pass.data.as_ref().is_some_and(|(words, _)| words.len() == w::MAX_WORDS) {
                assert_eq!(&*pass.data.as_ref().unwrap().0, &*data);
                complete = Some(tick); break;
            }
        }
        assert_eq!(first_meta, Some(1), "metadata is sent on the first tick despite work and sound backlog");
        assert!(complete.is_some_and(|tick| tick <= 14), "maximum author-sized snapshot completes through both hops within 14 ticks: {complete:?}");
    }
    #[test] fn dimension_clear_cannot_readvertise_previous_pass_or_quality_rebuild_until_clear() {
        use std::sync::Arc;
        let mut client = Game::new(true);
        let mut params = [0.0; 16]; params[9] = 1.0;
        let mut pass = orbital_post::Pass { source: Arc::from("known"), version: 17,
            params, data: None, day_time: 1 };
        v::capture(&client, Some(&pass));
        v::clear(&mut client);
        v::capture(&client, Some(&pass));
        v::sound(&client, "orbital:old_world", None, 1.0, 1.0);
        v::tick_client(&mut client);
        assert!(client.sent.iter().all(|(_, b)| matches!(Packet::decode(b), Some(Packet::State(m)) if m.shader == u8::MAX)),
            "neither old visual data nor sounds may revive across dimensions");
        client.sent.clear(); pass.version = 18;
        v::capture(&client, Some(&pass)); v::tick_client(&mut client);
        assert!(client.sent.iter().all(|(_, b)| matches!(Packet::decode(b), Some(Packet::State(m)) if m.shader == u8::MAX)),
            "quality rebuild cannot resurrect the old strike with a different WGSL hash");
        v::capture(&client, None);
        client.sent.clear();
        v::capture(&client, Some(&pass)); v::tick_client(&mut client);
        assert!(client.sent.iter().any(|(_, b)| matches!(Packet::decode(b), Some(Packet::State(m)) if m.shader == 0)),
            "a normal cleared strike permits the same hashed shader on a later strike");
    }
}
