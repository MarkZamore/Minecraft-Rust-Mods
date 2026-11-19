// Orbital 0.5.17 uses vanilla command strings on the LAN. Horizon f00125f
// checks native bulk edits as typed values against the active registry.

use super::{Dim, Game, api_values::{TryIntoApi, TryIntoEngine, block_metadata_table}};
use horizon_command::{Reader, args::BlockInput, block};
use mod_api::{BlockEdit, BlockRun, ColumnBlocks, Heightmap, Identifier, StateConstraint, StatePredicate, Tags};

#[derive(Default)]
pub(super) struct WireEdit {
    pub(super) palette: Vec<String>,
    pub(super) filters: Vec<String>,
    pub(super) runs: Vec<BlockRun>,
}

#[derive(Default)]
pub(super) struct WireColumns {
    pub(super) palette: Vec<String>,
    pub(super) bases: Vec<Option<i32>>,
    pub(super) blocks: Vec<u16>,
    pub(super) tints: Vec<u32>,
}

struct CommandTags;

impl Tags for CommandTags {
    fn key(&self, id: &Identifier) -> Option<mod_api::TagKey> {
        mod_api::__private::tag_key(id)
    }
}

fn parse_command_filter(game: &Game<'_>, text: &str) -> Result<BlockInput, String> {
    let mut reader = Reader::new(text);
    let input = block::parse(&mut reader, &game.ui.registries, true)
        .map_err(|error| format!("{text:?}: {}", error.message.plain()))?;
    if reader.can_read() || input.nbt.is_some() {
        return Err(format!("{text:?}: trailing data or block NBT is not allowed"));
    }
    Ok(input)
}

fn prepare_filter(game: &Game<'_>, text: &str) -> Result<StatePredicate, String> {
    let input = parse_command_filter(game, text)?;
    let metadata = block_metadata_table().map_err(|error| format!("{error:?}"))?;
    let constraints = input.properties.into_iter()
        .map(|(name, value)| StateConstraint::new(name, value)).collect::<Vec<_>>();
    if input.tag {
        let id = Identifier::parse(&input.id).map_err(|error| format!("{error:?}"))?;
        let tags = CommandTags;
        let key = tags.key(&id).ok_or_else(|| format!("Unknown block tag: {text}"))?;
        StatePredicate::tag(metadata, &tags, key, &constraints)
    } else {
        let key = game.block_key(&input.id).ok_or_else(|| format!("Unknown block: {text}"))?;
        StatePredicate::block(metadata, key, &constraints)
    }.map_err(|error| format!("{text:?}: {error:?}"))
}

pub(super) fn edit_blocks(game: &mut Game<'_>, dimension: &str, edit: &WireEdit) -> Result<u64, String> {
    let palette = edit.palette.iter().map(|name| {
        game.block_state_id(name).and_then(|state| state.try_into_api().ok())
            .ok_or_else(|| format!("Invalid block state: {name}"))
    }).collect::<Result<_, _>>()?;
    let filters = edit.filters.iter().map(|text| prepare_filter(game, text)).collect::<Result<_, _>>()?;
    game.edit_blocks(dimension, &BlockEdit { palette, filters, runs: edit.runs.clone() })
}

pub(super) fn read_columns(
    game: &Game<'_>, dimension: &str, columns: &[(i32, i32)], heightmap: Heightmap, range: (u32, u32),
) -> Result<WireColumns, String> {
    let ColumnBlocks { palette, bases, blocks, tints } = game.read_columns(dimension, columns, heightmap, range)?;
    let palette = palette.iter().map(|state| {
        let engine = state.try_into_engine(&()).ok()?;
        game.block_state_string(engine)
    }).collect::<Option<Vec<_>>>().ok_or_else(|| "Unknown state in column response".to_owned())?;
    Ok(WireColumns { palette, bases, blocks, tints })
}

pub(super) fn chunk_loaded(game: &Game<'_>, dimension: &str, [x, z]: [i32; 2]) -> bool {
    game.native_level_open(dimension) && game.world.chunks.contains_key(&(x, z))
}

pub(super) fn force_chunk(game: &mut Game<'_>, owner: &'static str, dimension: &str, [x, z]: [i32; 2], hold: bool) {
    if !game.native_authoritative()
        || !(-1_875_000..1_875_000).contains(&x)
        || !(-1_875_000..1_875_000).contains(&z)
    {
        return;
    }
    let dim = match dimension {
        "minecraft:overworld" => Dim::Overworld,
        "minecraft:the_nether" => Dim::Nether,
        "minecraft:the_end" => Dim::End,
        _ => return,
    };
    if game.dim == dim {
        game.streaming.set_transient_forced(owner, (x, z), hold);
    } else if let Some(streaming) = game.levels.parked_streaming_of_mut(dimension) {
        streaming.set_transient_forced(owner, (x, z), hold);
    } else if hold {
        // Open a vanilla dimension if the strike asks for it. Loading chunks
        // stays on the regular work queue; changing dimensions here never
        // waits for the strike's terrain to be generated.
        let back = game.dim;
        match game.enter_level(dim) {
            Ok(()) => {
                game.streaming.set_transient_forced(owner, (x, z), true);
                if let Err(error) = game.enter_level(back) {
                    log::warn!("[mod orbital] could not put back {}: {error}", back.id());
                }
            }
            Err(error) => log::warn!("[mod orbital] could not open {dimension}: {error}"),
        }
    }
}

pub(super) fn set_flying_speed(game: &mut Game<'_>, uuid: u128, speed: f32) {
    let Some(player) = game.player(uuid) else { return; };
    let Some(mut abilities) = game.player_abilities(player) else { return; };
    let speed = if speed.is_finite() { speed.clamp(0.0, 50.0) } else { 0.05 };
    if abilities.fly_speed != speed {
        abilities.fly_speed = speed;
        game.player_set_abilities(player, abilities);
    }
}
