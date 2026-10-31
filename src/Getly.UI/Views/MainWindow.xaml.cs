using System;
using System.Threading.Tasks;
using Getly.UI.ViewModels;
using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Controls;

namespace Getly.UI.Views;

public sealed partial class MainWindow : Window
{
    public MainViewModel ViewModel { get; }

    public MainWindow()
    {
        ViewModel = new MainViewModel();
        InitializeComponent();

        // Customise window title and dimensions
        Title = "Getly Download Manager";
        AppWindow.Resize(new Windows.Graphics.SizeInt32(1100, 680));

        // Launch background supervisor and connect IPC
        Activated += MainWindow_Activated;
    }

    private bool _initialized;
    private async void MainWindow_Activated(object sender, WindowActivatedEventArgs args)
    {
        if (!_initialized)
        {
            _initialized = true;
            await ViewModel.InitializeAsync();
        }
    }

    private void NavView_SelectionChanged(NavigationView sender, NavigationViewSelectionChangedEventArgs args)
    {
        if (args.IsSettingsSelected)
        {
            ViewModel.SelectedNavCategory = "SETTINGS";
            return;
        }

        if (args.SelectedItemContainer is NavigationViewItem item && item.Tag is string tag)
        {
            ViewModel.SelectedNavCategory = tag;
        }
    }

    private async void OnAddDownloadClick(object sender, RoutedEventArgs e)
    {
        var urlBox = new TextBox
        {
            PlaceholderText = "Incolla indirizzo HTTP, HTTPS, Magnet link o file .torrent...",
            Width = 420
        };

        var dialog = new ContentDialog
        {
            Title = "Nuovo Download",
            PrimaryButtonText = "Avvia Download",
            CloseButtonText = "Annulla",
            DefaultButton = ContentDialogButton.Primary,
            XamlRoot = Content.XamlRoot,
            Content = new StackPanel
            {
                Spacing = 10,
                Children =
                {
                    new TextBlock
                    {
                        Text = "Inserisci l'URL del file da aggiungere alla coda di Getly:",
                        FontSize = 12,
                        Foreground = Application.Current.Resources["TextFillColorSecondaryBrush"] as Microsoft.UI.Xaml.Media.Brush
                    },
                    urlBox
                }
            }
        };

        var result = await dialog.ShowAsync();
        if (result == ContentDialogResult.Primary && !string.IsNullOrWhiteSpace(urlBox.Text))
        {
            await ViewModel.AddDownloadCommand.ExecuteAsync(urlBox.Text.Trim());
        }
    }
}
