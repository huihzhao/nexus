using System;
using System.Diagnostics;
using System.Net;
using System.Net.Sockets;
using System.Runtime.InteropServices;
using System.Text;
using System.Threading;
using System.Threading.Tasks;

namespace RuneDesktop.UI.Services;

/// <summary>
/// Service for handling passkey authentication via system browser.
/// Opens system browser to server's passkey page, starts local HTTP listener
/// to receive callback with JWT token, then closes the browser.
/// </summary>
public static class PasskeyAuthService
{
    private const string CallbackPath = "/callback";
    private static HttpListener? _listener;
    private static TaskCompletionSource<string?>? _tokenSource;
    private static readonly object _gate = new();

    /// <summary>
    /// Authenticates user with passkey using system browser.
    /// </summary>
    /// <param name="serverUrl">Base URL of the server (e.g., http://localhost:8001)</param>
    /// <returns>JWT token on success, null if cancelled</returns>
    /// <exception cref="InvalidOperationException">Thrown if listener cannot start or another flow is in flight</exception>
    /// <exception cref="OperationCanceledException">Thrown if operation times out</exception>
    public static async Task<string?> AuthenticateAsync(string serverUrl)
    {
        HttpListener listener;
        TaskCompletionSource<string?> tokenSource;

        lock (_gate)
        {
            if (_tokenSource is not null)
            {
                throw new InvalidOperationException(
                    "Another passkey authentication is already in progress.");
            }

            int callbackPort = GetAvailablePort();
            tokenSource = new TaskCompletionSource<string?>();
            listener = new HttpListener();
            listener.Prefixes.Add($"http://localhost:{callbackPort}/");

            _listener = listener;
            _tokenSource = tokenSource;
        }

        try
        {
            listener.Start();

            // The prefix we just added is "http://localhost:<port>/" — pull
            // the port back out so we can build the callback URL the browser
            // will redirect to.
            string firstPrefix = "";
            foreach (var p in listener.Prefixes) { firstPrefix = p; break; }
            var prefixUri = new Uri(firstPrefix);
            string callbackUrl = $"http://localhost:{prefixUri.Port}{CallbackPath}";
            string authUrl = $"{serverUrl.TrimEnd('/')}/auth/passkey-page?callback={Uri.EscapeDataString(callbackUrl)}";
            OpenBrowser(authUrl);

            // Start listening for callback in background
            _ = ListenForCallbackAsync(listener, tokenSource);

            // Wait for token or timeout (5 minutes)
            using var cts = new CancellationTokenSource(TimeSpan.FromMinutes(5));
            using var ctsReg = cts.Token.Register(() => tokenSource.TrySetCanceled());

            return await tokenSource.Task;
        }
        finally
        {
            lock (_gate)
            {
                try { listener.Stop(); } catch { }
                try { listener.Close(); } catch { }
                if (ReferenceEquals(_listener, listener)) _listener = null;
                if (ReferenceEquals(_tokenSource, tokenSource)) _tokenSource = null;
            }
        }
    }

    /// <summary>
    /// Cancels any in-flight passkey authentication. Safe to call when nothing
    /// is in flight — it's a no-op then.
    /// </summary>
    public static void Cancel()
    {
        TaskCompletionSource<string?>? tcs;
        HttpListener? listener;
        lock (_gate)
        {
            tcs = _tokenSource;
            listener = _listener;
        }
        tcs?.TrySetResult(null);
        try { listener?.Stop(); } catch { }
    }

    /// <summary>
    /// Listens for incoming callback with JWT token. Takes the listener and
    /// tokenSource as parameters (rather than reading the static fields) so a
    /// later AuthenticateAsync call can't accidentally retarget this loop.
    /// </summary>
    private static async Task ListenForCallbackAsync(
        HttpListener listener,
        TaskCompletionSource<string?> tokenSource)
    {
        try
        {
            while (listener.IsListening)
            {
                HttpListenerContext context;
                try
                {
                    context = await listener.GetContextAsync();
                }
                catch (ObjectDisposedException)
                {
                    // Listener was closed (timeout / cancel / normal shutdown)
                    break;
                }
                catch (HttpListenerException)
                {
                    // Stop() was called while we were awaiting GetContextAsync
                    break;
                }

                HttpListenerRequest request = context.Request;
                HttpListenerResponse response = context.Response;

                try
                {
                    // Extract token from query string
                    string? token = request.QueryString["token"];
                    string? cancelled = request.QueryString["cancelled"];

                    if (!string.IsNullOrEmpty(token))
                    {
                        // Success - send response to browser
                        SendSuccessResponse(response);
                        tokenSource.TrySetResult(token);
                        break;
                    }
                    else if (!string.IsNullOrEmpty(cancelled))
                    {
                        // User cancelled
                        SendCancelResponse(response);
                        tokenSource.TrySetResult(null);
                        break;
                    }
                    else
                    {
                        // Invalid request
                        SendErrorResponse(response, "Missing token parameter");
                        tokenSource.TrySetException(
                            new InvalidOperationException("Invalid callback: missing token")
                        );
                        break;
                    }
                }
                finally
                {
                    response.Close();
                }
            }
        }
        catch (Exception ex)
        {
            tokenSource.TrySetException(ex);
        }
    }

    /// <summary>
    /// Sends success response to browser.
    /// </summary>
    private static void SendSuccessResponse(HttpListenerResponse response)
    {
        response.StatusCode = 200;
        response.ContentType = "text/html; charset=utf-8";

        string html = """
            <!DOCTYPE html>
            <html>
            <head>
                <title>Authentication Successful</title>
                <style>
                    body {
                        font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
                        display: flex;
                        align-items: center;
                        justify-content: center;
                        height: 100vh;
                        margin: 0;
                        background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
                    }
                    .card {
                        background: white;
                        padding: 40px;
                        border-radius: 12px;
                        text-align: center;
                        box-shadow: 0 10px 40px rgba(0,0,0,0.2);
                    }
                    .checkmark {
                        width: 80px;
                        height: 80px;
                        margin: 0 auto 20px;
                        border-radius: 50%;
                        background: #34A853;
                        display: flex;
                        align-items: center;
                        justify-content: center;
                        font-size: 48px;
                        color: white;
                    }
                    h1 {
                        color: #202124;
                        margin: 0 0 10px;
                        font-size: 24px;
                    }
                    p {
                        color: #5F6368;
                        margin: 0;
                    }
                </style>
            </head>
            <body>
                <div class="card">
                    <div class="checkmark">✓</div>
                    <h1>Authentication Successful</h1>
                    <p>You can now close this window and return to Rune Protocol.</p>
                </div>
            </body>
            </html>
            """;

        byte[] buffer = Encoding.UTF8.GetBytes(html);
        response.ContentLength64 = buffer.Length;
        response.OutputStream.Write(buffer, 0, buffer.Length);
    }

    /// <summary>
    /// Sends cancellation response to browser.
    /// </summary>
    private static void SendCancelResponse(HttpListenerResponse response)
    {
        response.StatusCode = 200;
        response.ContentType = "text/html; charset=utf-8";

        string html = """
            <!DOCTYPE html>
            <html>
            <head>
                <title>Authentication Cancelled</title>
                <style>
                    body {
                        font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
                        display: flex;
                        align-items: center;
                        justify-content: center;
                        height: 100vh;
                        margin: 0;
                        background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
                    }
                    .card {
                        background: white;
                        padding: 40px;
                        border-radius: 12px;
                        text-align: center;
                        box-shadow: 0 10px 40px rgba(0,0,0,0.2);
                    }
                    .icon {
                        font-size: 48px;
                        margin-bottom: 20px;
                    }
                    h1 {
                        color: #202124;
                        margin: 0 0 10px;
                        font-size: 24px;
                    }
                    p {
                        color: #5F6368;
                        margin: 0;
                    }
                </style>
            </head>
            <body>
                <div class="card">
                    <div class="icon">✕</div>
                    <h1>Authentication Cancelled</h1>
                    <p>You can close this window and try again.</p>
                </div>
            </body>
            </html>
            """;

        byte[] buffer = Encoding.UTF8.GetBytes(html);
        response.ContentLength64 = buffer.Length;
        response.OutputStream.Write(buffer, 0, buffer.Length);
    }

    /// <summary>
    /// Sends error response to browser.
    /// </summary>
    private static void SendErrorResponse(HttpListenerResponse response, string message)
    {
        response.StatusCode = 400;
        response.ContentType = "text/html; charset=utf-8";

        string html = "<html><body style='font-family:sans-serif;display:flex;align-items:center;justify-content:center;height:100vh;margin:0'>"
            + "<div style='text-align:center'><h2>Authentication Error</h2><p>" + System.Net.WebUtility.HtmlEncode(message) + "</p></div></body></html>";

        byte[] buffer = Encoding.UTF8.GetBytes(html);
        response.ContentLength64 = buffer.Length;
        response.OutputStream.Write(buffer, 0, buffer.Length);
    }

    /// <summary>
    /// Opens URL in system default browser.
    /// </summary>
    private static void OpenBrowser(string url)
    {
        try
        {
            if (RuntimeInformation.IsOSPlatform(OSPlatform.Windows))
            {
                Process.Start(new ProcessStartInfo(url) { UseShellExecute = true });
            }
            else if (RuntimeInformation.IsOSPlatform(OSPlatform.Linux))
            {
                Process.Start("xdg-open", url);
            }
            else if (RuntimeInformation.IsOSPlatform(OSPlatform.OSX))
            {
                Process.Start("open", url);
            }
        }
        catch (Exception ex)
        {
            throw new InvalidOperationException($"Failed to open browser: {ex.Message}", ex);
        }
    }

    /// <summary>
    /// Finds an available port by binding to port 0.
    /// </summary>
    private static int GetAvailablePort()
    {
        var listener = new TcpListener(IPAddress.Loopback, 0);
        listener.Start();
        int port = ((IPEndPoint)listener.LocalEndpoint).Port;
        listener.Stop();
        return port;
    }
}
