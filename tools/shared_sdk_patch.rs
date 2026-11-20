//! Feed the locked pack inputs to Horizon's SDK function-copy generator.
//!
//! This example uses the exact horizon-mod-api-gen crate from the prepared SDK
//! source. It only emits patch Rust source and replacement identities.

use horizon_mod_api_gen::{functions::FunctionIndex, patch};
use serde::Deserialize;
use serde_json::{Value, json};
use std::{collections::BTreeMap, env, fs, path::Path};

#[path = "lanminecraft_shared_sdk_extensions/mod.rs"]
mod game_extensions;

#[derive(Deserialize)]
struct Inputs {
    #[serde(rename = "schemaVersion")]
    schema: u32,
    mods: Vec<Package>,
}

#[derive(Deserialize)]
struct Package {
    id: String,
    #[serde(default)]
    source: String,
    #[serde(default)]
    injections: Vec<Row>,
    #[serde(default)]
    modules: Vec<Module>,
}

#[derive(Deserialize)]
struct Row {
    target: String,
    helper: String,
    #[serde(flatten)]
    fields: BTreeMap<String, Value>,
}

#[derive(Deserialize)]
struct Module {
    id: String,
    file: String,
    name: String,
    source: String,
    visibility: Option<String>,
}

#[derive(Deserialize)]
struct Index {
    schema: u32,
    build: Build,
    #[serde(flatten)]
    index: FunctionIndex,
}

#[derive(Deserialize)]
struct Build {
    commit: String,
}

fn identifiers(text: &str, extras: &mut patch::Extras) {
    if let Ok(tokens) = text.replace('$', " ").parse::<proc_macro2::TokenStream>() {
        patch::idents_in(&tokens, &mut extras.idents);
    }
}

fn option_identifiers(value: &Value, extras: &mut patch::Extras) {
    match value {
        Value::String(text) => identifiers(text, extras),
        Value::Array(items) => items
            .iter()
            .for_each(|item| option_identifiers(item, extras)),
        Value::Object(fields) => fields
            .values()
            .for_each(|item| option_identifiers(item, extras)),
        _ => {}
    }
}

fn run(
    input: &Path,
    root: &Path,
    source: &Path,
    index: &Path,
    abi: &str,
    output: &Path,
) -> Result<(), String> {
    let read = |path: &Path| {
        fs::read_to_string(path).map_err(|error| format!("{}: {error}", path.display()))
    };
    let inputs: Inputs = serde_json::from_str(&read(input)?).map_err(|error| error.to_string())?;
    let index: Index = serde_json::from_str(&read(index)?).map_err(|error| error.to_string())?;
    if inputs.schema != 1 || index.schema != 1 || index.build.commit != abi {
        return Err("Pack inputs or function index do not match the selected SDK".into());
    }
    let index = index.index.checked()?;
    let mut extras = patch::Extras::default();
    let mut targets = Vec::new();
    for package in &inputs.mods {
        let folder = root.join(&package.source);
        for row in &package.injections {
            targets.push(row.target.clone());
            identifiers(&read(&folder.join(&row.helper))?, &mut extras);
            for field in row.fields.values() {
                option_identifiers(field, &mut extras);
            }
        }
        for module in &package.modules {
            extras.modules.push(patch::SupportModule {
                id: format!("{}:{}", package.id, module.id),
                file: module.file.clone(),
                name: module.name.clone(),
                text: read(&folder.join(&module.source))?,
                public: module.visibility.as_deref() == Some("pub"),
            });
        }
    }
    let generated = patch::generate(source, "horizon_patched", &targets, &extras, &index).map_err(
        |refusals| {
            refusals
                .iter()
                .map(ToString::to_string)
                .collect::<Vec<_>>()
                .join("\n")
        },
    )?;
    fs::create_dir(output).map_err(|error| error.to_string())?;
    let mut crates = Vec::new();
    for (name, text) in generated.libs {
        fs::write(output.join(format!("{name}.rs")), text).map_err(|error| error.to_string())?;
        crates.push(name);
    }
    let functions: Vec<_> = generated.patched.into_iter().map(|function| {
        json!({"target": function.target, "copy": function.id, "install": function.install})
    }).collect();
    let record =
        json!({"schemaVersion": 1, "abiBuildId": abi, "crates": crates, "functions": functions});
    let text = serde_json::to_string_pretty(&record).map_err(|error| error.to_string())?;
    fs::write(output.join("patch.json"), text + "\n").map_err(|error| error.to_string())
}

fn main() {
    let args: Vec<_> = env::args_os().skip(1).collect();
    let result = match args.as_slice() {
        [operation, input, output] if operation == "--adapt-game-source" => {
            fs::read_to_string(input)
                .map_err(|error| error.to_string())
                .and_then(|source| game_extensions::adapt_game(&source))
                .and_then(|source| fs::write(output, source).map_err(|error| error.to_string()))
        }
        [input, root, source, index, abi, output] => run(
            Path::new(input),
            Path::new(root),
            Path::new(source),
            Path::new(index),
            abi.to_str().expect("ASCII ABI identity"),
            Path::new(output),
        ),
        _ => Err("Expected MOD_INPUT MOD_DIRECTORY SOURCE SDK_INDEX ABI OUTPUT or --adapt-game-source INPUT OUTPUT".into()),
    };
    if let Err(error) = result {
        eprintln!("{error}");
        std::process::exit(1);
    }
}
