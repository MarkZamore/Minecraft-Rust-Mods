using System.IO;
using System.Net;
using System.Net.Http;
using System.Security.Cryptography;
using System.Text.Json;
using System.Windows;
using System.Windows.Controls;
using System.Windows.Documents;
using System.Windows.Media.Imaging;
using Minecraft;

if (args.Length != 4 && (args.Length != 6 || args[4] != "--engine-upgrade"))
    throw new ArgumentException("BootstrapUpdateQa <LANMinecraft-root> <old-dist> <new-dist> <output> [--engine-upgrade <pack-source>]");

var workspace = Path.GetFullPath(args[0]);
var output = Path.GetFullPath(args[3]);
var allowed = Path.Combine(workspace, "artifacts", "agent-runs") + Path.DirectorySeparatorChar;
Check(output.StartsWith(allowed, StringComparison.OrdinalIgnoreCase) && !Directory.Exists(output),
    "Use a fresh output under the registered agent run.");
CheckOrdinary(output, workspace);
var fixture = Path.Combine(output, "installation");
var oldRelease = Release.Read(args[1]);
var newRelease = Release.Read(args[2]);
var expected = args.Length == 6 ? UpgradeExpectations.Read(args[5]) : null;
Directory.CreateDirectory(output);
var source = new PackSyncSource("MarkZamore", "Minecraft-Rust-Mods", "pack-latest");
const string pack = "Minecraft Rust Mods";
var json = new JsonSerializerOptions(JsonSerializerDefaults.Web) { WriteIndented = true };
var report = new Dictionary<string, object?>
{
    ["status"] = "RUNNING",
    ["profile"] = expected is null ? "bootstrap-only" : "engine-upgrade",
    ["gameplay"] = "NOT RUN",
    ["steamMultiplayer"] = "NOT RUN"
};
try
{
    Check(oldRelease.Manifest.PackId == "minecraft-10" && newRelease.Manifest.PackId == "minecraft-10"
        && oldRelease.Manifest.RelativePath == pack && newRelease.Manifest.RelativePath == pack,
        "The fixtures must describe Minecraft Rust Mods.");
    var oldFiles = oldRelease.Manifest.Files.ToDictionary(file => file.Path, StringComparer.OrdinalIgnoreCase);
    var newFiles = newRelease.Manifest.Files.ToDictionary(file => file.Path, StringComparer.OrdinalIgnoreCase);
    var changed = newFiles.Values.Where(file => !oldFiles.TryGetValue(file.Path, out var old)
        || file.SizeBytes != old.SizeBytes || file.Sha256 != old.Sha256).Select(file => file.Path).Order().ToArray();
    var removed = oldFiles.Keys.Except(newFiles.Keys, StringComparer.OrdinalIgnoreCase).Order().ToArray();
    if (expected is null)
        Check(changed.SequenceEqual(new[] { "native/launch-rust-edition.exe", "native/native-mods.json" })
            && removed.Length == 0, "Bootstrap-only mode accepts exactly the bootstrap and base-provenance change.");
    else
    {
        Check(oldRelease.Manifest.Revision != newRelease.Manifest.Revision
            && newRelease.Manifest.MinecraftVersion == expected.MinecraftVersion
            && newRelease.Manifest.ModCount == expected.Mods.Count,
            "The engine-upgrade manifest does not match the selected Minecraft version and mod count.");
        Check(changed.Contains("native/horizon-client.exe") && changed.Contains("native/native-mods.json")
            && changed.Contains("portable-pack.json"), "The upgrade must replace the combined client and runtime metadata.");
        using var oldLibrary = System.IO.Compression.ZipFile.OpenRead(oldRelease.Library);
        using var oldEntry = oldLibrary.GetEntry("library.json")!.Open();
        var oldContent = JsonSerializer.Deserialize<CatalogDescription>(oldEntry, json)!;
        var removedIds = oldContent.Mods.Where(mod => mod.IsLoadedMod).Select(mod => mod.Id)
            .Except(expected.Mods.Keys, StringComparer.Ordinal).Order().ToArray();
        var retainedIds = oldContent.Mods.Where(mod => mod.IsLoadedMod).Select(mod => mod.Id)
            .Except(removedIds, StringComparer.Ordinal).ToHashSet(StringComparer.Ordinal);
        Check(removedIds.SequenceEqual(new[] { "keystrokes", "server_browser", "speedrun_timer" })
            && retainedIds.SetEquals(expected.Mods.Keys), "Only the three player-requested mods may leave this upgrade.");
        report["removedModIds"] = removedIds;
        report["selectionSha256"] = expected.SelectionSha256;
        report["inputLockSha256"] = expected.InputLockSha256;
        report["horizonRevision"] = expected.HorizonRevision;
        report["approvedLibraryContentSha256"] = expected.LibraryContentSha256;
    }
    var paths = new AppPaths(fixture);
    paths.Ensure();
    var canonical = paths.CombineUnderPacks(pack);
    Directory.CreateDirectory(canonical);
    File.WriteAllText(Path.Combine(canonical, PortablePackSyncService.SourceMarkerFileName),
        JsonSerializer.Serialize(new { schemaVersion = 1, source.Owner, source.Repo, source.Tag }, json));
    var logger = new Logger(paths.LogFile);
    using var handler = new FixtureHandler(oldRelease);
    using var http = new HttpClient(handler);
    var service = new PortablePackSyncService(paths, logger, http);
    using var instances = new PackInstanceService(paths, logger);
    var installed = await service.SyncAsync(pack, null, CancellationToken.None);
    Check(installed.Outcome == PackSyncOutcome.Installed, "The old release did not install.");
    await instances.PrepareAsync(pack);
    var game = paths.CombineUnderInstances(pack);
    var sentinels = new Dictionary<string, string>
    {
        ["options.txt"] = "version:5023\ninvertYMouse:true\nkey_orbital.wheel:key.keyboard.p\n"
            + "key_waypoints.create:key.keyboard.b\nkey_waypoints.list:key.keyboard.y\n",
        ["horizon-graphics.txt"] = "api:dx12\n",
        ["horizon-mods.txt"] = "key.horizonmatica.rotate:R\nkey.waypoints.create:key.keyboard.b\nkey.waypoints.list:key.keyboard.y\n",
        ["config/horizon_physics.txt"] = "setup_done=1\n",
        ["config/tectonic-horizon.json"] = "{\"personal\":true}",
        ["saves/BootstrapUpdateQa/level.dat"] = "Synthetic world preservation marker",
        ["saves/BootstrapUpdateQa/playerdata/qa.dat"] = "Synthetic player preservation marker",
        ["backups/personal.zip"] = "Synthetic backup preservation marker",
        ["servers.dat"] = "Synthetic manually saved server marker",
        ["horizon-mods/.horizon-mod-data/speedrun_timer/qa.txt"] = "Personal timer data",
        ["horizon-mods/.horizon-mod-data/keystrokes/qa.txt"] = "Personal keys HUD settings",
        ["horizon-mods/.horizon-mod-data/server_browser/qa.txt"] = "Personal server menu data"
    };
    foreach (var (relative, contents) in sentinels)
    {
        var path = Path.Combine(game, relative);
        Directory.CreateDirectory(Path.GetDirectoryName(path)!);
        File.WriteAllText(path, contents);
    }
    handler.Release = newRelease;
    var updated = await service.SyncAsync(pack, null, CancellationToken.None);
    Check(updated.Outcome == PackSyncOutcome.Updated && updated.Revision == newRelease.Manifest.Revision
        && updated.FilesChanged == changed.Length + removed.Length,
        "The update did not apply exactly the changed and removed managed files.");
    await instances.PrepareAsync(pack);
    foreach (var file in newRelease.Manifest.Files)
    {
        var path = Path.Combine(canonical, file.Path);
        Check(new FileInfo(path).Length == file.SizeBytes && Hash(path) == file.Sha256,
            "Installed file differs from the exact release: " + file.Path);
    }
    foreach (var relative in removed)
        Check(!File.Exists(Path.Combine(canonical, relative)), "An obsolete managed payload file remained: " + relative);
    var repeat = await service.SyncAsync(pack, null, CancellationToken.None);
    Check(repeat.Outcome == PackSyncOutcome.UpToDate && repeat.FilesChanged == 0
        && repeat.BytesDownloaded == 0, "The repeated update must reuse the installed bytes.");
    await instances.PrepareAsync(pack);
    foreach (var (relative, contents) in sentinels)
        Check(File.ReadAllText(Path.Combine(game, relative)) == contents, "A personal file changed: " + relative);
    Check(!Directory.Exists(Path.Combine(game, "native")), "The game instance contains a duplicate native runtime.");
    report["oldRevision"] = oldRelease.Manifest.Revision;
    report["newRevision"] = newRelease.Manifest.Revision;
    report["launcherAssemblySha256"] = Hash(typeof(PortablePackSyncService).Assembly.Location);
    report["managedFilesVerified"] = newRelease.Manifest.Files.Length;
    report["changedManagedPaths"] = changed;
    report["removedManagedPaths"] = removed;
    report["personalFilesPreserved"] = sentinels.Keys;
    report["update"] = "PASS";
    report["idempotence"] = "PASS";
    if (expected is not null)
    {
        var controls = new ControlsPresetService(logger);
        var preset = controls.TryLoad(canonical);
        Check(preset is not null && preset.Entries.Single(entry => entry.Name == "key_orbital.wheel").Value
            == "key.keyboard.insert"
            && preset.Entries.Single(entry => entry.Name == "key_waypoints.create").Value == "key.keyboard.f7"
            && preset.Entries.Single(entry => entry.Name == "key_waypoints.list").Value == "key.keyboard.f8",
            "The release preset must retain Insert and separate Waypoints onto F7/F8.");
        Check(controls.Evaluate(canonical, game).FirstDifference == "key_orbital.wheel",
            "The update must preserve the personal Orbital key until the preset is explicitly applied.");
        var changedControls = controls.Apply(canonical, game);
        Check(changedControls > 0 && controls.Evaluate(canonical, game).IsApplied,
            "The explicit preset application did not apply the release's key mappings.");
        var options = File.ReadAllText(Path.Combine(game, "options.txt"));
        Check(options.Contains("invertYMouse:true\n", StringComparison.Ordinal)
            && options.Contains("version:5023\n", StringComparison.Ordinal)
            && options.Contains("key_orbital.wheel:key.keyboard.insert\n", StringComparison.Ordinal)
            && options.Contains("key_waypoints.create:key.keyboard.f7\n", StringComparison.Ordinal)
            && options.Contains("key_waypoints.list:key.keyboard.f8\n", StringComparison.Ordinal),
            "Preset application changed another personal setting or omitted a pack mod mapping.");
        foreach (var (relative, contents) in sentinels.Where(file => file.Key != "options.txt"))
            Check(File.ReadAllText(Path.Combine(game, relative)) == contents,
                "Preset application changed unrelated personal data: " + relative);
        Check(File.ReadAllText(Path.Combine(game, "horizon-mods.txt")) == sentinels["horizon-mods.txt"],
            "Applying the preset directly rewrote personal native mod settings before the game reads it.");
        Check(controls.Apply(canonical, game) == 0, "Repeated preset application must be idempotent.");
        report["explicitControlsPreset"] = "PASS";
        report["controlsPresetSha256"] = preset!.Sha256;
        report["controlsMappings"] = preset.Entries.Count;
    }

    Exception? presentationError = null;
    var presentation = new Thread(() =>
    {
        try
        {
            var app = new App();
            app.InitializeComponent();
            using var library = System.IO.Compression.ZipFile.OpenRead(newRelease.Library);
            using var entry = library.GetEntry("library.json")!.Open();
            var content = JsonSerializer.Deserialize<CatalogDescription>(entry, json)!;
            var card = new CatalogPack
            {
                Entry = new CatalogSource(content.Id, content.RelativePath, source),
                Content = content, Preview = new BitmapImage()
            };
            card.BuildTree();
            var expectedCount = expected?.Mods.Count ?? 40;
            var expectedMinecraftVersion = expected?.MinecraftVersion ?? "26.3";
            Check(card.VersionLabel == $"Minecraft {expectedMinecraftVersion} ({expectedCount} модов)",
                "The version label must show just Minecraft version and mod count.");
            if (expected is null)
                Check(card.Summary == "", "The historical bootstrap-only card must retain its blank summary.");
            else
            {
                var displayedDescription = string.Join(" ", expected.Description
                    .Split(['\r', '\n'], StringSplitOptions.RemoveEmptyEntries).Select(line => line.Trim()));
                Check(content.Summary == expected.Summary && card.Summary == expected.Summary
                    && content.Description == expected.Description && card.Description == displayedDescription,
                    "The card summary or description differs from the approved source library/content.json.");
                report["summaryMatchesApprovedSource"] = "PASS";
                report["descriptionMatchesApprovedSource"] = "PASS";
            }
            Check(card.WebsiteUri is null && content.NativeProject?.WebsiteUrl is null
                && !card.Description.Contains("Точный состав", StringComparison.Ordinal),
                "The removed project URL or trailing composition sentence is still present.");
            Check(card.ModCount == expectedCount && card.Content.Mods.All(mod => mod.WebsiteUrl is not null),
                "Every selected mod must retain its own official project page.");
            if (expected is not null)
            {
                var actual = content.Mods.Where(mod => mod.IsLoadedMod).ToDictionary(mod => mod.Id, mod => mod.Version,
                    StringComparer.Ordinal);
                Check(actual.Count == expected.Mods.Count && expected.Mods.All(pair =>
                    actual.TryGetValue(pair.Key, out var version) && version == pair.Value)
                    && content.NativeProject?.Revision == expected.HorizonRevision
                    && content.MinecraftVersion == expected.MinecraftVersion,
                    "The card's exact mod IDs/versions or Horizon revision differ from the selected locks.");
            }
            using var detail = new PackDetailPage(card);
            detail.Measure(new Size(1000, 800));
            detail.Arrange(new Rect(0, 0, 1000, 800));
            var link = (Hyperlink)detail.FindName("PackProjectLink");
            Check(link.NavigateUri is null && ((TextBlock)link.Parent).Visibility == Visibility.Collapsed,
                "The detail view still presents a native project link.");
            report["wpfPresentation"] = "PASS";
            report["versionLabel"] = card.VersionLabel;
        }
        catch (Exception error) { presentationError = error; }
    });
    presentation.SetApartmentState(ApartmentState.STA);
    presentation.Start();
    presentation.Join();
    if (presentationError is not null) throw new InvalidOperationException("Presentation check failed", presentationError);
    report["status"] = "PASS";
}
catch (Exception error)
{
    report["status"] = "FAIL";
    report["error"] = error.ToString();
    throw;
}
finally
{
    if (Directory.Exists(fixture))
    {
        CheckOrdinary(fixture, workspace);
        DeleteFixture(fixture);
    }
    report["fixtureRemoved"] = true;
    File.WriteAllText(Path.Combine(output, "report.json"), JsonSerializer.Serialize(report, json));
}

static string Hash(string path)
{
    using var stream = File.OpenRead(path);
    return Convert.ToHexStringLower(SHA256.HashData(stream));
}
static void Check(bool condition, string message)
{
    if (!condition) throw new InvalidOperationException(message);
}
static void CheckOrdinary(string path, string workspace)
{
    for (var current = path; !current.Equals(workspace, StringComparison.OrdinalIgnoreCase);
        current = Path.GetDirectoryName(current) ?? throw new InvalidOperationException("Fixture escaped the workspace."))
        if (Directory.Exists(current) || File.Exists(current))
            Check((File.GetAttributes(current) & FileAttributes.ReparsePoint) == 0, "A fixture ancestor is linked.");
}
static void DeleteFixture(string directory)
{
    foreach (var path in Directory.EnumerateFileSystemEntries(directory))
    {
        var attributes = File.GetAttributes(path);
        Check((attributes & FileAttributes.ReparsePoint) == 0, "A fixture entry is linked.");
        if ((attributes & FileAttributes.Directory) != 0) DeleteFixture(path);
        else File.Delete(path);
    }
    Directory.Delete(directory);
}

sealed record FileEntry(string Path, long SizeBytes, string Sha256);
sealed record Asset(string Name, long SizeBytes, string Sha256);
sealed record Manifest(string PackId, string RelativePath, string Revision, string MinecraftVersion, int? ModCount,
    FileEntry[] Files, Asset[] Assets, Asset Library);
sealed record Release(string Root, Manifest Manifest)
{
    public string Library => Path.Combine(Root, "assets", Manifest.Library.Name);
    public static Release Read(string directory)
    {
        var root = Path.GetFullPath(directory);
        var manifest = JsonSerializer.Deserialize<Manifest>(File.ReadAllBytes(Path.Combine(root, "pack-manifest.json")),
            new JsonSerializerOptions(JsonSerializerDefaults.Web))
            ?? throw new InvalidDataException("A release has no manifest.");
        var library = manifest.Library;
        if (library.Name != Path.GetFileName(library.Name) || library.Name.Contains(':')
            || library.Name.Contains('\\') || library.Name is "." or "..")
            throw new InvalidDataException("The library must name an ordinary release asset.");
        var file = Path.Combine(root, "assets", library.Name);
        using var stream = File.OpenRead(file);
        if (stream.Length != library.SizeBytes
            || Convert.ToHexStringLower(SHA256.HashData(stream)) != library.Sha256)
            throw new InvalidDataException("The library bytes differ from the release manifest.");
        return new(root, manifest);
    }
}

sealed record UpgradeExpectations(string MinecraftVersion, string HorizonRevision, Dictionary<string, string> Mods,
    string SelectionSha256, string InputLockSha256, string Summary, string Description, string LibraryContentSha256)
{
    public static UpgradeExpectations Read(string packSource)
    {
        var root = Path.GetFullPath(packSource);
        var selectionBytes = File.ReadAllBytes(Path.Combine(root, "tools", "mod-selection.json"));
        var inputBytes = File.ReadAllBytes(Path.Combine(root, "tools", "mod-input-lock.json"));
        var contentBytes = File.ReadAllBytes(Path.Combine(root, "library", "content.json"));
        using var selection = JsonDocument.Parse(selectionBytes);
        using var input = JsonDocument.Parse(inputBytes);
        using var content = JsonDocument.Parse(contentBytes);
        using var runtime = JsonDocument.Parse(File.ReadAllBytes(Path.Combine(root, "portable-pack.json")));
        var inputHash = Convert.ToHexStringLower(SHA256.HashData(inputBytes));
        if (selection.RootElement.GetProperty("schemaVersion").GetInt32() != 1
            || selection.RootElement.GetProperty("inputLockSha256").GetString() != inputHash)
            throw new InvalidDataException("The selected mod IDs do not belong to the exact input lock.");
        var projects = input.RootElement.GetProperty("projects").EnumerateArray()
            .ToDictionary(project => project.GetProperty("id").GetString()!, StringComparer.Ordinal);
        var mods = new Dictionary<string, string>(StringComparer.Ordinal);
        foreach (var id in selection.RootElement.GetProperty("ids").EnumerateArray().Select(id => id.GetString()!))
        {
            if (!projects.TryGetValue(id, out var project) || !mods.TryAdd(id, project.GetProperty("version").GetString()!))
                throw new InvalidDataException("The selection repeats an ID or refers to an unknown mod.");
        }
        if (mods.Count == 0) throw new InvalidDataException("The engine-upgrade selection is empty.");
        return new(runtime.RootElement.GetProperty("minecraftVersion").GetString()!,
            input.RootElement.GetProperty("horizonRevision").GetString()!, mods,
            Convert.ToHexStringLower(SHA256.HashData(selectionBytes)), inputHash,
            content.RootElement.GetProperty("summary").GetString()
                ?? throw new InvalidDataException("The approved library summary is not a string."),
            content.RootElement.GetProperty("description").GetString()
                ?? throw new InvalidDataException("The approved library description is not a string."),
            Convert.ToHexStringLower(SHA256.HashData(contentBytes)));
    }
}
sealed class FixtureHandler(Release release) : HttpMessageHandler
{
    public Release Release { get; set; } = release;
    protected override Task<HttpResponseMessage> SendAsync(HttpRequestMessage request, CancellationToken token)
    {
        const string prefix = "/MarkZamore/Minecraft-Rust-Mods/releases/download/pack-latest/";
        var uri = request.RequestUri!;
        if (request.Method != HttpMethod.Get || uri.Scheme != "https" || uri.Host != "github.com"
            || !uri.AbsolutePath.StartsWith(prefix, StringComparison.Ordinal))
            throw new InvalidOperationException("Unexpected request in the isolated fixture.");
        var name = Uri.UnescapeDataString(uri.AbsolutePath[prefix.Length..]);
        var file = name == "pack-manifest.json" ? Path.Combine(Release.Root, name)
            : Path.Combine(Release.Root, "assets", Release.Manifest.Assets.Single(asset => asset.Name == name).Name);
        return Task.FromResult(new HttpResponseMessage(HttpStatusCode.OK)
        {
            Content = new StreamContent(File.OpenRead(file))
        });
    }
}
