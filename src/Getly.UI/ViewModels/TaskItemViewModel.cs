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
    private bool _isCloudflareError;

    [ObservableProperty]
    private bool _isTorrent;

    [ObservableProperty]
    private bool _isDeleted;

    public bool IsNotDeleted => !IsDeleted;

    [ObservableProperty]
    private bool _isSelected;

    [ObservableProperty]
    private bool _isPendingConfirmation;

    [ObservableProperty]
    private string? _errorMessage;

    [ObservableProperty]
    private string _errorToolTip = string.Empty;

    [ObservableProperty]
    private string _destinationDir = string.Empty;

    [ObservableProperty]
    private string _statusColor = "#3B82F6"; // Accent blue default

    [ObservableProperty]
    private string _statusIconGlyph = "\uE896"; // Download icon

    [ObservableProperty]
    private int _seeds;

    [ObservableProperty]
    private int _peers;

    [ObservableProperty]
    private int _piecesTotal;

    [ObservableProperty]
    private int _piecesCompleted;

    [ObservableProperty]
    private List<TorrentFileDto>? _files;

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
        if (string.Equals(dto.Status, "COMPLETED", StringComparison.OrdinalIgnoreCase))
        {
            Progress = 100.0;
            ProgressPercentageFormatted = "100%";
            ProgressRatio = 1.0;
        }
        else
        {
            Progress = Math.Clamp(dto.ProgressPercentage, 0.0, 100.0);
            ProgressPercentageFormatted = $"{Progress:0.#}%";
            ProgressRatio = Math.Clamp(dto.ProgressRatio, 0.0, 1.0);
        }
        ErrorMessage = dto.ErrorMessage;
        IsTorrent = string.Equals(dto.TaskType, "TORRENT", StringComparison.OrdinalIgnoreCase);
        Seeds = dto.Seeds;
        Peers = dto.Peers;
        PiecesTotal = dto.PiecesTotal;
        PiecesCompleted = dto.PiecesCompleted;
        if (dto.FilesStatus != null)
        {
            Files = dto.FilesStatus.Select(f => new TorrentFileDto
            {
                Index = f.Index,
                Name = System.IO.Path.GetFileName(f.Path),
                Path = f.Path,
                Size = f.Size,
                Downloaded = f.Downloaded,
                Progress = f.Progress,
                Priority = f.Priority
            }).ToList();
        }

        // State Flags
        IsActive = string.Equals(dto.Status, "DOWNLOADING", StringComparison.OrdinalIgnoreCase);
        IsPaused = string.Equals(dto.Status, "PAUSED", StringComparison.OrdinalIgnoreCase);
        IsCompleted = string.Equals(dto.Status, "COMPLETED", StringComparison.OrdinalIgnoreCase);
        IsError = string.Equals(dto.Status, "FAILED", StringComparison.OrdinalIgnoreCase)
                  || string.Equals(dto.Status, "ERROR", StringComparison.OrdinalIgnoreCase);
        IsCloudflareError = IsError && !string.IsNullOrWhiteSpace(ErrorMessage) &&
            (ErrorMessage.Contains("403", StringComparison.OrdinalIgnoreCase)
             || ErrorMessage.Contains("Cloudflare", StringComparison.OrdinalIgnoreCase)
             || ErrorMessage.Contains("WAF", StringComparison.OrdinalIgnoreCase)
             || ErrorMessage.Contains("Captcha", StringComparison.OrdinalIgnoreCase)
             || ErrorMessage.Contains("Turnstile", StringComparison.OrdinalIgnoreCase));
        IsPendingConfirmation = string.Equals(dto.Status, "WAITING", StringComparison.OrdinalIgnoreCase)
                                || string.Equals(dto.Status, "PENDING_CONFIRMATION", StringComparison.OrdinalIgnoreCase)
                                || string.Equals(dto.Status, "WAITING_CONFIRMATION", StringComparison.OrdinalIgnoreCase)
                                || string.Equals(dto.Status, "WaitingConfirmation", StringComparison.OrdinalIgnoreCase);

        ErrorToolTip = !string.IsNullOrWhiteSpace(ErrorMessage)
            ? $"Errore: {ErrorMessage}"
            : (IsError ? "Errore durante il download" : string.Empty);

        // Formatted metrics
        DownloadedFormatted = FormatSize(dto.DownloadedBytes, dto.TotalSize);
        SpeedFormatted = FormatSpeed(dto.SpeedBps);
        EtaFormatted = FormatEta(dto.SpeedBps, dto.DownloadedBytes, dto.TotalSize);

        // Status presentation
        switch (dto.Status.ToUpperInvariant())
        {
            case "DOWNLOADING":
                StatusDisplay = IsTorrent ? $"Scaricamento (P2P)" : "In download";
                StatusColor = "#3B82F6"; // Fluent Accent Blue
                StatusIconGlyph = "\uE896"; // Download
                break;
            case "PAUSED":
                StatusDisplay = "In pausa";
                StatusColor = "#F59E0B"; // Vibrant Amber
                StatusIconGlyph = "\uE769"; // Pause
                break;
            case "COMPLETED":
                StatusDisplay = "Completato";
                StatusColor = "#10B981"; // Vibrant Emerald Green
                StatusIconGlyph = "\uE73E"; // Checkmark
                break;
            case "FAILED":
                StatusDisplay = "Errore";
                StatusColor = "#EF4444"; // Vibrant Red
                StatusIconGlyph = "\uEB90"; // Error badge
                break;
            case "WAITING":
            case "PENDING_CONFIRMATION":
            case "WAITING_CONFIRMATION":
            case "WAITINGCONFIRMATION":
                StatusDisplay = "In attesa di conferma";
                StatusColor = "#F59E0B"; // Amber
                StatusIconGlyph = "\uE897"; // Download or hourglass badge
                break;
            case "CANCELLED":
                StatusDisplay = "Annullato";
                StatusColor = "#9CA3AF"; // Gray
                StatusIconGlyph = "\uE711"; // Cancel
                break;
            case "DELETED":
                if (!string.IsNullOrWhiteSpace(ErrorMessage) && ErrorMessage.Contains("rimosso o spostato", StringComparison.OrdinalIgnoreCase))
                {
                    StatusDisplay = "File rimosso o spostato";
                    StatusColor = "#EF4444"; // Vibrant Red
                }
                else
                {
                    StatusDisplay = "Eliminato";
                    StatusColor = "#9CA3AF"; // Gray
                }
                StatusIconGlyph = "\uE74D"; // Trash
                break;
            default:
                StatusDisplay = "In attesa";
                StatusColor = "#60CDFF";
                StatusIconGlyph = "\uE823"; // Hourglass / Sync
                break;
        }
        IsDeleted = string.Equals(dto.Status, "DELETED", StringComparison.OrdinalIgnoreCase);
        OnPropertyChanged(nameof(IsNotDeleted));
        OnPropertyChanged(nameof(CategoryBackground));
        OnPropertyChanged(nameof(SizeMbFormatted));
    }

    public string SizeMbFormatted
    {
        get
        {
            if (TotalSize <= 0) return "0 MB";
            double mb = TotalSize / (1024.0 * 1024.0);
            return $"{mb:0.#} MB";
        }
    }

    public string CategoryBackground
    {
        get
        {
            return Category.ToLowerInvariant() switch
            {
                "video" or "film" => "#0078D4",
                "compressi" or "archivio" or "zip" or "rar" or "7z" => "#D83B01",
                "documenti" or "pdf" or "ebook" or "doc" => "#C42B1C",
                "audio" or "musica" or "mp3" => "#8764B8",
                "software" or "programmi" or "iso" or "exe" => "#008272",
                "torrents" or "torrent" or "p2p" => "#A855F7",
                _ => "#475569"
            };
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

    public string UploadSpeedFormatted => FormatSpeed(UploadSpeedBps);

    public static string FormatSpeed(double bytesPerSec)
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

public partial class BatchDownloadItem : ObservableObject
{
    [ObservableProperty]
    private bool _isSelected = true;

    [ObservableProperty]
    private string _url = string.Empty;

    [ObservableProperty]
    private string _filename = string.Empty;

    [ObservableProperty]
    private string _category = "Altro";

    public string CategoryBackground
    {
        get
        {
            return Category.ToLowerInvariant() switch
            {
                "video" or "film" => "#0078D4",
                "compressi" or "archivio" or "zip" or "rar" or "7z" => "#D83B01",
                "documenti" or "pdf" or "ebook" or "doc" => "#C42B1C",
                "audio" or "musica" or "mp3" => "#8764B8",
                "software" or "programmi" or "iso" or "exe" => "#008272",
                "torrents" or "torrent" or "p2p" => "#A855F7",
                _ => "#475569"
            };
        }
    }

    public static string DeduceFilename(string url)
    {
        if (string.IsNullOrWhiteSpace(url)) return "download";

        if (url.StartsWith("magnet:", StringComparison.OrdinalIgnoreCase))
        {
            var match = System.Text.RegularExpressions.Regex.Match(url, @"[?&]dn=([^&]+)", System.Text.RegularExpressions.RegexOptions.IgnoreCase);
            if (match.Success)
            {
                return Uri.UnescapeDataString(match.Groups[1].Value.Replace('+', ' '));
            }
            return "Torrent Download";
        }

        try
        {
            if (Uri.TryCreate(url, UriKind.Absolute, out var uri))
            {
                string name = System.IO.Path.GetFileName(uri.LocalPath);
                if (!string.IsNullOrWhiteSpace(name))
                {
                    return Uri.UnescapeDataString(name);
                }
            }
        }
        catch { }

        return "download";
    }

    public static string DeduceCategory(string filename, string url)
    {
        if (url.StartsWith("magnet:", StringComparison.OrdinalIgnoreCase) || filename.EndsWith(".torrent", StringComparison.OrdinalIgnoreCase)) return "Torrent";
        string ext = System.IO.Path.GetExtension(filename).ToLowerInvariant();

        return ext switch
        {
            ".mp4" or ".mkv" or ".avi" or ".mov" or ".webm" or ".flv" or ".ts" or ".m4v" => "Video",
            ".zip" or ".rar" or ".7z" or ".tar" or ".gz" or ".bz2" or ".xz" or ".iso" => "Compressi",
            ".pdf" or ".doc" or ".docx" or ".epub" or ".txt" or ".xlsx" or ".xls" or ".ppt" or ".pptx" or ".csv" => "Documenti",
            ".mp3" or ".flac" or ".wav" or ".aac" or ".ogg" or ".m4a" or ".wma" => "Audio",
            ".exe" or ".msi" or ".apk" or ".dmg" or ".pkg" or ".deb" or ".rpm" or ".appx" => "Software",
            _ => "Altro"
        };
    }
}
