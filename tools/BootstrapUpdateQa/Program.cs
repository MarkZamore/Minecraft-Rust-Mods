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

if (args.Length != 4)
    throw new ArgumentException("BootstrapUpdateQa <LANMinecraft-root> <old-dist> <new-dist> <output>");

var workspace = Path.GetFullPath(args[0]);
var output = Path.GetFullPath(args[3]);
var allowed = Path.Combine(workspace, "artifacts", "agent-runs") + Path.DirectorySeparatorChar;
Check(output.StartsWith(allowed, StringComparison.OrdinalIgnoreCase) && !Directory.Exists(output),
    "Use a fresh output under the registered agent run.");
CheckOrdinary(output, workspace);
Directory.CreateDirectory(output);
var fixture = Path.Combine(output, "installation");
var oldRelease = Release.Read(args[1]);
var newRelease = Release.Read(args[2]);
var source = new PackSyncSource("MarkZamore", "Minecraft-Rust-Mods", "pack-latest");
const string pack = "Minecraft Rust Mods";
var json = new JsonSerializerOptions(JsonSerializerDefaults.Web) { WriteIndented = true };
var report = new Dictionary<string, object?> { ["status"] = "RUNNING" };
try
{
    Check(oldRelease.Manifest.PackId == "minecraft-10" && newRelease.Manifest.PackId == "minecraft-10"
        && oldRelease.Manifest.RelativePath == pack && newRelease.Manifest.RelativePath == pack,
        "The fixtures must describe Minecraft Rust Mods.");
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
        ["options.txt"] = "version:5023\ninvertYMouse:true\nkey_orbital.wheel:key.keyboard.p\n",
        ["horizon-graphics.txt"] = "api:dx12\n",
        ["horizon-mods.txt"] = "key.horizonmatica.rotate:R\n",
        ["config/horizon_physics.txt"] = "setup_done=1\n",
        ["config/tectonic-horizon.json"] = "{\"personal\":true}",
        ["saves/BootstrapUpdateQa/level.dat"] = "Synthetic world preservation marker",
        ["saves/BootstrapUpdateQa/playerdata/qa.dat"] = "Synthetic player preservation marker",
        ["backups/personal.zip"] = "Synthetic backup preservation marker"
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
        && updated.FilesChanged == 2, "The update must replace the bootstrap and its base provenance only.");
    await instances.PrepareAsync(pack);
    foreach (var file in newRelease.Manifest.Files)
    {
        var path = Path.Combine(canonical, file.Path);
        Check(new FileInfo(path).Length == file.SizeBytes && Hash(path) == file.Sha256,
            "Installed file differs from the exact release: " + file.Path);
    }
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
    report["personalFilesPreserved"] = sentinels.Keys;
    report["update"] = "PASS";
    report["idempotence"] = "PASS";

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
            Check(card.VersionLabel == "Minecraft 26.3 (40 модов)" && card.Summary == "",
                "The card must show just Minecraft version and mod count.");
            Check(card.WebsiteUri is null && content.NativeProject?.WebsiteUrl is null
                && !card.Description.Contains("Точный состав", StringComparison.Ordinal),
                "The removed project URL or trailing composition sentence is still present.");
            Check(card.ModCount == 40 && card.Content.Mods.All(mod => mod.WebsiteUrl is not null),
                "All 40 mods must retain their own official project pages.");
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

static string Hash(string path) => Convert.ToHexStringLower(SHA256.HashData(File.ReadAllBytes(path)));
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
sealed record Manifest(string PackId, string RelativePath, string Revision, FileEntry[] Files, Asset[] Assets, Asset Library);
sealed record Release(string Root, Manifest Manifest)
{
    public string Library => Path.Combine(Root, "assets", Manifest.Library.Name);
    public static Release Read(string directory) => new(Path.GetFullPath(directory),
        JsonSerializer.Deserialize<Manifest>(File.ReadAllBytes(Path.Combine(directory, "pack-manifest.json")),
            new JsonSerializerOptions(JsonSerializerDefaults.Web))!);
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
