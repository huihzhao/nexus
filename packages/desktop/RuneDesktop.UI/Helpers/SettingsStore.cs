// SPDX-License-Identifier: Apache-2.0
//
// SettingsStore — single source of truth for the desktop's persistent
// user-level settings (currently just `ServerUrl`, but the schema is
// extensible without breaking older installs).
//
// Why this exists
// ===============
// Pre-Welcome-wizard the desktop read its server URL from
// `appsettings.json` next to the .exe (i.e. inside the install
// directory). That works for developers running `dotnet run` but is
// hostile to packaged installs:
//   * The .app / .msi install dir is read-only or admin-write.
//   * Every reinstall would clobber the user's choice.
//   * No first-run UX path — users would have to manually edit the
//     JSON inside the bundle.
//
// SettingsStore moves this to the per-user data directory:
//   macOS   → ~/Library/Application Support/RuneProtocol/settings.json
//   Linux   → ~/.config/RuneProtocol/settings.json (via XDG default)
//   Windows → %APPDATA%/RuneProtocol/settings.json
//
// Same parent directory as `SessionPrefs` — keeps everything user-
// scoped in one place. Format is JSON for the future-proofing reason
// above; the wire shape is the `Settings` record below.

using System;
using System.IO;
using System.Text.Json;

namespace RuneDesktop.UI.Helpers;

/// <summary>Persistent, schema-versioned, JSON-serialisable user
/// settings. Add fields freely; missing fields fall back to defaults
/// on load so old installs never break.</summary>
public sealed class Settings
{
    /// <summary>The Nexus server URL the desktop talks to. Empty
    /// string means "first run — show the Welcome wizard".</summary>
    public string ServerUrl { get; set; } = "";

    /// <summary>When true, the desktop's HttpClient accepts
    /// self-signed TLS certs (skips system trust-store check).
    /// Required for dev deployments using
    /// ``scripts/generate_self_signed_cert.sh``; should be false for
    /// production / Let's Encrypt setups. The Welcome wizard exposes
    /// this as a checkbox the user explicitly opts into.</summary>
    public bool AcceptSelfSignedCert { get; set; } = false;

    /// <summary>Schema version. Bump when fields change incompatibly
    /// so an older release reading a newer file falls back gracefully
    /// instead of crashing on missing properties.</summary>
    public int Version { get; set; } = 1;
}

public static class SettingsStore
{
    private static readonly JsonSerializerOptions _opts = new()
    {
        WriteIndented = true,
        PropertyNamingPolicy = null,  // PascalCase on disk — matches schema
    };

    /// <summary>Per-user RuneProtocol app-data directory. Created on
    /// first access. Never throws — falls back to current directory if
    /// the standard location is somehow unwritable.</summary>
    public static string Dir
    {
        get
        {
            try
            {
                var baseDir = Environment.GetFolderPath(
                    Environment.SpecialFolder.ApplicationData);
                // Fully-qualify `System.IO.Path.Combine` here because we
                // expose a `FilePath` property on this class — even though
                // it doesn't collide directly, fully-qualifying avoids
                // any future surprise if someone adds a `Path` member.
                var dir = System.IO.Path.Combine(baseDir, "RuneProtocol");
                Directory.CreateDirectory(dir);
                return dir;
            }
            catch
            {
                return AppContext.BaseDirectory;
            }
        }
    }

    /// <summary>Absolute path to settings.json. Property is named
    /// `FilePath` rather than `Path` so it doesn't shadow
    /// <see cref="System.IO.Path"/> inside this class — that shadow
    /// caused a CS1061 build break in earlier rev because every
    /// `Path.Combine(...)` call inside the class then resolved to
    /// `string.Combine` (which doesn't exist).</summary>
    public static string FilePath => System.IO.Path.Combine(Dir, "settings.json");

    /// <summary>Load settings from disk. Returns a fresh default
    /// Settings if the file doesn't exist or is corrupted.</summary>
    public static Settings Load()
    {
        try
        {
            if (!File.Exists(FilePath)) return new Settings();
            var raw = File.ReadAllText(FilePath);
            var loaded = JsonSerializer.Deserialize<Settings>(raw, _opts);
            return loaded ?? new Settings();
        }
        catch (Exception ex)
        {
            // Don't crash the app on a corrupted prefs file — just
            // start fresh. The Welcome wizard will guide them again.
            System.Diagnostics.Debug.WriteLine(
                $"SettingsStore.Load failed: {ex.Message}");
            return new Settings();
        }
    }

    /// <summary>Persist settings to disk. Best-effort — failure to
    /// write doesn't propagate (the caller has already updated the
    /// in-memory state, so the user isn't blocked; we just lose the
    /// persistence side effect).</summary>
    public static void Save(Settings settings)
    {
        try
        {
            var json = JsonSerializer.Serialize(settings, _opts);
            File.WriteAllText(FilePath, json);
        }
        catch (Exception ex)
        {
            System.Diagnostics.Debug.WriteLine(
                $"SettingsStore.Save failed: {ex.Message}");
        }
    }

    /// <summary>True when the user has completed the Welcome wizard
    /// (server URL is set + non-empty). Drives the MainViewModel's
    /// "show Welcome vs show Login" branch on startup.</summary>
    public static bool IsConfigured(Settings? s = null)
    {
        s ??= Load();
        return !string.IsNullOrWhiteSpace(s.ServerUrl);
    }

    /// <summary>Convenience: return the configured URL, or empty
    /// string if first-run.</summary>
    public static string GetServerUrl() => Load().ServerUrl;

    /// <summary>Convenience: persist a new server URL without having
    /// to load + mutate + save manually.</summary>
    public static void SetServerUrl(string url)
    {
        var s = Load();
        s.ServerUrl = (url ?? "").Trim();
        Save(s);
    }
}
