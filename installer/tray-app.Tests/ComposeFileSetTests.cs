using RecallHub.TrayApp;
using Xunit;

namespace RecallHub.TrayApp.Tests;

/// <summary>
/// ServiceManager.ResolveComposeFiles / BuildComposeArgs against a temporary install
/// folder. An explicit -f list makes Compose ignore COMPOSE_FILE from .env, so the tray
/// app has to pass the files listed there itself (in that order), keep the old
/// compose file + override + shares set without COMPOSE_FILE, and never fail a start on
/// a listed file that is missing. installer/tests covers the PowerShell twin
/// (Get-ComposeFileSet, Start-Services.ps1 / Stop-Services.ps1 -DryRun).
/// </summary>
public sealed class ComposeFileSetTests : IDisposable
{
    private static readonly Func<string, string?> NoEnvironment = _ => null;

    private readonly string _install;
    private readonly List<string> _warnings = new();

    public ComposeFileSetTests()
    {
        _install = Path.Combine(Path.GetTempPath(), "rh-tray-tests-" + Guid.NewGuid().ToString("N")[..8]);
        Directory.CreateDirectory(_install);
        foreach (var name in new[] { "docker-compose.yml", "docker-compose.override.yml", "docker-compose.shares.yml", "docker-compose.gpu.yml" })
            File.WriteAllText(P(name), "services: {}\n");
    }

    public void Dispose() => Directory.Delete(_install, recursive: true);

    private string P(string name) => Path.Combine(_install, name);

    private void WriteEnv(string content) => File.WriteAllText(P(".env"), content);

    private IReadOnlyList<string> Resolve(string composeFile = "docker-compose.yml")
        => ServiceManager.ResolveComposeFiles(P(composeFile), _install, _warnings.Add, NoEnvironment);

    [Fact]
    public void WithoutComposeFile_UsesComposeFilePlusOverrideAndShares()
    {
        WriteEnv("COMPOSE_PROFILES=quellex\n");

        Assert.Equal(new[] { P("docker-compose.yml"), P("docker-compose.override.yml"), P("docker-compose.shares.yml") }, Resolve());
        Assert.Empty(_warnings);
    }

    [Fact]
    public void WithoutEnvFile_UsesTheDefaultSet_AndSkipsExtrasThatAreMissing()
    {
        File.Delete(P("docker-compose.shares.yml"));

        Assert.Equal(new[] { P("docker-compose.yml"), P("docker-compose.override.yml") }, Resolve());
        Assert.Empty(_warnings);
    }

    [Fact]
    public void ComposeFile_IsPassedInItsOwnOrder()
    {
        WriteEnv("COMPOSE_PROFILES=quellex\nCOMPOSE_FILE=docker-compose.yml;docker-compose.shares.yml;docker-compose.override.yml;docker-compose.gpu.yml\n");

        Assert.Equal(
            new[] { P("docker-compose.yml"), P("docker-compose.shares.yml"), P("docker-compose.override.yml"), P("docker-compose.gpu.yml") },
            Resolve());
        Assert.Empty(_warnings);
    }

    [Fact]
    public void MissingEntries_AreReportedAndSkipped()
    {
        WriteEnv("COMPOSE_FILE=docker-compose.yml;docker-compose.gpu.yml;docker-compose.missing.yml\n");

        Assert.Equal(new[] { P("docker-compose.yml"), P("docker-compose.gpu.yml") }, Resolve());
        var warning = Assert.Single(_warnings);
        Assert.Contains("docker-compose.missing.yml", warning);
        Assert.Contains("skipped", warning);
    }

    [Fact]
    public void OnlyMissingEntries_FallBackToTheDefaultSet()
    {
        WriteEnv("COMPOSE_FILE=nope.yml;nada.yml\n");

        Assert.Equal(new[] { P("docker-compose.yml"), P("docker-compose.override.yml"), P("docker-compose.shares.yml") }, Resolve());
        Assert.Equal(3, _warnings.Count);
        Assert.Contains("default compose file set", _warnings[^1]);
    }

    [Fact]
    public void CustomSeparator_ExportPrefix_AndQuotes_AreHonoured()
    {
        WriteEnv("COMPOSE_PATH_SEPARATOR=:\nexport COMPOSE_FILE=\"docker-compose.yml:docker-compose.gpu.yml\"\n");

        Assert.Equal(new[] { P("docker-compose.yml"), P("docker-compose.gpu.yml") }, Resolve());
        Assert.Empty(_warnings);
    }

    [Fact]
    public void LastDefinitionWins_AndCommentsAreIgnored()
    {
        WriteEnv("# COMPOSE_FILE=docker-compose.yml;docker-compose.override.yml\n"
            + "COMPOSE_FILE=docker-compose.yml\n"
            + "COMPOSE_FILE=docker-compose.yml;docker-compose.gpu.yml\n");

        Assert.Equal(new[] { P("docker-compose.yml"), P("docker-compose.gpu.yml") }, Resolve());
    }

    [Fact]
    public void EmptyComposeFile_CountsAsUnset()
    {
        WriteEnv("COMPOSE_FILE=\n");

        Assert.Equal(new[] { P("docker-compose.yml"), P("docker-compose.override.yml"), P("docker-compose.shares.yml") }, Resolve());
        Assert.Empty(_warnings);
    }

    [Fact]
    public void ProcessEnvironment_TakesPrecedenceOverDotEnv()
    {
        WriteEnv("COMPOSE_FILE=docker-compose.yml;docker-compose.override.yml\n");

        var files = ServiceManager.ResolveComposeFiles(P("docker-compose.yml"), _install, _warnings.Add,
            key => key == "COMPOSE_FILE" ? "docker-compose.yml;docker-compose.gpu.yml" : null);

        Assert.Equal(new[] { P("docker-compose.yml"), P("docker-compose.gpu.yml") }, files);
    }

    [Fact]
    public void AbsoluteEntries_AreKept_AndDuplicatesDropped()
    {
        WriteEnv($"COMPOSE_FILE=docker-compose.yml;{P("docker-compose.gpu.yml")};docker-compose.gpu.yml\n");

        Assert.Equal(new[] { P("docker-compose.yml"), P("docker-compose.gpu.yml") }, Resolve());
    }

    [Fact]
    public void OtherComposeFile_IgnoresComposeFileAndTheExtras()
    {
        File.WriteAllText(P("docker-compose.custom.yml"), "services: {}\n");
        WriteEnv("COMPOSE_FILE=docker-compose.yml;docker-compose.gpu.yml\n");

        Assert.Equal(new[] { P("docker-compose.custom.yml") }, Resolve("docker-compose.custom.yml"));
        Assert.Empty(_warnings);
    }

    [Fact]
    public void BuildComposeArgs_QuotesEveryFileAndAppendsTheProfiles()
    {
        var args = ServiceManager.BuildComposeArgs(
            new[] { @"C:\Quellex\docker-compose.yml", @"C:\Quellex\docker-compose.gpu.yml" },
            new[] { "quellex", "embedder-gpu" });

        Assert.Equal("compose -f \"C:\\Quellex\\docker-compose.yml\" -f \"C:\\Quellex\\docker-compose.gpu.yml\" --profile quellex --profile embedder-gpu", args);
    }

    [Fact]
    public void ReadDotEnvValue_IsNullWithoutFileOrKey()
    {
        Assert.Null(ServiceManager.ReadDotEnvValue(P(".env"), "COMPOSE_FILE"));
        WriteEnv("COMPOSE_PROFILES=quellex\n");
        Assert.Null(ServiceManager.ReadDotEnvValue(P(".env"), "COMPOSE_FILE"));
        Assert.Equal("quellex", ServiceManager.ReadDotEnvValue(P(".env"), "COMPOSE_PROFILES"));
    }
}
