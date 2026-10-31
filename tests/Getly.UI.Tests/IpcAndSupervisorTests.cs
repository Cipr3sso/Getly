using System;
using System.Diagnostics;
using System.IO;
using System.Text.Json;
using System.Threading;
using System.Threading.Tasks;
using Getly.UI.Models;
using Getly.UI.Services;
using Getly.UI.ViewModels;
using Xunit;

namespace Getly.UI.Tests;

public class IpcAndSupervisorTests : IDisposable
{
    private readonly CancellationTokenSource _testCts = new(TimeSpan.FromSeconds(9));

    public void Dispose()
    {
        _testCts.Cancel();
        _testCts.Dispose();
    }

    [Fact]
    public void JobObjectManager_CreateAndAssignProcess_Succeeds()
    {
        using var jobObject = new JobObjectManager();

        var startInfo = new ProcessStartInfo
        {
            FileName = "cmd.exe",
            Arguments = "/c ping 127.0.0.1 -n 3 > nul",
            CreateNoWindow = true,
            UseShellExecute = false
        };

        using var process = Process.Start(startInfo);
        Assert.NotNull(process);

        try
        {
            bool assigned = jobObject.AssignProcess(process);
            Assert.True(assigned);
        }
        finally
        {
            try
            {
                if (!process.HasExited)
                {
                    process.Kill(entireProcessTree: true);
                    process.WaitForExit(1000);
                }
            }
            catch { }
        }
    }

    [Fact]
    public void EngineSupervisor_FindPythonExecutable_ResolvesValidInterpreter()
    {
        string pythonExe = EngineSupervisor.FindPythonExecutable();
        Assert.False(string.IsNullOrWhiteSpace(pythonExe));
        Assert.True(File.Exists(pythonExe), $"L'eseguibile Python non esiste: {pythonExe}");
        Assert.DoesNotContain("WindowsApps", pythonExe, StringComparison.OrdinalIgnoreCase);
    }

    [Fact]
    public void IpcModels_SerializationAndDeserialization_MatchesPythonSchema()
    {
        string pythonJson = """
        {
          "task_id": "test-uuid-1234",
          "url": "https://example.com/test.iso",
          "filename": "test.iso",
          "destination_dir": "C:\\Downloads\\Getly",
          "status": "DOWNLOADING",
          "total_size": 104857600,
          "downloaded_bytes": 52428800,
          "speed_bps": 5242880.0,
          "upload_speed_bps": 102400.0,
          "progress_percentage": 50.0,
          "progress_ratio": 0.5,
          "category": "Software",
          "task_type": "HTTP",
          "created_at": 1700000000.0,
          "seeds": 0,
          "peers": 0,
          "priority": 4
        }
        """;

        var task = JsonSerializer.Deserialize<TaskItemDto>(pythonJson);
        Assert.NotNull(task);
        Assert.Equal("test-uuid-1234", task.TaskId);
        Assert.Equal("https://example.com/test.iso", task.Url);
        Assert.Equal("test.iso", task.Filename);
        Assert.Equal("DOWNLOADING", task.Status);
        Assert.Equal(104857600, task.TotalSize);
        Assert.Equal(52428800, task.DownloadedBytes);
        Assert.Equal(50.0, task.ProgressPercentage);
        Assert.Equal("Software", task.Category);
    }

    [Fact]
    public void TaskItemViewModel_FormattingAndState_CalculatesCorrectly()
    {
        var dto = new TaskItemDto
        {
            TaskId = "1",
            Url = "https://cdn.example.com/video.mp4",
            Filename = "video.mp4",
            Category = "Video",
            Status = "DOWNLOADING",
            TotalSize = 100 * 1024 * 1024,      // 100 MB
            DownloadedBytes = 25 * 1024 * 1024,  // 25 MB
            SpeedBps = 5 * 1024 * 1024,          // 5 MB/s
            ProgressPercentage = 25.0,
            ProgressRatio = 0.25,
            TaskType = "HTTP"
        };

        var vm = new TaskItemViewModel(dto);

        Assert.Equal("video.mp4", vm.Filename);
        Assert.Equal("Video", vm.Category);
        Assert.Equal(25.0, vm.Progress);
        Assert.Equal("25%", vm.ProgressPercentageFormatted);
        Assert.True(vm.IsActive);
        Assert.False(vm.IsPaused);
        Assert.False(vm.IsCompleted);

        // Downloaded formatted should show 25 MB / 100 MB
        Assert.Contains("25", vm.DownloadedFormatted);
        Assert.Contains("100", vm.DownloadedFormatted);

        // Speed formatted should show ~5 MB/s
        Assert.Contains("5", vm.SpeedFormatted);
        Assert.Contains("MB/s", vm.SpeedFormatted);

        // ETA for remaining 75 MB at 5 MB/s = 15 seconds -> 00:15
        Assert.Equal("00:15", vm.EtaFormatted);

        // State update transition to COMPLETED
        var completedDto = dto with
        {
            Status = "COMPLETED",
            DownloadedBytes = 100 * 1024 * 1024,
            ProgressPercentage = 100.0,
            ProgressRatio = 1.0,
            SpeedBps = 0.0
        };

        vm.UpdateFrom(completedDto);
        Assert.True(vm.IsCompleted);
        Assert.False(vm.IsActive);
        Assert.Equal("Completato", vm.StatusDisplay);
        Assert.Equal("--:--", vm.EtaFormatted);
    }

    [Fact]
    public void TaskItemViewModel_ErrorMessageAndToolTip_Formatting()
    {
        var dto = new TaskItemDto
        {
            TaskId = "error-task-1",
            Url = "https://link.testfile.org/PDF100MB",
            Filename = "PDF100MB",
            Status = "FAILED",
            ErrorMessage = "Probe failed: server returned HTTP 403 for https://link.testfile.org/PDF100MB",
            Category = "Documenti",
            TaskType = "HTTP"
        };

        var vm = new TaskItemViewModel(dto);

        Assert.True(vm.IsError);
        Assert.Equal("Errore", vm.StatusDisplay);
        Assert.Equal("Probe failed: server returned HTTP 403 for https://link.testfile.org/PDF100MB", vm.ErrorMessage);
        Assert.Equal("Errore: Probe failed: server returned HTTP 403 for https://link.testfile.org/PDF100MB", vm.ErrorToolTip);
    }

    [Fact(Timeout = 10000)]
    public async Task IpcClient_ConnectTimeout_FailsPromptlyAsync()
    {
        string nonExistentPipe = $@"\\.\pipe\test_timeout_{Guid.NewGuid():N}";
        using var client = new IpcClient(nonExistentPipe);

        var sw = Stopwatch.StartNew();
        await Assert.ThrowsAnyAsync<Exception>(async () =>
        {
            await client.ConnectAsync(TimeSpan.FromSeconds(1), _testCts.Token);
        });
        sw.Stop();

        Assert.True(sw.ElapsedMilliseconds < 3500, $"ConnectAsync took too long to fail: {sw.ElapsedMilliseconds}ms");
    }

    [Fact(Timeout = 10000)]
    public async Task IpcClient_ConnectAndRpc_WithPythonEngineServiceAsync()
    {
        string pipeName = $@"\\.\pipe\test_cs_ipc_{Guid.NewGuid():N}";
        using var supervisor = new EngineSupervisor(pipeName);
        supervisor.OnLogReceived += msg => Console.WriteLine(msg);

        // Launch engine via supervisor (assigned to Job Object, async stdout/stderr reading)
        bool started = await supervisor.StartEngineAsync(_testCts.Token);
        Assert.True(started);

        using var client = new IpcClient(pipeName);
        client.OnLog += msg => Console.WriteLine(msg);

        try
        {
            // Connect with strict 3-second timeout
            await client.ConnectAsync(TimeSpan.FromSeconds(3), _testCts.Token);
            Assert.True(client.IsConnected);

            // 1. GetStatsAsync
            var stats = await client.GetStatsAsync(_testCts.Token);
            Assert.NotNull(stats);
            Assert.NotNull(stats.Memory);
            Assert.True(stats.Memory.ProcessRssBytes > 0);

            // 2. AddTaskAsync
            var task = await client.AddTaskAsync(
                "https://example.com/csharp_test.dat",
                category: "Test",
                startPaused: true,
                cancellationToken: _testCts.Token
            );
            Assert.NotNull(task);
            Assert.Equal("https://example.com/csharp_test.dat", task.Url);
            Assert.Equal("PAUSED", task.Status);

            // 3. Pause & Resume
            await client.ResumeTaskAsync(task.TaskId, _testCts.Token);
            await client.PauseTaskAsync(task.TaskId, _testCts.Token);

            // 4. Delete
            await client.DeleteTaskAsync(task.TaskId, deleteFiles: false, cancellationToken: _testCts.Token);
        }
        finally
        {
            await client.DisposeAsync();
            await supervisor.StopEngineAsync(1500);
        }
    }
}
