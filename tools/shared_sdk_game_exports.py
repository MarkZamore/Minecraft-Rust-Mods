"""Expose the game re-exports referenced by Horizon's external patch crate."""


PATCH_EXPORTS = {
    "crates/horizon-client/src/game/interface/entity_culling/mod.rs": (
        "9114f35128345b82303a13092b59552a27408a7058af1aec67431e7b356bf2cd",
        ((
            "pub(super) use horizon_client_gameplay::entity_occlusion::{\n"
            "    EntityCullDecision, EntityOcclusion, cull_box, default_range, entity_view_scale,\n"
            "};",
            "pub use horizon_client_gameplay::entity_occlusion::{EntityCullDecision, default_range};\n"
            "pub(super) use horizon_client_gameplay::entity_occlusion::{EntityOcclusion, cull_box, entity_view_scale};",
        ),),
    ),
    "crates/horizon-client/src/game/modding/api_values.rs": (
        "35d029192ef56d0390a8a43454770dc66341bac6fe61324e4af685735980561d",
        ((
            "pub(crate) use horizon_gameplay::api_values::{\n"
            "    BlockEntitySnapshotError, StackReadError, TryIntoApi, TryIntoEngine,\n"
            "    TryIntoEnginePreservingCount, block_metadata_table, finish_block_entity_writeback, stack_value,\n"
            "};",
            "pub use horizon_gameplay::api_values::{TryIntoApi, TryIntoEngine, block_metadata_table};\n"
            "pub(crate) use horizon_gameplay::api_values::{\n"
            "    BlockEntitySnapshotError, StackReadError, TryIntoEnginePreservingCount,\n"
            "    finish_block_entity_writeback, stack_value,\n"
            "};",
        ),),
    ),
    "crates/horizon-client/src/game/modding/api_values/block_state.rs": (
        "3f67790ffde265c9c2dad5716057f01402b4bc0e6ad06314d1f66a25af28cbf9",
        ((
            "pub(crate) use horizon_gameplay::api_values::block_state::*;",
            "pub use horizon_gameplay::api_values::block_state::block_listens;\n"
            "pub(crate) use horizon_gameplay::api_values::block_state::*;",
        ),),
    ),
    "crates/horizon-client/src/game/modding/native_server.rs": (
        "89c9e9877c4aba78c0d38a7976802cd6ad96ea8397d38882ef295a203cc86fc3",
        ((
            "pub(super) use argument_kinds::{CommandArguments, command_arguments};",
            "pub use argument_kinds::command_arguments;\n"
            "pub(super) use argument_kinds::CommandArguments;",
        ),),
    ),
    "crates/horizon-client/src/game/runtime/execution_role.rs": (
        "94d7ee92994a3f52714ff1eec79be3ecf7f28471795c685e6d95b156bc61a49c",
        ((
            "pub(super) use horizon_gameplay::execution_role::ExecutionRole;",
            "pub use horizon_gameplay::execution_role::ExecutionRole;",
        ),),
    ),
}
