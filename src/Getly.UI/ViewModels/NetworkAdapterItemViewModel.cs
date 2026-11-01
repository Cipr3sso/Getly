using CommunityToolkit.Mvvm.ComponentModel;
using Getly.UI.Models;

namespace Getly.UI.ViewModels;

public partial class NetworkAdapterItemViewModel : ObservableObject
{
    [ObservableProperty]
    private string _name = string.Empty;

    [ObservableProperty]
    private string _displayName = string.Empty;

    [ObservableProperty]
    private string _ip = "0.0.0.0";

    [ObservableProperty]
    private bool _isUp = true;

    [ObservableProperty]
    private bool _isPrimary;

    [ObservableProperty]
    private bool _isVpn;

    [ObservableProperty]
    private string _speedFormatted = "N/D";

    [ObservableProperty]
    private bool _isEnabled = true;

    [ObservableProperty]
    private string _statusDisplay = "Pronta";

    [ObservableProperty]
    private string _statusColor = "#107C41"; // Emerald Green

    [ObservableProperty]
    private string _typeBadge = "LAN / Wi-Fi";

    [ObservableProperty]
    private string _typeBadgeColor = "#0078D4";

    public NetworkAdapterItemViewModel() { }

    public NetworkAdapterItemViewModel(NetworkInterfaceDto dto)
    {
        UpdateFrom(dto);
    }

    public void UpdateFrom(NetworkInterfaceDto dto)
    {
        Name = dto.Name;
        DisplayName = !string.IsNullOrWhiteSpace(dto.FriendlyName) ? dto.FriendlyName : dto.Name;
        Ip = !string.IsNullOrWhiteSpace(dto.Ip) ? dto.Ip : "0.0.0.0";
        IsUp = dto.IsUp;
        IsPrimary = dto.IsPrimary;
        IsVpn = dto.IsVpn;
        IsEnabled = dto.IsEnabled;

        SpeedFormatted = dto.SpeedMbps.HasValue && dto.SpeedMbps.Value > 0
            ? $"{dto.SpeedMbps.Value:0} Mbps"
            : (dto.IsUp ? "1 Gbps (stimata)" : "Disconnessa");

        if (IsVpn)
        {
            TypeBadge = "VPN NordLynx / Killswitch";
            TypeBadgeColor = "#107C41";
        }
        else if (DisplayName.Contains("Wi-Fi", System.StringComparison.OrdinalIgnoreCase) || Name.Contains("Wi-Fi", System.StringComparison.OrdinalIgnoreCase))
        {
            TypeBadge = "Wi-Fi";
            TypeBadgeColor = "#0078D4";
        }
        else
        {
            TypeBadge = "Ethernet";
            TypeBadgeColor = "#D83B01";
        }

        if (!IsUp)
        {
            StatusDisplay = "Non collegata";
            StatusColor = "#9CA3AF";
        }
        else if (!IsEnabled)
        {
            StatusDisplay = "Esclusa dal pool";
            StatusColor = "#D83B01";
        }
        else if (IsPrimary)
        {
            StatusDisplay = "Principale (Gateway predefinito)";
            StatusColor = "#107C41";
        }
        else
        {
            StatusDisplay = "Aggregata (Multi-Interface)";
            StatusColor = "#3B82F6";
        }
    }
}
