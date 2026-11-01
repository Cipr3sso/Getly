using System;
using System.Collections.Generic;
using System.Text.Json.Serialization;

namespace Getly.UI.Models;

public sealed record TaskItemDto
{
    [JsonPropertyName("task_id")]
    public string TaskId { get; init; } = string.Empty;

    [JsonPropertyName("url")]
    public string Url { get; init; } = string.Empty;

    [JsonPropertyName("filename")]
    public string? Filename { get; init; }

    [JsonPropertyName("destination_dir")]
    public string DestinationDir { get; init; } = string.Empty;

    [JsonPropertyName("status")]
    public string Status { get; init; } = "PENDING";

    [JsonPropertyName("total_size")]
    public long TotalSize { get; init; }

    [JsonPropertyName("downloaded_bytes")]
    public long DownloadedBytes { get; init; }

    [JsonPropertyName("speed_bps")]
    public double SpeedBps { get; init; }

    [JsonPropertyName("upload_speed_bps")]
    public double UploadSpeedBps { get; init; }

    [JsonPropertyName("progress_percentage")]
    public double ProgressPercentage { get; init; }

    [JsonPropertyName("progress_ratio")]
    public double ProgressRatio { get; init; }

    [JsonPropertyName("category")]
    public string? Category { get; init; }

    [JsonPropertyName("task_type")]
    public string TaskType { get; init; } = "HTTP";

    [JsonPropertyName("error_message")]
    public string? ErrorMessage { get; init; }

    [JsonPropertyName("created_at")]
    public double CreatedAt { get; init; }

    [JsonPropertyName("seeds")]
    public int Seeds { get; init; }

    [JsonPropertyName("peers")]
    public int Peers { get; init; }

    [JsonPropertyName("pieces_total")]
    public int PiecesTotal { get; init; }

    [JsonPropertyName("pieces_completed")]
    public int PiecesCompleted { get; init; }

    [JsonPropertyName("priority")]
    public int Priority { get; init; } = 4;

    [JsonPropertyName("info_hash")]
    public string? InfoHash { get; init; }
}

public sealed record TelemetryTickDto
{
    [JsonPropertyName("event")]
    public string Event { get; init; } = "tick";

    [JsonPropertyName("timestamp")]
    public double Timestamp { get; init; }

    [JsonPropertyName("stats")]
    public TelemetryStatsDto Stats { get; init; } = new();

    [JsonPropertyName("tasks")]
    public List<TaskItemDto> Tasks { get; init; } = [];
}

public sealed record TelemetryStatsDto
{
    [JsonPropertyName("download_speed_bps")]
    public double DownloadSpeedBps { get; init; }

    [JsonPropertyName("upload_speed_bps")]
    public double UploadSpeedBps { get; init; }

    [JsonPropertyName("total_downloaded_bytes")]
    public long TotalDownloadedBytes { get; init; }

    [JsonPropertyName("interfaces")]
    public List<NetworkInterfaceDto> Interfaces { get; init; } = [];
}

public sealed record NetworkInterfaceDto
{
    [JsonPropertyName("name")]
    public string Name { get; init; } = string.Empty;

    [JsonPropertyName("friendly_name")]
    public string? FriendlyName { get; init; }

    [JsonPropertyName("ip")]
    public string? Ip { get; init; }

    [JsonPropertyName("is_up")]
    public bool IsUp { get; init; }

    [JsonPropertyName("is_primary")]
    public bool IsPrimary { get; init; }

    [JsonPropertyName("speed_mbps")]
    public double? SpeedMbps { get; init; }

    [JsonPropertyName("is_vpn")]
    public bool IsVpn { get; init; }

    [JsonPropertyName("is_enabled")]
    public bool IsEnabled { get; init; } = true;
}

public sealed record SystemStatsDto
{
    [JsonPropertyName("active_adapters")]
    public List<NetworkInterfaceDto> ActiveAdapters { get; init; } = [];

    [JsonPropertyName("vpn_shield_enabled")]
    public bool VpnShieldEnabled { get; init; }

    [JsonPropertyName("download_speed_bps")]
    public double DownloadSpeedBps { get; init; }

    [JsonPropertyName("upload_speed_bps")]
    public double UploadSpeedBps { get; init; }

    [JsonPropertyName("speed_limit_bps")]
    public long SpeedLimitBps { get; init; }

    [JsonPropertyName("memory")]
    public SystemMemoryDto Memory { get; init; } = new();

    [JsonPropertyName("task_counts")]
    public TaskCountsDto TaskCounts { get; init; } = new();
}

public sealed record SystemMemoryDto
{
    [JsonPropertyName("process_rss_bytes")]
    public long ProcessRssBytes { get; init; }

    [JsonPropertyName("process_rss_mb")]
    public double ProcessRssMb { get; init; }

    [JsonPropertyName("system_total_bytes")]
    public long SystemTotalBytes { get; init; }

    [JsonPropertyName("system_available_bytes")]
    public long SystemAvailableBytes { get; init; }

    [JsonPropertyName("system_percent")]
    public double SystemPercent { get; init; }
}

public sealed record TaskCountsDto
{
    [JsonPropertyName("total")]
    public int Total { get; init; }

    [JsonPropertyName("downloading")]
    public int Downloading { get; init; }

    [JsonPropertyName("completed")]
    public int Completed { get; init; }

    [JsonPropertyName("paused")]
    public int Paused { get; init; }

    [JsonPropertyName("failed")]
    public int Failed { get; init; }
}

public sealed record TorrentFileDto
{
    [JsonPropertyName("index")]
    public int Index { get; init; }

    [JsonPropertyName("name")]
    public string Name { get; init; } = string.Empty;

    [JsonPropertyName("size")]
    public long Size { get; init; }

    [JsonPropertyName("progress")]
    public double Progress { get; init; }

    [JsonPropertyName("priority")]
    public int Priority { get; init; }
}
