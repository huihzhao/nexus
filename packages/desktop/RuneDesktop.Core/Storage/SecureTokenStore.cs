using System.Security.Cryptography;
using System.Text;

namespace RuneDesktop.Core.Storage;

/// <summary>
/// Interface for secure token and credential storage.
/// Implementations may use platform-specific secure storage (Keychain, DPAPI, etc).
/// </summary>
public interface ISecureStore
{
    /// <summary>
    /// Retrieves a secure value by key.
    /// </summary>
    /// <param name="key">The storage key.</param>
    /// <returns>The stored value, or null if not found.</returns>
    Task<string?> GetAsync(string key);

    /// <summary>
    /// Stores a secure value with a key.
    /// </summary>
    /// <param name="key">The storage key.</param>
    /// <param name="value">The value to store.</param>
    /// <returns>A task that completes when the value is stored.</returns>
    Task SetAsync(string key, string value);

    /// <summary>
    /// Deletes a stored value by key.
    /// </summary>
    /// <param name="key">The storage key.</param>
    /// <returns>A task that completes when the value is deleted.</returns>
    Task DeleteAsync(string key);

    /// <summary>
    /// Checks if a key exists in storage.
    /// </summary>
    /// <param name="key">The storage key.</param>
    /// <returns>True if the key exists, false otherwise.</returns>
    Task<bool> ExistsAsync(string key);
}

/// <summary>
/// File-based encrypted token store using AES-256-GCM encryption.
/// Suitable for all platforms (macOS, Windows, Linux).
/// For production, consider using platform-specific secure stores (Keychain, DPAPI, Secret Service).
/// </summary>
public class FileBasedSecureStore : ISecureStore
{
    private readonly string _storePath;
    private readonly byte[] _encryptionKey;
    private static readonly object _lockObject = new();

    /// <summary>
    /// Initializes a new file-based secure store.
    /// Derives a encryption key from machine-specific data for automatic protection.
    /// </summary>
    /// <param name="storePath">Path to directory where encrypted tokens will be stored.</param>
    public FileBasedSecureStore(string storePath)
    {
        _storePath = storePath;
        Directory.CreateDirectory(_storePath);
        _encryptionKey = DeriveEncryptionKey();
    }

    /// <summary>
    /// Retrieves a secure value by key.
    /// </summary>
    public async Task<string?> GetAsync(string key)
    {
        var filePath = GetFilePath(key);

        if (!File.Exists(filePath))
            return null;

        try
        {
            lock (_lockObject)
            {
                var encryptedData = File.ReadAllBytes(filePath);
                var decrypted = DecryptAes256Gcm(encryptedData, _encryptionKey);
                return Encoding.UTF8.GetString(decrypted);
            }
        }
        catch (Exception ex)
        {
            throw new InvalidOperationException($"Failed to retrieve secure value for key '{key}': {ex.Message}", ex);
        }
    }

    /// <summary>
    /// Stores a secure value with a key.
    /// </summary>
    public async Task SetAsync(string key, string value)
    {
        var filePath = GetFilePath(key);

        try
        {
            lock (_lockObject)
            {
                var plaintext = Encoding.UTF8.GetBytes(value);
                var encrypted = EncryptAes256Gcm(plaintext, _encryptionKey);
                File.WriteAllBytes(filePath, encrypted);
            }
        }
        catch (Exception ex)
        {
            throw new InvalidOperationException($"Failed to store secure value for key '{key}': {ex.Message}", ex);
        }
    }

    /// <summary>
    /// Deletes a stored value by key.
    /// </summary>
    public async Task DeleteAsync(string key)
    {
        var filePath = GetFilePath(key);

        try
        {
            lock (_lockObject)
            {
                if (File.Exists(filePath))
                {
                    File.Delete(filePath);
                }
            }
        }
        catch (Exception ex)
        {
            throw new InvalidOperationException($"Failed to delete secure value for key '{key}': {ex.Message}", ex);
        }
    }

    /// <summary>
    /// Checks if a key exists in storage.
    /// </summary>
    public async Task<bool> ExistsAsync(string key)
    {
        var filePath = GetFilePath(key);
        return File.Exists(filePath);
    }

    /// <summary>
    /// Gets the file path for a given key.
    /// Sanitizes the key to create a safe filename.
    /// </summary>
    private string GetFilePath(string key)
    {
        var sanitized = SanitizeKey(key);
        return Path.Combine(_storePath, $"{sanitized}.dat");
    }

    /// <summary>
    /// Sanitizes a key to create a safe filename.
    /// </summary>
    private static string SanitizeKey(string key)
    {
        var chars = Path.GetInvalidFileNameChars();
        var sanitized = new StringBuilder();

        foreach (var c in key)
        {
            if (chars.Contains(c) || c == '.' || c == '/')
            {
                sanitized.Append('_');
            }
            else
            {
                sanitized.Append(c);
            }
        }

        return sanitized.ToString();
    }

    /// <summary>
    /// Derives an encryption key from machine-specific data.
    /// Uses SHA256 hash of a combination of machine ID and application name.
    /// </summary>
    private static byte[] DeriveEncryptionKey()
    {
        var machineId = GetMachineId();
        var appName = "RuneDesktop";
        var combined = $"{machineId}:{appName}";
        var hash = SHA256.HashData(Encoding.UTF8.GetBytes(combined));
        return hash;
    }

    /// <summary>
    /// Gets a machine-specific identifier.
    /// On Linux: uses /etc/machine-id or hostname.
    /// On Windows: uses HKEY_LOCAL_MACHINE registry key.
    /// On macOS: uses hardware UUID.
    /// </summary>
    private static string GetMachineId()
    {
        try
        {
            if (OperatingSystem.IsLinux())
            {
                if (File.Exists("/etc/machine-id"))
                    return File.ReadAllText("/etc/machine-id").Trim();

                return Environment.MachineName;
            }

            if (OperatingSystem.IsWindows())
            {
                return Environment.MachineName + Environment.ProcessorCount;
            }

            if (OperatingSystem.IsMacOS())
            {
                return Environment.MachineName + Environment.ProcessorCount;
            }

            return Environment.MachineName;
        }
        catch
        {
            return Environment.MachineName;
        }
    }

    /// <summary>
    /// Encrypts data using AES-256-GCM.
    /// Returns: [12-byte nonce][16-byte tag][encrypted data].
    /// </summary>
    private static byte[] EncryptAes256Gcm(byte[] plaintext, byte[] key)
    {
        using var aes = new AesGcm(key, 96);

        var nonce = new byte[12];
        using (var rng = RandomNumberGenerator.Create())
        {
            rng.GetBytes(nonce);
        }

        var ciphertext = new byte[plaintext.Length];
        var tag = new byte[16];

        aes.Encrypt(nonce, plaintext, ciphertext, tag);

        var result = new byte[nonce.Length + tag.Length + ciphertext.Length];
        Buffer.BlockCopy(nonce, 0, result, 0, nonce.Length);
        Buffer.BlockCopy(tag, 0, result, nonce.Length, tag.Length);
        Buffer.BlockCopy(ciphertext, 0, result, nonce.Length + tag.Length, ciphertext.Length);

        return result;
    }

    /// <summary>
    /// Decrypts data previously encrypted with AES-256-GCM.
    /// Expects: [12-byte nonce][16-byte tag][encrypted data].
    /// </summary>
    private static byte[] DecryptAes256Gcm(byte[] encryptedData, byte[] key)
    {
        if (encryptedData.Length < 28)
            throw new InvalidOperationException("Encrypted data is too short");

        using var aes = new AesGcm(key, 96);

        var nonce = new byte[12];
        var tag = new byte[16];
        var ciphertext = new byte[encryptedData.Length - 28];

        Buffer.BlockCopy(encryptedData, 0, nonce, 0, 12);
        Buffer.BlockCopy(encryptedData, 12, tag, 0, 16);
        Buffer.BlockCopy(encryptedData, 28, ciphertext, 0, ciphertext.Length);

        var plaintext = new byte[ciphertext.Length];
        aes.Decrypt(nonce, ciphertext, tag, plaintext);

        return plaintext;
    }
}

/// <summary>
/// Simple in-memory secure store for testing and temporary storage.
/// WARNING: This is not suitable for production as values are not persisted
/// and are held in application memory.
/// </summary>
public class InMemorySecureStore : ISecureStore
{
    private readonly Dictionary<string, string> _store = [];
    private static readonly object _lockObject = new();

    /// <summary>
    /// Retrieves a value from in-memory storage.
    /// </summary>
    public async Task<string?> GetAsync(string key)
    {
        lock (_lockObject)
        {
            return _store.TryGetValue(key, out var value) ? value : null;
        }
    }

    /// <summary>
    /// Stores a value in memory.
    /// </summary>
    public async Task SetAsync(string key, string value)
    {
        lock (_lockObject)
        {
            _store[key] = value;
        }
    }

    /// <summary>
    /// Deletes a value from in-memory storage.
    /// </summary>
    public async Task DeleteAsync(string key)
    {
        lock (_lockObject)
        {
            _store.Remove(key);
        }
    }

    /// <summary>
    /// Checks if a key exists in in-memory storage.
    /// </summary>
    public async Task<bool> ExistsAsync(string key)
    {
        lock (_lockObject)
        {
            return _store.ContainsKey(key);
        }
    }

    /// <summary>
    /// Clears all in-memory values.
    /// </summary>
    public void Clear()
    {
        lock (_lockObject)
        {
            _store.Clear();
        }
    }
}
