using CommunityToolkit.Mvvm.ComponentModel;
using Microsoft.UI.Xaml.Media;
using Windows.UI;

namespace Getly.UI.ViewModels;

public partial class GrabbedResourceItem : ObservableObject
{
    [ObservableProperty]
    private bool _isSelected = true;

    [ObservableProperty]
    private string _url = string.Empty;

    [ObservableProperty]
    private string _filename = string.Empty;

    [ObservableProperty]
    private string _category = "Altro";

    [ObservableProperty]
    private string _sourceTag = "link";

    public Brush CategoryBackground => Category switch
    {
        "Compressi" => new SolidColorBrush(Color.FromArgb(45, 245, 158, 11)), // Amber
        "Documenti" => new SolidColorBrush(Color.FromArgb(45, 59, 130, 246)), // Blue
        "Video" => new SolidColorBrush(Color.FromArgb(45, 239, 68, 68)),     // Red
        "Audio" => new SolidColorBrush(Color.FromArgb(45, 168, 85, 247)),   // Purple
        "Immagini" => new SolidColorBrush(Color.FromArgb(45, 16, 185, 129)), // Emerald
        "Software" => new SolidColorBrush(Color.FromArgb(45, 14, 165, 233)), // Sky
        _ => new SolidColorBrush(Color.FromArgb(40, 107, 114, 128))          // Gray
    };
}
