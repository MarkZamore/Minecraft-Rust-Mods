//! Move the pack's Game methods onto traits implemented in the patch crate.

use quote::{format_ident, quote};
use syn::{
    Attribute, FnArg, Ident, ImplItem, Item, ItemImpl, Meta, Pat, Token, Type, Visibility,
    parse_quote, punctuated::Punctuated,
};

struct Extension {
    name: Ident,
    availability: Vec<Attribute>,
}

#[derive(Default)]
struct Extensions {
    next_id: usize,
}

pub fn adapt_game(source: &str) -> Result<String, String> {
    let mut file =
        syn::parse_file(source).map_err(|error| format!("Game patch source: {error}"))?;
    let extensions = Extensions::default().rewrite(&mut file.items, false, "crate")?;
    if extensions.is_empty() {
        return Ok(source.to_owned());
    }
    import_extensions(&mut file.items, &extensions);
    Ok(prettyplease::unparse(&file))
}

impl Extensions {
    fn rewrite(
        &mut self,
        items: &mut Vec<Item>,
        shadowed: bool,
        scope: &str,
    ) -> Result<Vec<Extension>, String> {
        let shadowed = shadowed || items.iter().any(declares_game);
        let mut visible = Vec::new();
        for item in std::mem::take(items) {
            match item {
                Item::Impl(mut block) if foreign_game(&block, shadowed) => {
                    let name = format_ident!("__LanMinecraftSdkGameExtension{}", self.next_id);
                    self.next_id += 1;
                    let availability = availability(&block.attrs)?;
                    items.push(extension_trait(&mut block, &name, &availability, scope)?);
                    items.push(Item::Impl(block));
                    visible.push(Extension { name, availability });
                }
                Item::Mod(mut module) => {
                    let mut aliases = Vec::new();
                    if let Some((_, children)) = &mut module.content {
                        let child = &module.ident;
                        let child_scope = format!("{scope}::{child}");
                        for extension in self.rewrite(children, shadowed, &child_scope)? {
                            // Each parent can name its immediate child, including private modules.
                            let mut attrs = availability(&module.attrs)?;
                            attrs.extend(extension.availability);
                            let name = extension.name;
                            aliases.push(parse_quote!(#(#attrs)* pub(crate) use #child::#name;));
                            visible.push(Extension {
                                name,
                                availability: attrs,
                            });
                        }
                    }
                    items.push(Item::Mod(module));
                    items.extend(aliases);
                }
                item => items.push(item),
            }
        }
        Ok(visible)
    }
}

fn declares_game(item: &Item) -> bool {
    match item {
        Item::Struct(item) => item.ident == "Game",
        Item::Enum(item) => item.ident == "Game",
        Item::Union(item) => item.ident == "Game",
        Item::Type(item) => item.ident == "Game",
        _ => false,
    }
}

fn foreign_game(block: &ItemImpl, shadowed: bool) -> bool {
    if block.trait_.is_some() {
        return false;
    }
    let Type::Path(owner) = block.self_ty.as_ref() else {
        return false;
    };
    if owner.qself.is_some() {
        return false;
    }
    let path: Vec<_> = owner.path.segments.iter().map(|part| &part.ident).collect();
    match path.as_slice() {
        [game] => *game == "Game" && !shadowed,
        [sdk, module, game] => *sdk == "horizon_client" && *module == "game" && *game == "Game",
        _ => false,
    }
}

fn extension_trait(
    block: &mut ItemImpl,
    name: &Ident,
    available: &[Attribute],
    scope: &str,
) -> Result<Item, String> {
    let mut declarations = Vec::new();
    for member in &mut block.items {
        let ImplItem::Fn(method) = member else {
            return Err(format!(
                "{scope}: Game extension contains an unsupported non-function item"
            ));
        };
        if method.sig.constness.is_some() {
            return Err(format!(
                "{scope}: const Game extension method {} is unsupported",
                method.sig.ident
            ));
        }
        let mut prototype = method.sig.clone();
        for argument in &mut prototype.inputs {
            match argument {
                FnArg::Receiver(receiver) if receiver.reference.is_none() => {
                    receiver.mutability = None
                }
                FnArg::Typed(argument) => match argument.pat.as_mut() {
                    Pat::Ident(pattern) => {
                        pattern.by_ref = None;
                        pattern.mutability = None;
                        pattern.subpat = None;
                    }
                    _ => argument.pat = Box::new(parse_quote!(_)),
                },
                _ => {}
            }
        }
        let attrs = availability(&method.attrs)?;
        declarations.push(quote!(#(#attrs)* #prototype;));
        method.vis = Visibility::Inherited;
    }
    let generics = &block.generics;
    let (impl_generics, ty_generics, where_clause) = generics.split_for_impl();
    let declaration = parse_quote!(
        #(#available)*
        pub(crate) trait #name #impl_generics #where_clause { #(#declarations)* }
    );
    block.trait_ = Some((None, parse_quote!(#name #ty_generics), Default::default()));
    Ok(declaration)
}

fn availability(attrs: &[Attribute]) -> Result<Vec<Attribute>, String> {
    attrs
        .iter()
        .filter_map(|attr| match configuration(&attr.meta) {
            Ok(Some(meta)) => Some(Ok(Attribute {
                meta,
                ..attr.clone()
            })),
            Ok(None) => None,
            Err(error) => Some(Err(format!("Game extension attributes: {error}"))),
        })
        .collect()
}

fn configuration(meta: &Meta) -> syn::Result<Option<Meta>> {
    if meta.path().is_ident("cfg") {
        return Ok(Some(meta.clone()));
    }
    if !meta.path().is_ident("cfg_attr") {
        return Ok(None);
    }
    let list = meta.require_list()?;
    let mut terms = list
        .parse_args_with(Punctuated::<Meta, Token![,]>::parse_terminated)?
        .into_iter();
    let condition = terms
        .next()
        .ok_or_else(|| syn::Error::new_spanned(list, "missing cfg_attr condition"))?;
    let gates: Vec<_> = terms
        .map(|term| configuration(&term))
        .collect::<syn::Result<Vec<_>>>()?
        .into_iter()
        .flatten()
        .collect();
    Ok((!gates.is_empty()).then(|| parse_quote!(cfg_attr(#condition, #(#gates),*))))
}

fn import_extensions(items: &mut Vec<Item>, extensions: &[Extension]) {
    for item in items.iter_mut() {
        if let Item::Mod(module) = item
            && let Some((_, children)) = &mut module.content
        {
            import_extensions(children, extensions);
        }
    }
    items.splice(
        0..0,
        extensions.iter().map(|extension| {
            let Extension { name, availability } = extension;
            parse_quote!(#(#availability)* use crate::#name as _;)
        }),
    );
}
