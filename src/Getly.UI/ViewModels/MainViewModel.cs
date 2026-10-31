using System;
using System.Collections.Concurrent;
using System.Collections.Generic;
using System.Collections.ObjectModel;
using System.Diagnostics;
using System.IO;
using System.Linq;
using System.Threading.Tasks;
using CommunityToolkit.Mvvm.ComponentModel;
using CommunityToolkit.Mvvm.Input;
using Getly.UI.Models;
using Getly.UI.Services;
using Microsoft.UI.Dispatching;

namespace Getly.UI.ViewModels;

public partial class MainViewModel : ObservableObject
{
    private readonly IpcClient _ipcClient;
    private readonly EngineSupervisor _supervisor;
    private readonly DispatcherQueue? _dispatcherQueue;
    private readonly Dictionary<string, TaskItemViewModel> _taskIndex = new();

    public ObservableCollection<TaskItemViewModel> Tasks { get; } = [];
    public ObservableCollection<TaskItemViewModel> FilteredTasks { get; } = [];

    [ObservableProperty]
    private TaskItemViewModel? _selectedTask;

    [ObservableProperty]
    private string _selectedNavCategory = "ALL";

    [ObservableProperty]
    private string _searchText = string.Empty;

    [ObservableProperty]
    private string _aggregateDownloadSpeed = "0 B/s";

    [ObservableProperty]
    private string _aggregateUploadSpeed = "0 B/s";

    [ObservableProperty]
    private string _totalDownloaded = "0 B";

    [ObservableProperty]
    private bool _vpnShieldActive = true;

    [ObservableProperty]
    private string _vpnShieldStatusText = "VPN Shield Attivo";

    [ObservableProperty]
    private string _activeAdaptersSummary = "Rilevamento reti in corso...";

    [ObservableProperty]
    private bool _isBackendConnected;

    [ObservableProperty]
    private string _statusMessage = "Pronto";

    public MainViewModel(IpcClient? ipcClient = null, EngineSupervisor? supervisor = null)
    {
        _supervisor = supervisor ?? new EngineSupervisor();
        _ipcClient = ipcClient ?? new IpcClient(_supervisor.PipeName);
        _dispatcherQueue = DispatcherQueue.GetForCurrentThread();

        _ipcClient.OnConnected += () => RunOnUI(() => IsBackendConnected = true);
        _ipcClient.OnDisconnected += () => RunOnUI(() => IsBackendConnected = false);
        _ipcClient.OnTelemetryReceived += HandleTelemetryTick;
    }

    /// <summary>
    /// Initializes background engine supervisor, connects IPC client and loads initial tasks.
    /// </summary>
    public async Task InitializeAsync()
    {
        try
        {
            StatusMessage = "Avvio motore di download...";
            await _supervisor.StartEngineAsync();

            StatusMessage = "Connessione al canale IPC...";
            await _ipcClient.ConnectAsync(timeoutMs: 8000);

            StatusMessage = "Caricamento download...";
            var initialTasks = await _ipcClient.GetTasksAsync();
            RunOnUI(() =>
            {
                Tasks.Clear();
                _taskIndex.Clear();
                foreach (var dto in initialTasks)
                {
                    var vm = new TaskItemViewModel(dto);
                    _taskIndex[vm.TaskId] = vm;
                    Tasks.Add(vm);
                }
                ApplyFilters();
                StatusMessage = "Pronto";
            });

            // Initial stats query
            var stats = await _ipcClient.GetStatsAsync();
            UpdateSystemStats(stats);
        }
        catch (Exception ex)
        {
            StatusMessage = $"Errore inizializzazione: {ex.Message}";
        }
    }

    partial void OnSearchTextChanged(string value) => ApplyFilters();
    partial void OnSelectedNavCategoryChanged(string value) => ApplyFilters();

    public void ApplyFilters()
    {
        var query = SearchText.Trim();
        var category = SelectedNavCategory;

        var matching = Tasks.Where(t =>
        {
            // Category / Tab filter
            bool catMatch = category switch
            {
                "DOWNLOADING" => t.IsActive,
                "COMPLETED" => t.IsCompleted,
                "TORRENT" => t.IsTorrent,
                _ => true // "ALL"
            };

            if (!catMatch) return false;

            // Search query filter
            if (!string.IsNullOrEmpty(query))
            {
                bool fnMatch = t.Filename.Contains(query, StringComparison.OrdinalIgnoreCase);
                bool urlMatch = t.Url.Contains(query, StringComparison.OrdinalIgnoreCase);
                bool tagMatch = t.Category.Contains(query, StringComparison.OrdinalIgnoreCase);
                return fnMatch || urlMatch || tagMatch;
            }

            return true;
        }).ToList();

        FilteredTasks.Clear();
        foreach (var item in matching)
        {
            FilteredTasks.Add(item);
        }
    }

    private void HandleTelemetryTick(TelemetryTickDto tick)
    {
        RunOnUI(() =>
        {
            AggregateDownloadSpeed = TaskItemViewModel.FormatBytes((long)tick.Stats.DownloadSpeedBps) + "/s";
            AggregateUploadSpeed = TaskItemViewModel.FormatBytes((long)tick.Stats.UploadSpeedBps) + "/s";
            TotalDownloaded = TaskItemViewModel.FormatBytes(tick.Stats.TotalDownloadedBytes);

            // Delta update: update only tasks included in this tick frame
            bool listStructureChanged = false;
            foreach (var taskDto in tick.Tasks)
            {
                if (_taskIndex.TryGetValue(taskDto.TaskId, out var existing))
                {
                    existing.UpdateFrom(taskDto);
                }
                else
                {
                    var newVm = new TaskItemViewModel(taskDto);
                    _taskIndex[newVm.TaskId] = newVm;
                    Tasks.Add(newVm);
                    listStructureChanged = true;
                }
            }

            if (listStructureChanged)
            {
                ApplyFilters();
            }

            // Update network adapters summary if present
            if (tick.Stats.Interfaces.Count > 0)
            {
                var up = tick.Stats.Interfaces.Where(i => i.IsUp).Select(i => i.FriendlyName ?? i.Name);
                ActiveAdaptersSummary = string.Join(" | ", up);
            }
        });
    }

    private void UpdateSystemStats(SystemStatsDto stats)
    {
        RunOnUI(() =>
        {
            VpnShieldActive = stats.VpnShieldEnabled;
            VpnShieldStatusText = stats.VpnShieldEnabled ? "VPN Shield Attivo (Kill-Switch)" : "VPN Shield Disattivo";
            if (stats.ActiveAdapters.Count > 0)
            {
                var activeNames = stats.ActiveAdapters.Select(a => a.FriendlyName ?? a.Name);
                ActiveAdaptersSummary = string.Join(" + ", activeNames);
            }
        });
    }

    #region Relay Commands

    [RelayCommand]
    private async Task AddDownloadAsync(string url)
    {
        if (string.IsNullOrWhiteSpace(url)) return;
        try
        {
            StatusMessage = "Aggiunta download...";
            var task = await _ipcClient.AddTaskAsync(url.Trim());
            RunOnUI(() =>
            {
                if (!_taskIndex.ContainsKey(task.TaskId))
                {
                    var vm = new TaskItemViewModel(task);
                    _taskIndex[vm.TaskId] = vm;
                    Tasks.Add(vm);
                    ApplyFilters();
                }
                StatusMessage = "Download aggiunto alla coda.";
            });
        }
        catch (Exception ex)
        {
            StatusMessage = $"Errore aggiunta: {ex.Message}";
        }
    }

    [RelayCommand]
    private async Task PauseTaskAsync(TaskItemViewModel? task)
    {
        var target = task ?? SelectedTask;
        if (target is null) return;
        try
        {
            await _ipcClient.PauseTaskAsync(target.TaskId);
            target.Status = "PAUSED";
            target.IsActive = false;
            target.IsPaused = true;
            target.StatusDisplay = "In pausa";
        }
        catch (Exception ex)
        {
            StatusMessage = $"Errore pausa: {ex.Message}";
        }
    }

    [RelayCommand]
    private async Task ResumeTaskAsync(TaskItemViewModel? task)
    {
        var target = task ?? SelectedTask;
        if (target is null) return;
        try
        {
            await _ipcClient.ResumeTaskAsync(target.TaskId);
            target.Status = "DOWNLOADING";
            target.IsActive = true;
            target.IsPaused = false;
            target.StatusDisplay = "In download";
        }
        catch (Exception ex)
        {
            StatusMessage = $"Errore avvio: {ex.Message}";
        }
    }

    [RelayCommand]
    private async Task DeleteTaskAsync(TaskItemViewModel? task)
    {
        var target = task ?? SelectedTask;
        if (target is null) return;
        try
        {
            await _ipcClient.DeleteTaskAsync(target.TaskId, deleteFiles: false);
            RunOnUI(() =>
            {
                _taskIndex.Remove(target.TaskId);
                Tasks.Remove(target);
                FilteredTasks.Remove(target);
                if (SelectedTask == target) SelectedTask = null;
            });
        }
        catch (Exception ex)
        {
            StatusMessage = $"Errore eliminazione: {ex.Message}";
        }
    }

    [RelayCommand]
    private void OpenContainingFolder(TaskItemViewModel? task)
    {
        var target = task ?? SelectedTask;
        if (target is null) return;

        string folder = target.DestinationDir;
        string file = Path.Combine(folder, target.Filename);

        if (File.Exists(file))
        {
            Process.Start("explorer.exe", $"/select,\"{file}\"");
        }
        else if (Directory.Exists(folder))
        {
            Process.Start("explorer.exe", $"\"{folder}\"");
        }
    }

    [RelayCommand]
    private void OpenFile(TaskItemViewModel? task)
    {
        var target = task ?? SelectedTask;
        if (target is null) return;

        string file = Path.Combine(target.DestinationDir, target.Filename);
        if (File.Exists(file))
        {
            Process.Start(new ProcessStartInfo(file) { UseShellExecute = true });
        }
    }

    #endregion

    private void RunOnUI(Action action)
    {
        if (_dispatcherQueue is not null && !_dispatcherQueue.HasThreadAccess)
        {
            _dispatcherQueue.TryEnqueue(() => action());
        }
        else
        {
            action();
        }
    }
}
