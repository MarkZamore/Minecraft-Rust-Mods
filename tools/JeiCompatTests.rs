//! Harness for the exact patched JEI sync function and author viewer.
mod game {
    pub mod jei {
        /* RUNTIME */
    }
    use std::{marker::PhantomData, sync::Arc};

    struct NativeRecipes {
        items: Vec<jei::Item>,
        recipes: Vec<jei::Recipe>,
    }
    #[derive(Default)]
    struct Panel {
        shown: Option<bool>,
    }
    impl Panel {
        fn shown(&self, _on: impl Fn(&str) -> bool) -> Option<&bool> {
            self.shown.as_ref()
        }
    }
    struct Game<'a> {
        enabled: bool,
        player: bool,
        catalog: Option<Arc<NativeRecipes>>,
        panel: Panel,
        outputs: Vec<jei::Out>,
        phantom: PhantomData<&'a ()>,
    }
    impl Game<'_> {
        fn native_on(&self, _id: &str) -> bool { self.enabled }
        fn native_player(&self) -> Option<()> { self.player.then_some(()) }
        fn native_recipes(&mut self) -> Option<Arc<NativeRecipes>> { self.catalog.clone() }
        fn native_known_recipes(&mut self) -> Arc<[i32]> { Arc::from([7]) }
        fn native_setting(&self, _id: &str, _key: &str) -> Option<String> { None }
        fn native_chat(&mut self, text: &str) { panic!("unexpected JEI error: {text}") }
    }
    mod jei_glue {
        use super::{jei::{self, Viewer}, NativeRecipes};
        use std::{cell::RefCell, sync::Arc};
        /* GLUE */
        fn items(catalog: &NativeRecipes) -> Vec<jei::Item> { catalog.items.clone() }
        fn recipes(catalog: &NativeRecipes) -> Vec<jei::Recipe> { catalog.recipes.clone() }
        fn apply(game: &mut super::Game<'_>, out: jei::Out) -> Result<(), String> {
            game.panel.shown = Some(true);
            game.outputs.push(out);
            Ok(())
        }
        pub(super) fn search(game: &mut super::Game<'_>, text: &str) {
            event(game, jei::Event::Search(text.into()));
        }
        pub(super) fn event(game: &mut super::Game<'_>, event: jei::Event) {
            let outputs = STATE.with(|s| s.borrow_mut().viewer.as_mut().unwrap().handle(
                event, &jei::Context {
                    recipe_key: "r", uses_key: "u", give: false, creative: false, known: &[7],
                }));
            for out in outputs { apply(game, out).unwrap(); }
        }
    }
    fn catalog(ids: &[&str]) -> Arc<NativeRecipes> {
        Arc::new(NativeRecipes {
            items: ids.iter().map(|id| jei::Item {
                id: format!("minecraft:{id}"), name: (*id).into(), tags: Vec::new(),
            }).collect(),
            recipes: Vec::new(),
        })
    }
    fn game() -> Game<'static> {
        Game {
            enabled: true, player: true, catalog: Some(catalog(&["stone", "diamond"])),
            panel: Panel::default(), outputs: Vec::new(), phantom: PhantomData,
        }
    }
    fn recipe_catalog(book_id: i32) -> Arc<NativeRecipes> {
        let mut result = catalog(&["stone", "diamond"]);
        let ingredient = |item| jei::Ingredient { item, count: 1, components: String::new() };
        Arc::get_mut(&mut result).unwrap().recipes.push(jei::Recipe {
            kind: jei::Kind::CraftingShapeless, width: 0, height: 0,
            slots: vec![vec![ingredient(0)]], results: vec![ingredient(1)],
            cooking_time: 0, experience: 0.0, book_id,
        });
        result
    }
    fn listed(out: &[jei::Out]) -> Vec<String> {
        out.iter().filter_map(|out| match out {
            jei::Out::Items(items) => Some(items.iter().map(|i| i.item.clone()).collect::<Vec<_>>()),
            _ => None,
        }).flatten().collect()
    }
    #[test]
    fn unchanged_table_does_not_republish_every_frame() {
        let mut game = game();
        jei_glue::sync(&mut game);
        assert_eq!(listed(&game.outputs).len(), 2);
        game.outputs.clear();
        for _ in 0..60 { jei_glue::sync(&mut game); }
        assert!(game.outputs.is_empty());
    }
    #[test]
    fn replaced_catalog_updates_items_and_keeps_search() {
        let mut game = game();
        jei_glue::sync(&mut game);
        jei_glue::search(&mut game, "diamond");
        game.outputs.clear();
        game.catalog = Some(catalog(&["stone", "diamond", "diamond_block"]));
        jei_glue::sync(&mut game);
        assert_eq!(listed(&game.outputs), ["minecraft:diamond", "minecraft:diamond_block"]);
    }
    #[test]
    fn replaced_recipes_keep_history_and_refresh_book_ids() {
        let mut game = game();
        game.catalog = Some(recipe_catalog(7));
        jei_glue::sync(&mut game);
        jei_glue::event(&mut game, jei::Event::Key {
            key: "r".into(), stack: jei::Stack::item("minecraft:diamond"),
        });
        game.outputs.clear();
        game.catalog = Some(recipe_catalog(8));
        jei_glue::sync(&mut game);
        assert!(game.outputs.iter().any(|out| matches!(out,
            jei::Out::Recipes { views, .. } if views.len() == 1 && views[0].action == Some(false))));
        game.outputs.clear();
        jei_glue::event(&mut game, jei::Event::Action(0));
        assert!(matches!(&game.outputs[0], jei::Out::Place { book_id: 8, .. }));
        game.outputs.clear();
        jei_glue::event(&mut game, jei::Event::Back);
        assert!(matches!(&game.outputs[0], jei::Out::Recipes { views, .. } if views.is_empty()));
    }
    #[test]
    fn missing_panel_is_restored_without_resetting_search() {
        let mut game = game();
        jei_glue::sync(&mut game);
        jei_glue::search(&mut game, "diamond");
        game.outputs.clear();
        game.panel = Panel::default();
        jei_glue::sync(&mut game);
        assert_eq!(listed(&game.outputs), ["minecraft:diamond"]);
    }
    #[test]
    fn new_game_object_does_not_inherit_an_invisible_viewer() {
        let mut first = game();
        jei_glue::sync(&mut first);
        let mut second = game();
        jei_glue::sync(&mut second);
        assert_eq!(listed(&second.outputs).len(), 2);
    }
    #[test]
    fn no_player_resets_search_for_the_next_world() {
        let mut game = game();
        jei_glue::sync(&mut game);
        jei_glue::search(&mut game, "diamond");
        game.player = false;
        jei_glue::sync(&mut game);
        game.player = true;
        game.outputs.clear();
        jei_glue::sync(&mut game);
        assert_eq!(listed(&game.outputs).len(), 2);
    }
    #[test]
    fn disabled_or_still_loading_does_not_build_a_viewer() {
        let mut game = game();
        let ready = game.catalog.take();
        jei_glue::sync(&mut game);
        assert!(game.outputs.is_empty());
        game.catalog = ready;
        game.enabled = false;
        jei_glue::sync(&mut game);
        assert!(game.outputs.is_empty());
        game.enabled = true;
        jei_glue::sync(&mut game);
        assert_eq!(listed(&game.outputs).len(), 2);
    }
}
