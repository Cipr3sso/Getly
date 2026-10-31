using System;
using System.Diagnostics;
using System.IO;
using System.Threading;
using System.Threading.Tasks;

namespace Getly.UI.Services;

/// <summary>
/// Supervises the background Python engine process (engine_service.py).
/// Enforces anti-zombie lifetime binding via Windows Job Object, redirects diagnostic logs asynchronously,
/// and handles controlled startup and teardown without deadlocks.
/// </summary>
public sealed class EngineSupervisor : IDisposable
{
    private readonly JobObjectManager _jobObject;
    private Process? _engineProcess;
    private bool _disposed;

    public string PipeName { get; }
    public bool IsEngineRunning => _engineProcess is not null && !_engineProcess.HasExited;
    public int? ProcessId => _engineProcess?.Id;

    public event Action? OnEngineStarted;
    public event Action<int>? OnEngineExited;
    public event Action<string>? OnLogReceived;

    public EngineSupervisor(string pipeName = @"\\.\pipe\GetlyEngine")
    {
        PipeName = pipeName;
        _jobObject = new JobObjectManager();
    }

    /// <summary>
    /// Launches the Python headless engine service and assigns it to the Job Object.
    /// </summary>
    public async Task<bool> StartEngineAsync(CancellationToken cancellationToken = default)
    {
        if (IsEngineRunning)
        {
            return true;
        }

        string repoRoot = FindRepositoryRoot();
        string pythonExe = FindPythonExecutable(repoRoot);
        string engineScript = Path.Combine(repoRoot, "engine_service.py");

        if (!File.Exists(engineScript))
        {
            throw new FileNotFoundException($"Impossibile trovare l'entrypoint headless: {engineScript}");
        }

        var startInfo = new ProcessStartInfo
        {
            FileName = pythonExe,
            Arguments = $"\"{engineScript}\" --pipe \"{PipeName}\"",
            WorkingDirectory = repoRoot,
            UseShellExecute = false,
            CreateNoWindow = true,
            ErrorDialog = false,
            RedirectStandardOutput = true,
            RedirectStandardError = true,
        };

        startInfo.EnvironmentVariables["PYTHONUNBUFFERED"] = "1";
        startInfo.EnvironmentVariables["PYTHONPATH"] = repoRoot;

        var process = new Process { StartInfo = startInfo, EnableRaisingEvents = true };

        process.OutputDataReceived += (_, e) =>
        {
            if (!string.IsNullOrEmpty(e.Data))
                OnLogReceived?.Invoke($"[ENGINE] {e.Data}");
        };

        process.ErrorDataReceived += (_, e) =>
        {
            if (!string.IsNullOrEmpty(e.Data))
                OnLogReceived?.Invoke($"[ENGINE-ERR] {e.Data}");
        };

        process.Exited += (_, _) =>
        {
            int exitCode = -1;
            try { exitCode = process.ExitCode; } catch { }
            OnLogReceived?.Invoke($"[ENGINE] Processo terminato con codice {exitCode}");
            OnEngineExited?.Invoke(exitCode);
        };

        if (!process.Start())
        {
            return false;
        }

        // Bind immediately to Windows Job Object
        _jobObject.AssignProcess(process);

        // Begin asynchronous reads to prevent buffer deadlocks
        process.BeginOutputReadLine();
        process.BeginErrorReadLine();

        _engineProcess = process;
        OnEngineStarted?.Invoke();

        // Brief delay to allow pipe creation
        await Task.Delay(350, cancellationToken);
        return true;
    }

    /// <summary>
    /// Gracefully stops the engine process with timeout and fallback kill.
    /// </summary>
    public async Task StopEngineAsync(int timeoutMs = 2000)
    {
        if (_engineProcess is null)
        {
            return;
        }

        try
        {
            if (!_engineProcess.HasExited)
            {
                using var cts = new CancellationTokenSource(timeoutMs);
                try
                {
                    _engineProcess.Kill(entireProcessTree: true);
                    await _engineProcess.WaitForExitAsync(cts.Token);
                }
                catch { }
            }
        }
        catch (Exception ex)
        {
            OnLogReceived?.Invoke($"[ENGINE-ERR] Errore durante l'arresto del processo: {ex.Message}");
        }
        finally
        {
            try { _engineProcess.Dispose(); } catch { }
            _engineProcess = null;
        }
    }

    /// <summary>
    /// Locates the repository root by walking up from the base directory.
    /// </summary>
    public static string FindRepositoryRoot()
    {
        string dir = AppContext.BaseDirectory;
        while (!string.IsNullOrEmpty(dir))
        {
            if (File.Exists(Path.Combine(dir, "engine_service.py")) && Directory.Exists(Path.Combine(dir, "core")))
            {
                return Path.GetFullPath(dir);
            }

            var parent = Directory.GetParent(dir);
            if (parent is null) break;
            dir = parent.FullName;
        }

        string cur = Directory.GetCurrentDirectory();
        while (!string.IsNullOrEmpty(cur))
        {
            if (File.Exists(Path.Combine(cur, "engine_service.py")) || File.Exists(Path.Combine(cur, "pyproject.toml")))
            {
                return Path.GetFullPath(cur);
            }

            var parent = Directory.GetParent(cur);
            if (parent is null) break;
            cur = parent.FullName;
        }

        return AppContext.BaseDirectory;
    }

    /// <summary>
    /// Finds the exact Python executable, prioritizing virtual environments (.venv)
    /// and falling back to real python.exe paths without triggering Windows Store redirects.
    /// </summary>
    public static string FindPythonExecutable(string? repoRoot = null)
    {
        repoRoot ??= FindRepositoryRoot();

        // 1. Virtual Environment in repo (.venv or venv)
        string[] venvCandidates =
        [
            Path.Combine(repoRoot, ".venv", "Scripts", "python.exe"),
            Path.Combine(repoRoot, "venv", "Scripts", "python.exe"),
            Path.GetFullPath(Path.Combine(AppContext.BaseDirectory, "../../../../../.venv/Scripts/python.exe")),
            Path.GetFullPath(Path.Combine(AppContext.BaseDirectory, "../../../../.venv/Scripts/python.exe")),
            Path.GetFullPath(Path.Combine(AppContext.BaseDirectory, "../../../.venv/Scripts/python.exe")),
        ];

        foreach (string candidate in venvCandidates)
        {
            if (File.Exists(candidate))
                return Path.GetFullPath(candidate);
        }

        // 2. Active VIRTUAL_ENV environment variable
        string? envVenv = Environment.GetEnvironmentVariable("VIRTUAL_ENV");
        if (!string.IsNullOrEmpty(envVenv))
        {
            string envPy = Path.Combine(envVenv, "Scripts", "python.exe");
            if (File.Exists(envPy))
                return Path.GetFullPath(envPy);
        }

        // 3. Fallback to real python.exe resolved via where.exe python (ignoring WindowsApps stubs)
        try
        {
            var startInfo = new ProcessStartInfo
            {
                FileName = "where.exe",
                Arguments = "python",
                UseShellExecute = false,
                RedirectStandardOutput = true,
                RedirectStandardError = true,
                CreateNoWindow = true
            };
            using var proc = Process.Start(startInfo);
            if (proc != null)
            {
                string output = proc.StandardOutput.ReadToEnd();
                proc.WaitForExit(1000);
                var lines = output.Split(new[] { '\r', '\n' }, StringSplitOptions.RemoveEmptyEntries);
                foreach (string line in lines)
                {
                    string trimmed = line.Trim();
                    if (File.Exists(trimmed) && !trimmed.Contains("WindowsApps", StringComparison.OrdinalIgnoreCase))
                    {
                        return Path.GetFullPath(trimmed);
                    }
                }
            }
        }
        catch { }

        // 4. Known system installations
        string localAppData = Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData);
        string[] standardPaths =
        [
            Path.Combine(localAppData, "Programs", "Python", "Python310", "python.exe"),
            Path.Combine(localAppData, "Programs", "Python", "Python311", "python.exe"),
            Path.Combine(localAppData, "Programs", "Python", "Python312", "python.exe"),
            Path.Combine(localAppData, "Python", "bin", "python.exe")
        ];

        foreach (string path in standardPaths)
        {
            if (File.Exists(path))
                return Path.GetFullPath(path);
        }

        // 5. Default fallback
        return "python.exe";
    }

    public void Dispose()
    {
        if (!_disposed)
        {
            try
            {
                if (_engineProcess is not null && !_engineProcess.HasExited)
                {
                    _engineProcess.Kill(entireProcessTree: true);
                }
            }
            catch { }

            _jobObject.Dispose();
            _engineProcess?.Dispose();
            _engineProcess = null;
            _disposed = true;
        }
    }
}
