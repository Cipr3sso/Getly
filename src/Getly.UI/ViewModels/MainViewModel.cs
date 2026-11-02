using System;
using System.Collections.Concurrent;
using System.Collections.Generic;
using System.Collections.ObjectModel;
using System.Diagnostics;
using System.IO;
using System.Linq;
using System.Text.Json;
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
    private readonly Dictionary<string, NetworkAdapterItemViewModel> _adapterIndex = new();

    public ObservableCollection<TaskItemViewModel> Tasks { get; } = [];
    public ObservableCollection<TaskItemViewModel> FilteredTasks { get; } = [];
    public ObservableCollection<NetworkAdapterItemViewModel> NetworkAdapters { get; } = [];
    public IpcClient IpcClient => _ipcClient;
    public event Action<TaskItemViewModel>? TaskNotificationTriggered;

    public void NotifyTaskActive(TaskItemViewModel task)
    {
        TaskNotificationTriggered?.Invoke(task);
    }

    [ObservableProperty]
    private string _defaultDownloadDir = Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.UserProfile), "Downloads", "Getly");

    [ObservableProperty]
    private int _maxConcurrentConnections = 8;

    [ObservableProperty]
    private long _globalSpeedLimitBps = 0;

    [ObservableProperty]
    private bool _requireConfirmation = false;

    [ObservableProperty]
    private double _confirmationThresholdMB = 100;

    [ObservableProperty]
    private bool _autoCategorize = true;

    [ObservableProperty]
    private bool _enableFloatingWindow = true;

    [ObservableProperty]
    private string _appTheme = "System";

    [ObservableProperty]
    private bool _autostartEnabled = IsAutostartConfigured();

    partial void OnAutostartEnabledChanged(bool value)
    {
        SetAutostartRegistry(value);
    }

    private static bool IsAutostartConfigured()
    {
        try
        {
            if (!OperatingSystem.IsWindows()) return false;
            using var key = Microsoft.Win32.Registry.CurrentUser.OpenSubKey(@"Software\Microsoft\Windows\CurrentVersion\Run", false);
            return key?.GetValue("Getly") != null;
        }
        catch { return false; }
    }

    private static void SetAutostartRegistry(bool enable)
    {
        try
        {
            if (!OperatingSystem.IsWindows()) return;
            using var key = Microsoft.Win32.Registry.CurrentUser.OpenSubKey(@"Software\Microsoft\Windows\CurrentVersion\Run", true);
            if (key is null) return;
            if (enable)
            {
                string? exe = Environment.ProcessPath;
                if (!string.IsNullOrEmpty(exe))
                {
                    key.SetValue("Getly", $"\"{exe}\" --minimized");
                }
            }
            else
            {
                key.DeleteValue("Getly", false);
            }
        }
        catch { }
    }

    [ObservableProperty]
    private TaskItemViewModel? _selectedTask;

    [ObservableProperty]
    private string _selectedNavCategory = "ALL";

    [ObservableProperty]
    private string _searchText = string.Empty;

    [ObservableProperty]
    private bool _hasNoTasks;

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
        try
        {
            _dispatcherQueue = DispatcherQueue.GetForCurrentThread();
        }
        catch
        {
            _dispatcherQueue = null;
        }

        LoadSettings();

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

            // Synchronize settings with Python backend
            try
            {
                await _ipcClient.SetSettingsAsync(
                    RequireConfirmation,
                    (int)ConfirmationThresholdMB,
                    AutoCategorize,
                    DefaultDownloadDir,
                    MaxConcurrentConnections
                );
            }
            catch (Exception ex)
            {
                Debug.WriteLine($"[Settings] Sync error: {ex.Message}");
            }

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

            // Startup health check for database and directories
            try
            {
                var health = await _ipcClient.HealthCheckAsync();
                if (health != null && (health.IndexedFiles > 0 || health.MissingFilesDetected > 0))
                {
                    StatusMessage = $"Avvio: {health.IndexedFiles} file indicizzati, {health.MissingFilesDetected} file aggiornati in Eliminati.";
                }
            }
            catch { }
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
                "DOWNLOADING" => t.IsActive && !t.IsDeleted,
                "COMPLETED" => t.IsCompleted && !t.IsDeleted,
                "TORRENT" => t.IsTorrent && !t.IsDeleted,
                "DELETED" => t.IsDeleted,
                _ => !t.IsDeleted // "ALL"
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
        HasNoTasks = FilteredTasks.Count == 0;
    }

    public void SelectAllTasks(bool isSelected)
    {
        foreach (var task in FilteredTasks)
        {
            task.IsSelected = isSelected;
        }
    }

    public async Task DeleteTasksAsync(IEnumerable<TaskItemViewModel> tasksToDelete, bool deleteFiles)
    {
        int count = 0;
        bool isAlreadyInDeleted = SelectedNavCategory == "DELETED";
        bool permanent = isAlreadyInDeleted || deleteFiles;

        foreach (var task in tasksToDelete.ToList())
        {
            try
            {
                await _ipcClient.DeleteTaskAsync(task.TaskId, deleteFiles: deleteFiles, permanent: permanent);
                RunOnUI(() =>
                {
                    if (permanent)
                    {
                        _taskIndex.Remove(task.TaskId);
                        Tasks.Remove(task);
                        FilteredTasks.Remove(task);
                        if (SelectedTask == task) SelectedTask = null;
                    }
                    else
                    {
                        task.Status = "DELETED";
                        task.IsDeleted = true;
                        task.IsActive = false;
                        task.StatusDisplay = "Eliminato";
                        task.StatusColor = "#9CA3AF";
                        task.StatusIconGlyph = "\uE74D";
                    }
                });
                count++;
            }
            catch (Exception ex)
            {
                StatusMessage = $"Errore eliminazione {task.Filename}: {ex.Message}";
            }
        }
        ApplyFilters();
        StatusMessage = permanent
            ? $"Eliminati definitivamente {count} download."
            : $"Spostati in Eliminati {count} download.";
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
                    if (newVm.IsActive || newVm.IsPendingConfirmation)
                    {
                        TaskNotificationTriggered?.Invoke(newVm);
                    }
                }
            }

            if (listStructureChanged)
            {
                ApplyFilters();
            }

            // Update network adapters summary and collection
            if (tick.Stats.Interfaces.Count > 0)
            {
                var up = tick.Stats.Interfaces.Where(i => i.IsUp).Select(i => i.FriendlyName ?? i.Name);
                ActiveAdaptersSummary = string.Join(" | ", up);

                foreach (var ifDto in tick.Stats.Interfaces)
                {
                    if (_adapterIndex.TryGetValue(ifDto.Name, out var existingAdapter))
                    {
                        existingAdapter.UpdateFrom(ifDto);
                    }
                    else
                    {
                        var newAdapter = new NetworkAdapterItemViewModel(ifDto);
                        _adapterIndex[newAdapter.Name] = newAdapter;
                        NetworkAdapters.Add(newAdapter);
                    }
                }
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

                foreach (var ifDto in stats.ActiveAdapters)
                {
                    if (_adapterIndex.TryGetValue(ifDto.Name, out var existingAdapter))
                    {
                        existingAdapter.UpdateFrom(ifDto);
                    }
                    else
                    {
                        var newAdapter = new NetworkAdapterItemViewModel(ifDto);
                        _adapterIndex[newAdapter.Name] = newAdapter;
                        NetworkAdapters.Add(newAdapter);
                    }
                }
            }
        });
    }

    #region Relay Commands

    [RelayCommand]
    public async Task AddDownloadAsync(string url)
    {
        await AddDownloadWithParamsAsync(url);
    }

    public async Task AddDownloadWithParamsAsync(string url, string? category = null, bool startPaused = false, string? filename = null)
    {
        if (string.IsNullOrWhiteSpace(url)) return;
        try
        {
            StatusMessage = "Aggiunta download...";
            var task = await _ipcClient.AddTaskAsync(url.Trim(), category: category, destDir: DefaultDownloadDir, filename: filename, startPaused: startPaused);
            RunOnUI(() =>
            {
                TaskItemViewModel vm;
                if (!_taskIndex.TryGetValue(task.TaskId, out var existing))
                {
                    vm = new TaskItemViewModel(task);
                    _taskIndex[vm.TaskId] = vm;
                    Tasks.Add(vm);
                    ApplyFilters();
                }
                else
                {
                    vm = existing;
                }
                StatusMessage = "Download aggiunto alla coda.";
                TaskNotificationTriggered?.Invoke(vm);
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
            TaskNotificationTriggered?.Invoke(target);
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
            bool isAlreadyInDeleted = SelectedNavCategory == "DELETED" || target.IsDeleted;
            await _ipcClient.DeleteTaskAsync(target.TaskId, deleteFiles: false, permanent: isAlreadyInDeleted);
            RunOnUI(() =>
            {
                if (isAlreadyInDeleted)
                {
                    _taskIndex.Remove(target.TaskId);
                    Tasks.Remove(target);
                    FilteredTasks.Remove(target);
                    if (SelectedTask == target) SelectedTask = null;
                    StatusMessage = $"Download rimosso definitivamente: {target.Filename}";
                }
                else
                {
                    target.Status = "DELETED";
                    target.IsDeleted = true;
                    target.IsActive = false;
                    target.StatusDisplay = "Eliminato";
                    target.StatusColor = "#9CA3AF";
                    target.StatusIconGlyph = "\uE74D";
                    ApplyFilters();
                    if (SelectedTask == target) SelectedTask = null;
                    StatusMessage = $"Spostato in Eliminati: {target.Filename}";
                }
            });
        }
        catch (Exception ex)
        {
            StatusMessage = $"Errore eliminazione: {ex.Message}";
        }
    }

    [RelayCommand]
    private async Task RestoreTaskAsync(TaskItemViewModel? task)
    {
        var target = task ?? SelectedTask;
        if (target is null) return;
        try
        {
            await _ipcClient.RestoreTaskAsync(target.TaskId);
            RunOnUI(() =>
            {
                target.IsDeleted = false;
                if (target.TotalSize > 0 && target.DownloadedBytes >= target.TotalSize)
                {
                    target.Status = "COMPLETED";
                    target.StatusDisplay = "Completato";
                    target.StatusColor = "#10B981";
                    target.StatusIconGlyph = "\uE73E";
                }
                else
                {
                    target.Status = "PAUSED";
                    target.StatusDisplay = "In pausa";
                    target.StatusColor = "#F59E0B";
                    target.StatusIconGlyph = "\uE769";
                }
                target.ErrorMessage = null;
                ApplyFilters();
                StatusMessage = $"Ripristinato: {target.Filename}";
            });
        }
        catch (Exception ex)
        {
            StatusMessage = $"Errore ripristino: {ex.Message}";
        }
    }

    [RelayCommand]
    private async Task ClearDeletedTasksAsync()
    {
        try
        {
            await _ipcClient.ClearDeletedTasksAsync(deleteFiles: false);
            RunOnUI(() =>
            {
                var deletedList = Tasks.Where(t => t.IsDeleted).ToList();
                foreach (var t in deletedList)
                {
                    _taskIndex.Remove(t.TaskId);
                    Tasks.Remove(t);
                }
                ApplyFilters();
                StatusMessage = "Cestino svuotato con successo.";
            });
        }
        catch (Exception ex)
        {
            StatusMessage = $"Errore svuotamento cestino: {ex.Message}";
        }
    }

    [RelayCommand]
    private async Task DeleteTaskWithFilesAsync(TaskItemViewModel? task)
    {
        var target = task ?? SelectedTask;
        if (target is null) return;
        try
        {
            await _ipcClient.DeleteTaskAsync(target.TaskId, deleteFiles: true);
            RunOnUI(() =>
            {
                _taskIndex.Remove(target.TaskId);
                Tasks.Remove(target);
                FilteredTasks.Remove(target);
                if (SelectedTask == target) SelectedTask = null;
            });
            StatusMessage = $"Download e file rimossi: {target.Filename}";
        }
        catch (Exception ex)
        {
            StatusMessage = $"Errore eliminazione file: {ex.Message}";
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

    [RelayCommand]
    public async Task ToggleAdapterAsync(NetworkAdapterItemViewModel? adapter)
    {
        if (adapter is null) return;
        try
        {
            await _ipcClient.ToggleAdapterAsync(adapter.Name, adapter.IsEnabled);
            StatusMessage = $"Scheda '{adapter.DisplayName}' {(adapter.IsEnabled ? "abilitata" : "esclusa")}";
        }
        catch (Exception ex)
        {
            StatusMessage = $"Errore modifica scheda {adapter.DisplayName}: {ex.Message}";
        }
    }

    public static string GetSettingsFilePath()
    {
        var localAppData = Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData);
        return Path.Combine(localAppData, "Getly", "settings.json");
    }

    public void LoadSettings()
    {
        try
        {
            var filePath = GetSettingsFilePath();
            if (File.Exists(filePath))
            {
                var json = File.ReadAllText(filePath);
                var settings = JsonSerializer.Deserialize<GetlySettingsModel>(json);
                if (settings != null)
                {
                    if (!string.IsNullOrWhiteSpace(settings.DefaultDownloadDir))
                        DefaultDownloadDir = settings.DefaultDownloadDir;
                    if (settings.MaxConcurrentConnections > 0)
                        MaxConcurrentConnections = settings.MaxConcurrentConnections;
                    GlobalSpeedLimitBps = settings.GlobalSpeedLimitBps;
                    RequireConfirmation = settings.RequireConfirmation;
                    ConfirmationThresholdMB = settings.ConfirmationThresholdMB;
                    AutoCategorize = settings.AutoCategorize;
                    VpnShieldActive = settings.VpnShieldActive;
                    AutostartEnabled = settings.AutostartEnabled;
                    EnableFloatingWindow = settings.EnableFloatingWindow;
                    if (!string.IsNullOrWhiteSpace(settings.AppTheme))
                        AppTheme = settings.AppTheme;
                }
            }
        }
        catch (Exception ex)
        {
            Debug.WriteLine($"[Settings] Load error: {ex.Message}");
        }
    }

    [RelayCommand]
    public async Task SaveSettingsAsync()
    {
        try
        {
            var settings = new GetlySettingsModel
            {
                DefaultDownloadDir = DefaultDownloadDir,
                MaxConcurrentConnections = MaxConcurrentConnections,
                GlobalSpeedLimitBps = GlobalSpeedLimitBps,
                RequireConfirmation = RequireConfirmation,
                ConfirmationThresholdMB = ConfirmationThresholdMB,
                AutoCategorize = AutoCategorize,
                VpnShieldActive = VpnShieldActive,
                AutostartEnabled = AutostartEnabled,
                EnableFloatingWindow = EnableFloatingWindow,
                AppTheme = AppTheme
            };

            var filePath = GetSettingsFilePath();
            var dir = Path.GetDirectoryName(filePath);
            if (!string.IsNullOrEmpty(dir) && !Directory.Exists(dir))
            {
                Directory.CreateDirectory(dir);
            }

            var json = JsonSerializer.Serialize(settings, new JsonSerializerOptions { WriteIndented = true });
            await File.WriteAllTextAsync(filePath, json);

            if (_ipcClient.IsConnected)
            {
                if (GlobalSpeedLimitBps >= 0)
                {
                    await _ipcClient.SetSpeedLimitAsync(GlobalSpeedLimitBps);
                }

                await _ipcClient.SetSettingsAsync(
                    RequireConfirmation,
                    (int)ConfirmationThresholdMB,
                    AutoCategorize,
                    DefaultDownloadDir,
                    MaxConcurrentConnections
                );
            }

            StatusMessage = "Impostazioni applicate con successo.";
        }
        catch (Exception ex)
        {
            StatusMessage = $"Errore applicazione impostazioni: {ex.Message}";
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

public class GetlySettingsModel
{
    public string? DefaultDownloadDir { get; set; }
    public int MaxConcurrentConnections { get; set; } = 8;
    public long GlobalSpeedLimitBps { get; set; } = 0;
    public bool RequireConfirmation { get; set; } = false;
    public double ConfirmationThresholdMB { get; set; } = 100;
    public bool AutoCategorize { get; set; } = true;
    public bool VpnShieldActive { get; set; } = true;
    public bool AutostartEnabled { get; set; } = false;
    public bool EnableFloatingWindow { get; set; } = true;
    public string AppTheme { get; set; } = "System";
}
