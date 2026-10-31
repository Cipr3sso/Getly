using System;
using System.Collections.Concurrent;
using System.Collections.Generic;
using System.IO;
using System.IO.Pipes;
using System.Text;
using System.Text.Json;
using System.Threading;
using System.Threading.Tasks;
using Getly.UI.Models;

namespace Getly.UI.Services;

/// <summary>
/// Reactive Windows Named Pipe IPC Client for communicating with the Getly headless engine.
/// Provides Line-Delimited JSON (NDJSON) streaming, RPC request-response correlation,
/// and live 10 Hz telemetry dispatch.
/// </summary>
public sealed class IpcClient : IAsyncDisposable, IDisposable
{
    private readonly string _pipeName;
    private NamedPipeClientStream? _pipeStream;
    private StreamWriter? _writer;
    private StreamReader? _reader;
    private CancellationTokenSource? _cts;
    private Task? _readLoopTask;
    private long _requestIdCounter;

    private readonly ConcurrentDictionary<string, TaskCompletionSource<JsonElement>> _pendingRequests = new();

    public bool IsConnected => _pipeStream is { IsConnected: true };

    public event Action? OnConnected;
    public event Action? OnDisconnected;
    public event Action<TelemetryTickDto>? OnTelemetryReceived;
    public event Action<string>? OnLog;

    public IpcClient(string pipeName = "GetlyEngine")
    {
        // Strip \\.\pipe\ prefix if present for NamedPipeClientStream constructor
        _pipeName = pipeName.StartsWith(@"\\.\pipe\", StringComparison.OrdinalIgnoreCase)
            ? pipeName.Substring(@"\\.\pipe\".Length)
            : pipeName;
    }

    /// <summary>
    /// Connects to the named pipe with explicit timeout and starts background reading.
    /// Retries transparently until the pipe server is initialized or timeout expires.
    /// If the pipe does not respond within the timeout, raises an explicit TimeoutException.
    /// </summary>
    public async Task ConnectAsync(TimeSpan timeout, CancellationToken cancellationToken = default)
    {
        if (IsConnected)
            return;

        _cts = new CancellationTokenSource();
        using var timeoutCts = new CancellationTokenSource(timeout);
        using var linkedCts = CancellationTokenSource.CreateLinkedTokenSource(cancellationToken, timeoutCts.Token, _cts.Token);

        OnLog?.Invoke($"[IPC] Connessione a \\\\.\\pipe\\{_pipeName} (timeout {timeout.TotalSeconds:F1}s)...");

        var stopwatch = System.Diagnostics.Stopwatch.StartNew();
        Exception? lastException = null;

        while (!linkedCts.Token.IsCancellationRequested && stopwatch.Elapsed < timeout)
        {
            try
            {
                var pipe = new NamedPipeClientStream(".", _pipeName, PipeDirection.InOut, PipeOptions.Asynchronous);
                int remainingMs = Math.Max(100, (int)(timeout - stopwatch.Elapsed).TotalMilliseconds);
                await pipe.ConnectAsync(remainingMs, linkedCts.Token);

                _pipeStream = pipe;
                _writer = new StreamWriter(_pipeStream, new UTF8Encoding(false), leaveOpen: true) { AutoFlush = true };
                _reader = new StreamReader(_pipeStream, Encoding.UTF8, detectEncodingFromByteOrderMarks: false, leaveOpen: true);

                _readLoopTask = Task.Run(() => ReadLoopAsync(_cts.Token));
                OnLog?.Invoke($"[IPC] Connesso con successo al server Named Pipe.");
                OnConnected?.Invoke();
                return;
            }
            catch (OperationCanceledException) when (timeoutCts.IsCancellationRequested)
            {
                break;
            }
            catch (Exception ex)
            {
                lastException = ex;
                try
                {
                    await Task.Delay(150, linkedCts.Token);
                }
                catch (OperationCanceledException)
                {
                    break;
                }
            }
        }

        throw new TimeoutException($"Timeout durante la connessione alla Named Pipe '{_pipeName}' ({timeout.TotalSeconds:F1}s). Ultimo errore: {lastException?.Message}", lastException);
    }

    /// <summary>
    /// Connects to the named pipe with timeout in milliseconds.
    /// </summary>
    public Task ConnectAsync(int timeoutMs = 5000, CancellationToken cancellationToken = default)
        => ConnectAsync(TimeSpan.FromMilliseconds(timeoutMs), cancellationToken);

    /// <summary>
    /// Background loop processing incoming NDJSON messages from the pipe.
    /// </summary>
    private async Task ReadLoopAsync(CancellationToken token)
    {
        try
        {
            while (!token.IsCancellationRequested && _reader is not null)
            {
                string? line = await _reader.ReadLineAsync(token);
                if (line is null)
                {
                    // Pipe closed or server disconnected
                    break;
                }

                if (string.IsNullOrWhiteSpace(line))
                    continue;

                ProcessIncomingMessage(line);
            }
        }
        catch (OperationCanceledException) { }
        catch (IOException) { }
        catch (Exception ex)
        {
            OnLog?.Invoke($"[IPC-ERR] Errore nel ciclo di lettura: {ex.Message}");
        }
        finally
        {
            OnLog?.Invoke("[IPC] Connessione Named Pipe chiusa.");
            FailAllPendingRequests("Connessione IPC interrotta.");
            OnDisconnected?.Invoke();
        }
    }

    private void ProcessIncomingMessage(string line)
    {
        try
        {
            using var doc = JsonDocument.Parse(line);
            var root = doc.RootElement;

            // 1. Check if it's an asynchronous broadcast event (e.g. telemetry tick)
            if (root.TryGetProperty("event", out var eventProp))
            {
                string eventName = eventProp.GetString() ?? "";
                if (eventName == "tick")
                {
                    var tick = JsonSerializer.Deserialize<TelemetryTickDto>(root.GetRawText());
                    if (tick is not null)
                    {
                        OnTelemetryReceived?.Invoke(tick);
                    }
                }
                return;
            }

            // 2. Check if it's an RPC response (correlated by 'id')
            if (root.TryGetProperty("id", out var idProp))
            {
                string reqId = idProp.GetString() ?? "";
                if (!string.IsNullOrEmpty(reqId) && _pendingRequests.TryRemove(reqId, out var tcs))
                {
                    if (root.TryGetProperty("error", out var errorProp) && errorProp.ValueKind != JsonValueKind.Null)
                    {
                        string errMsg = errorProp.GetString() ?? "Errore RPC sconosciuto";
                        tcs.TrySetException(new InvalidOperationException(errMsg));
                    }
                    else if (root.TryGetProperty("result", out var resultProp))
                    {
                        tcs.TrySetResult(resultProp.Clone());
                    }
                    else
                    {
                        tcs.TrySetResult(default);
                    }
                }
            }
        }
        catch (Exception ex)
        {
            OnLog?.Invoke($"[IPC-ERR] Errore decodifica frame NDJSON: {ex.Message}");
        }
    }

    /// <summary>
    /// Sends an RPC request over the pipe and awaits the correlated response.
    /// </summary>
    public async Task<JsonElement> InvokeAsync(string method, object? parameters = null, int timeoutMs = 8000, CancellationToken cancellationToken = default)
    {
        if (_writer is null || !IsConnected)
        {
            throw new InvalidOperationException("Il client IPC non è attualmente connesso al server Named Pipe.");
        }

        string reqId = Interlocked.Increment(ref _requestIdCounter).ToString();
        var tcs = new TaskCompletionSource<JsonElement>(TaskCreationOptions.RunContinuationsAsynchronously);
        _pendingRequests[reqId] = tcs;

        var requestObj = new
        {
            id = reqId,
            method,
            @params = parameters ?? new { }
        };

        string jsonPayload = JsonSerializer.Serialize(requestObj);

        using var timeoutCts = new CancellationTokenSource(timeoutMs);
        using var linkedCts = CancellationTokenSource.CreateLinkedTokenSource(cancellationToken, timeoutCts.Token);

        try
        {
            await _writer.WriteLineAsync(jsonPayload.AsMemory(), linkedCts.Token);
            await _writer.FlushAsync(linkedCts.Token);
            return await tcs.Task.WaitAsync(linkedCts.Token);
        }
        catch (OperationCanceledException) when (timeoutCts.IsCancellationRequested)
        {
            _pendingRequests.TryRemove(reqId, out _);
            throw new TimeoutException($"Timeout della chiamata RPC '{method}' (nessuna risposta dopo {timeoutMs}ms).");
        }
        catch
        {
            _pendingRequests.TryRemove(reqId, out _);
            throw;
        }
    }

    private void FailAllPendingRequests(string reason)
    {
        foreach (var kvp in _pendingRequests)
        {
            if (_pendingRequests.TryRemove(kvp.Key, out var tcs))
            {
                tcs.TrySetException(new IOException(reason));
            }
        }
    }

    #region Strongly-Typed RPC API

    public async Task<List<TaskItemDto>> GetTasksAsync(CancellationToken cancellationToken = default)
    {
        var result = await InvokeAsync("tasks.list", null, cancellationToken: cancellationToken);
        return JsonSerializer.Deserialize<List<TaskItemDto>>(result.GetRawText()) ?? [];
    }

    public async Task<TaskItemDto?> GetTaskAsync(string taskId, CancellationToken cancellationToken = default)
    {
        var result = await InvokeAsync("task.get", new { task_id = taskId }, cancellationToken: cancellationToken);
        if (result.ValueKind == JsonValueKind.Null) return null;
        return JsonSerializer.Deserialize<TaskItemDto>(result.GetRawText());
    }

    public async Task<TaskItemDto> AddTaskAsync(
        string url,
        string? category = null,
        string? destDir = null,
        string? filename = null,
        bool startPaused = false,
        int priority = 4,
        CancellationToken cancellationToken = default)
    {
        var result = await InvokeAsync("task.add", new
        {
            url,
            category,
            dest_dir = destDir,
            filename,
            start_paused = startPaused,
            priority
        }, cancellationToken: cancellationToken);

        return JsonSerializer.Deserialize<TaskItemDto>(result.GetRawText())
            ?? throw new InvalidOperationException("Risposta non valida da task.add");
    }

    public async Task PauseTaskAsync(string taskId, CancellationToken cancellationToken = default)
    {
        await InvokeAsync("task.pause", new { task_id = taskId }, cancellationToken: cancellationToken);
    }

    public async Task ResumeTaskAsync(string taskId, CancellationToken cancellationToken = default)
    {
        await InvokeAsync("task.resume", new { task_id = taskId }, cancellationToken: cancellationToken);
    }

    public async Task DeleteTaskAsync(string taskId, bool deleteFiles = false, CancellationToken cancellationToken = default)
    {
        await InvokeAsync("task.delete", new { task_id = taskId, delete_files = deleteFiles }, cancellationToken: cancellationToken);
    }

    public async Task UpdateTaskUrlAsync(string taskId, string newUrl, bool restart = true, CancellationToken cancellationToken = default)
    {
        await InvokeAsync("task.update_url", new { task_id = taskId, new_url = newUrl, restart }, cancellationToken: cancellationToken);
    }

    public async Task SetSpeedLimitAsync(long bytesPerSec, CancellationToken cancellationToken = default)
    {
        await InvokeAsync("system.set_speed_limit", new { limit_bps = bytesPerSec }, cancellationToken: cancellationToken);
    }

    public async Task<SystemStatsDto> GetStatsAsync(CancellationToken cancellationToken = default)
    {
        var result = await InvokeAsync("system.get_stats", null, cancellationToken: cancellationToken);
        return JsonSerializer.Deserialize<SystemStatsDto>(result.GetRawText()) ?? new();
    }

    public async Task<List<TorrentFileDto>> GetTorrentFilesAsync(string taskId, CancellationToken cancellationToken = default)
    {
        var result = await InvokeAsync("torrent.get_files", new { task_id = taskId }, cancellationToken: cancellationToken);
        return JsonSerializer.Deserialize<List<TorrentFileDto>>(result.GetRawText()) ?? [];
    }

    public async Task SetTorrentPriorityAsync(string taskId, int fileIndex, int priority, CancellationToken cancellationToken = default)
    {
        await InvokeAsync("torrent.set_priority", new { task_id = taskId, file_index = fileIndex, priority }, cancellationToken: cancellationToken);
    }

    #endregion

    private bool _disposed;

    public async ValueTask DisposeAsync()
    {
        if (_disposed) return;
        _disposed = true;

        try { _cts?.Cancel(); } catch { }

        // Disposing pipeStream and reader immediately unblocks ReadLineAsync
        try { _pipeStream?.Dispose(); } catch { }
        try { _writer?.Dispose(); } catch { }
        try { _reader?.Dispose(); } catch { }

        if (_readLoopTask is not null)
        {
            try { await _readLoopTask.WaitAsync(TimeSpan.FromMilliseconds(500)); } catch { }
        }

        try { _cts?.Dispose(); } catch { }
        _cts = null;
    }

    public void Dispose()
    {
        if (_disposed) return;
        _disposed = true;

        try { _cts?.Cancel(); } catch { }
        try { _pipeStream?.Dispose(); } catch { }
        try { _writer?.Dispose(); } catch { }
        try { _reader?.Dispose(); } catch { }
        try { _cts?.Dispose(); } catch { }
        _cts = null;
    }
}
