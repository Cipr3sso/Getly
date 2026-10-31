using System;
using CommunityToolkit.Mvvm.ComponentModel;
using Getly.UI.Models;

namespace Getly.UI.ViewModels;

public partial class TaskItemViewModel : ObservableObject
{
    [ObservableProperty]
    private string _taskId = string.Empty;

    [ObservableProperty]
    private string _url = string.Empty;

    [ObservableProperty]
    private string _filename = string.Empty;

    [ObservableProperty]
    private string _category = "Altro";

    [ObservableProperty]
    private string _status = "PENDING";

    [ObservableProperty]
    private string _statusDisplay = "In attesa";

    [ObservableProperty]
    private double _progress;

    [ObservableProperty]
    private string _progressPercentageFormatted = "0%";

    [ObservableProperty]
    private double _progressRatio;

    [ObservableProperty]
    private long _downloadedBytes;

    [ObservableProperty]
    private long _totalSize;

    [ObservableProperty]
    private double _speedBps;

    [ObservableProperty]
    private double _uploadSpeedBps;

    [ObservableProperty]
    private string _downloadedFormatted = "0 B";

    [ObservableProperty]
    private string _speedFormatted = "0 B/s";

    [ObservableProperty]
    private string _etaFormatted = "--:--";

    [ObservableProperty]
    private bool _isActive;

    [ObservableProperty]
    private bool _isPaused;

    [ObservableProperty]
    private bool _isCompleted;

    [ObservableProperty]
    private bool _isError;

    [ObservableProperty]
    private bool _isTorrent;

    [ObservableProperty]
    private string? _errorMessage;

    [ObservableProperty]
    private string _destinationDir = string.Empty;

    [ObservableProperty]
    private string _statusColor = "#3B82F6"; // Accent blue default

    [ObservableProperty]
    private string _statusIconGlyph = "\uE896"; // Download icon

    public TaskItemViewModel(TaskItemDto dto)
    {
        UpdateFrom(dto);
    }

    /// <summary>
    /// Synchronizes reactive properties from an incoming DTO or delta update tick.
    /// </summary>
    public void UpdateFrom(TaskItemDto dto)
    {
        TaskId = dto.TaskId;
        Url = dto.Url;
        Filename = !string.IsNullOrEmpty(dto.Filename) ? dto.Filename : "Download";
        DestinationDir = dto.DestinationDir;
        Category = !string.IsNullOrEmpty(dto.Category) ? dto.Category : "Altro";
        Status = dto.Status;
        TotalSize = dto.TotalSize;
        DownloadedBytes = dto.DownloadedBytes;
        SpeedBps = dto.SpeedBps;
        UploadSpeedBps = dto.UploadSpeedBps;
        Progress = Math.Clamp(dto.ProgressPercentage, 0.0, 100.0);
        ProgressPercentageFormatted = $"{Progress:0.#}%";
        ProgressRatio = Math.Clamp(dto.ProgressRatio, 0.0, 1.0);
        ErrorMessage = dto.ErrorMessage;
        IsTorrent = string.Equals(dto.TaskType, "TORRENT", StringComparison.OrdinalIgnoreCase);

        // State Flags
        IsActive = string.Equals(dto.Status, "DOWNLOADING", StringComparison.OrdinalIgnoreCase);
        IsPaused = string.Equals(dto.Status, "PAUSED", StringComparison.OrdinalIgnoreCase);
        IsCompleted = string.Equals(dto.Status, "COMPLETED", StringComparison.OrdinalIgnoreCase);
        IsError = string.Equals(dto.Status, "FAILED", StringComparison.OrdinalIgnoreCase);

        // Formatted metrics
        DownloadedFormatted = FormatSize(dto.DownloadedBytes, dto.TotalSize);
        SpeedFormatted = FormatSpeed(dto.SpeedBps);
        EtaFormatted = FormatEta(dto.SpeedBps, dto.DownloadedBytes, dto.TotalSize);

        // Status presentation
        switch (dto.Status.ToUpperInvariant())
        {
            case "DOWNLOADING":
                StatusDisplay = IsTorrent ? $"Scaricamento (P2P)" : "In download";
                StatusColor = "#3B82F6"; // Fluent Blue
                StatusIconGlyph = "\uE896"; // Download
                break;
            case "PAUSED":
                StatusDisplay = "In pausa";
                StatusColor = "#F59E0B"; // Amber
                StatusIconGlyph = "\uE769"; // Pause
                break;
            case "COMPLETED":
                StatusDisplay = "Completato";
                StatusColor = "#10B981"; // Emerald Green
                StatusIconGlyph = "\uE73E"; // Checkmark
                break;
            case "FAILED":
                StatusDisplay = "Errore";
                StatusColor = "#EF4444"; // Red
                StatusIconGlyph = "\uEB90"; // Error badge
                break;
            case "CANCELLED":
                StatusDisplay = "Annullato";
                StatusColor = "#9CA3AF"; // Gray
                StatusIconGlyph = "\uE711"; // Cancel
                break;
            default:
                StatusDisplay = "In attesa";
                StatusColor = "#60A5FA";
                StatusIconGlyph = "\uE823"; // Hourglass / Sync
                break;
        }
    }

    public static string FormatBytes(long bytes)
    {
        if (bytes < 0) bytes = 0;
        string[] suffixes = ["B", "KB", "MB", "GB", "TB"];
        int i = 0;
        double d = bytes;
        while (d >= 1024.0 && i < suffixes.Length - 1)
        {
            d /= 1024.0;
            i++;
        }
        return $"{d:0.#} {suffixes[i]}";
    }

    private static string FormatSize(long downloaded, long total)
    {
        if (total <= 0)
            return FormatBytes(downloaded);
        return $"{FormatBytes(downloaded)} / {FormatBytes(total)}";
    }

    private static string FormatSpeed(double bytesPerSec)
    {
        if (bytesPerSec <= 0) return "0 B/s";
        return $"{FormatBytes((long)bytesPerSec)}/s";
    }

    private static string FormatEta(double speedBps, long downloaded, long total)
    {
        if (speedBps <= 0 || total <= 0 || downloaded >= total)
            return "--:--";

        long remaining = total - downloaded;
        double seconds = remaining / speedBps;
        if (seconds > 86400) return "> 24h";

        var ts = TimeSpan.FromSeconds(seconds);
        return ts.TotalHours >= 1 ? ts.ToString(@"hh\:mm\:ss") : ts.ToString(@"mm\:ss");
    }
}
