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

            // 5. SetSettingsAsync
            await client.SetSettingsAsync(
                requireConfirmation: true,
                confirmationThresholdMb: 250,
                autoCategorize: false,
                cancellationToken: _testCts.Token
            );
        }
        finally
        {
            await client.DisposeAsync();
            await supervisor.StopEngineAsync(1500);
        }
    }

    [Fact]
    public void MainViewModel_EnableFloatingWindow_DefaultAndEvent_WorkCorrectly()
    {
        var vm = new MainViewModel();
        Assert.True(vm.EnableFloatingWindow);

        vm.EnableFloatingWindow = false;
        Assert.False(vm.EnableFloatingWindow);

        bool eventFired = false;
        TaskItemViewModel? notifiedTask = null;
        vm.TaskNotificationTriggered += task =>
        {
            eventFired = true;
            notifiedTask = task;
        };

        var dto = new TaskItemDto
        {
            TaskId = "test-floating-1",
            Url = "https://example.com/test.zip",
            Filename = "test.zip",
            Status = "DOWNLOADING",
            TotalSize = 1000,
            DownloadedBytes = 500,
            SpeedBps = 100,
            TaskType = "HTTP"
        };
        var taskVm = new TaskItemViewModel(dto);

        vm.NotifyTaskActive(taskVm);

        Assert.True(eventFired);
        Assert.NotNull(notifiedTask);
        Assert.Equal("test-floating-1", notifiedTask.TaskId);
    }

    [Fact]
    public void TaskItemViewModel_WaitingAndPendingConfirmation_SetsIsPendingConfirmation()
    {
        var waitingDto = new TaskItemDto
        {
            TaskId = "waiting-1",
            Url = "https://example.com/waiting.iso",
            Filename = "waiting.iso",
            Status = "WAITING",
            TotalSize = 50000,
            DownloadedBytes = 0,
            TaskType = "HTTP"
        };
        var waitingVm = new TaskItemViewModel(waitingDto);
        Assert.True(waitingVm.IsPendingConfirmation);
        Assert.Equal("In attesa di conferma", waitingVm.StatusDisplay);
        Assert.Equal("#F59E0B", waitingVm.StatusColor);

        var pendingConfirmDto = new TaskItemDto
        {
            TaskId = "pending-1",
            Url = "https://example.com/pending.iso",
            Filename = "pending.iso",
            Status = "PENDING_CONFIRMATION",
            TotalSize = 50000,
            DownloadedBytes = 0,
            TaskType = "HTTP"
        };
        var pendingConfirmVm = new TaskItemViewModel(pendingConfirmDto);
        Assert.True(pendingConfirmVm.IsPendingConfirmation);
        Assert.Equal("In attesa di conferma", pendingConfirmVm.StatusDisplay);

        var errorDto = new TaskItemDto
        {
            TaskId = "error-1",
            Url = "https://example.com/err.iso",
            Filename = "err.iso",
            Status = "FAILED",
            ErrorMessage = "Connessione fallita - CDN",
            TotalSize = 50000,
            DownloadedBytes = 0,
            TaskType = "HTTP"
        };
        var errorVm = new TaskItemViewModel(errorDto);
        Assert.True(errorVm.IsError);
        Assert.Equal("Connessione fallita - CDN", errorVm.ErrorMessage);
        Assert.Contains("Connessione fallita - CDN", errorVm.ErrorToolTip);
    }

    [Theory]
    [InlineData("WAITING")]
    [InlineData("PENDING_CONFIRMATION")]
    [InlineData("WAITING_CONFIRMATION")]
    [InlineData("WaitingConfirmation")]
    public void TaskItemViewModel_AllWaitingConfirmationVariants_Recognized(string status)
    {
        var dto = new TaskItemDto
        {
            TaskId = "variant-1",
            Url = "https://example.com/file.zip",
            Filename = "file.zip",
            Status = status,
            TotalSize = 100 * 1024 * 1024,
            TaskType = "HTTP"
        };
        var vm = new TaskItemViewModel(dto);
        Assert.True(vm.IsPendingConfirmation);
        Assert.Equal("In attesa di conferma", vm.StatusDisplay);
        Assert.Equal("#F59E0B", vm.StatusColor);
    }

    [Fact]
    public void TaskItemViewModel_SizeMbFormatted_FormatsCorrectly()
    {
        var dto100Mb = new TaskItemDto
        {
            TaskId = "size-1",
            Url = "https://example.com/file.zip",
            Filename = "file.zip",
            Status = "DOWNLOADING",
            TotalSize = 104857600, // 100 MB
            TaskType = "HTTP"
        };
        var vm1 = new TaskItemViewModel(dto100Mb);
        Assert.Equal("100 MB", vm1.SizeMbFormatted);

        var dto50Mb = dto100Mb with { TotalSize = 52428800 }; // 50 MB
        vm1.UpdateFrom(dto50Mb);
        Assert.Equal("50 MB", vm1.SizeMbFormatted);

        var dtoZero = dto100Mb with { TotalSize = 0 };
        vm1.UpdateFrom(dtoZero);
        Assert.Equal("0 MB", vm1.SizeMbFormatted);
    }

    [Fact]
    public void BatchDownloadItem_DeduceFilenameAndCategory_WorksCorrectly()
    {
        // 1. Direct HTTP URL
        string zipUrl = "https://cdn.example.org/downloads/setup_v2.zip";
        Assert.Equal("setup_v2.zip", BatchDownloadItem.DeduceFilename(zipUrl));
        Assert.Equal("Compressi", BatchDownloadItem.DeduceCategory("setup_v2.zip", zipUrl));

        // 2. Video URL
        string videoUrl = "https://media.example.com/trailer.mp4";
        Assert.Equal("trailer.mp4", BatchDownloadItem.DeduceFilename(videoUrl));
        Assert.Equal("Video", BatchDownloadItem.DeduceCategory("trailer.mp4", videoUrl));

        // 3. Document PDF
        string docUrl = "https://research.org/report.pdf";
        Assert.Equal("report.pdf", BatchDownloadItem.DeduceFilename(docUrl));
        Assert.Equal("Documenti", BatchDownloadItem.DeduceCategory("report.pdf", docUrl));

        // 4. Magnet link
        string magnetUrl = "magnet:?xt=urn:btih:da39a3ee5e6b4b0d3255bfef95601890afd80709&dn=Ubuntu+24.04+LTS.iso&tr=udp%3A%2F%2Ftracker.openbittorrent.com%3A80";
        Assert.Equal("Ubuntu 24.04 LTS.iso", BatchDownloadItem.DeduceFilename(magnetUrl));
        Assert.Equal("Torrent", BatchDownloadItem.DeduceCategory("Ubuntu 24.04 LTS.iso", magnetUrl));
    }

    [Fact]
    public void BatchDownload_RegexExtraction_ExtractsUrlsFromMixedText()
    {
        string rawText = """
        Ecco i link da scaricare:
        1. https://cdn.example.com/file1.zip (file compresso)
        2. http://downloads.test.org/installer.exe
        3. magnet:?xt=urn:btih:0123456789abcdef&dn=debian.iso
        Ignora questo testo non-link o link incompleti come ftp://old.server.com
        """;

        var regex = new System.Text.RegularExpressions.Regex(
            @"(https?://[^\s""'<>]+|magnet:\?[^\s""'<>]+)",
            System.Text.RegularExpressions.RegexOptions.IgnoreCase
        );

        var matches = regex.Matches(rawText);
        Assert.Equal(3, matches.Count);
        Assert.Equal("https://cdn.example.com/file1.zip", matches[0].Value);
        Assert.Equal("http://downloads.test.org/installer.exe", matches[1].Value);
        Assert.Equal("magnet:?xt=urn:btih:0123456789abcdef&dn=debian.iso", matches[2].Value);
    }

    [Fact]
    public async Task MainViewModel_Settings_DefaultsAndPersistenceAsync()
    {
        var vm = new MainViewModel();

        // Defaults
        Assert.False(vm.RequireConfirmation);
        Assert.Equal(100.0, vm.ConfirmationThresholdMB);
        Assert.True(vm.AutoCategorize);

        // Modify settings
        vm.RequireConfirmation = true;
        vm.ConfirmationThresholdMB = 250;
        vm.AutoCategorize = false;

        // Save
        await vm.SaveSettingsAsync();

        string settingsPath = MainViewModel.GetSettingsFilePath();
        Assert.True(File.Exists(settingsPath));

        string json = await File.ReadAllTextAsync(settingsPath);
        Assert.Contains("\"RequireConfirmation\": true", json);
        Assert.Contains("\"ConfirmationThresholdMB\": 250", json);
        Assert.Contains("\"AutoCategorize\": false", json);

        // Reload into a fresh VM instance
        var newVm = new MainViewModel();
        Assert.True(newVm.RequireConfirmation);
        Assert.Equal(250.0, newVm.ConfirmationThresholdMB);
        Assert.False(newVm.AutoCategorize);

        // Reset to original defaults
        newVm.RequireConfirmation = false;
        newVm.ConfirmationThresholdMB = 100;
        newVm.AutoCategorize = true;
        await newVm.SaveSettingsAsync();
    }
}
