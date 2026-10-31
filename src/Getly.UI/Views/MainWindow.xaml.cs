using System;
using System.IO;
using System.Threading.Tasks;
using Getly.UI.ViewModels;
using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Controls;
using Microsoft.UI.Xaml.Input;
using Windows.ApplicationModel.DataTransfer;
using Windows.Storage.Pickers;

namespace Getly.UI.Views;

public sealed partial class MainWindow : Window
{
    public MainViewModel ViewModel { get; }
    private bool _initialized;
    private bool _isDialogOpen;

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
            // Settings dialog is handled via NavView_ItemInvoked
            return;
        }

        if (args.SelectedItemContainer is NavigationViewItem item && item.Tag is string tag)
        {
            ViewModel.SelectedNavCategory = tag;
        }
    }

    private async void NavView_ItemInvoked(NavigationView sender, NavigationViewItemInvokedEventArgs args)
    {
        if (args.IsSettingsInvoked)
        {
            await ShowSettingsDialogAsync();
        }
    }

    private TaskItemViewModel? GetTask(object sender)
    {
        if (sender is FrameworkElement fe && fe.DataContext is TaskItemViewModel task)
        {
            return task;
        }
        return ViewModel.SelectedTask;
    }

    #region Context Menu & Row Action Handlers

    private void OnResumeTaskClick(object sender, RoutedEventArgs e)
    {
        var task = GetTask(sender);
        if (task != null)
        {
            ViewModel.ResumeTaskCommand.Execute(task);
        }
    }

    private void OnPauseTaskClick(object sender, RoutedEventArgs e)
    {
        var task = GetTask(sender);
        if (task != null)
        {
            ViewModel.PauseTaskCommand.Execute(task);
        }
    }

    private void OnOpenFolderClick(object sender, RoutedEventArgs e)
    {
        var task = GetTask(sender);
        if (task != null)
        {
            ViewModel.OpenContainingFolderCommand.Execute(task);
        }
    }

    private void OnOpenFileClick(object sender, RoutedEventArgs e)
    {
        var task = GetTask(sender);
        if (task != null)
        {
            ViewModel.OpenFileCommand.Execute(task);
        }
    }

    private void OnCopyUrlClick(object sender, RoutedEventArgs e)
    {
        var task = GetTask(sender);
        if (task != null && !string.IsNullOrWhiteSpace(task.Url))
        {
            var dataPackage = new DataPackage();
            dataPackage.SetText(task.Url);
            Clipboard.SetContent(dataPackage);
            ViewModel.StatusMessage = $"URL copiato negli appunti: {task.Filename}";
        }
    }

    private void OnRemoveFromListClick(object sender, RoutedEventArgs e)
    {
        var task = GetTask(sender);
        if (task != null)
        {
            ViewModel.DeleteTaskCommand.Execute(task);
        }
    }

    private void OnDeleteFromDiskClick(object sender, RoutedEventArgs e)
    {
        var task = GetTask(sender);
        if (task != null)
        {
            ViewModel.DeleteTaskWithFilesCommand.Execute(task);
        }
    }

    private void OnRowDoubleTapped(object sender, DoubleTappedRoutedEventArgs e)
    {
        var task = GetTask(sender);
        if (task == null) return;

        if (task.IsCompleted)
        {
            ViewModel.OpenFileCommand.Execute(task);
        }
        else
        {
            _ = ShowTaskDetailsDialogAsync(task);
        }
    }

    #endregion

    #region Dialogs

    private async void OnAddDownloadClick(object sender, RoutedEventArgs e)
    {
        if (_isDialogOpen) return;
        _isDialogOpen = true;

        try
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
        finally
        {
            _isDialogOpen = false;
        }
    }

    private async Task ShowTaskDetailsDialogAsync(TaskItemViewModel task)
    {
        if (_isDialogOpen) return;
        _isDialogOpen = true;

        try
        {
            var contentPanel = new StackPanel { Spacing = 10, Width = 440 };

            void AddRow(string label, string val)
            {
                var p = new StackPanel { Spacing = 2 };
                p.Children.Add(new TextBlock
                {
                    Text = label,
                    FontSize = 11,
                    Foreground = Application.Current.Resources["TextFillColorSecondaryBrush"] as Microsoft.UI.Xaml.Media.Brush
                });
                p.Children.Add(new TextBox
                {
                    Text = val,
                    IsReadOnly = true,
                    FontSize = 12,
                    TextWrapping = TextWrapping.Wrap
                });
                contentPanel.Children.Add(p);
            }

            AddRow("Nome File", task.Filename);
            AddRow("URL Sorgente", task.Url);
            AddRow("Cartella Salvataggio", task.DestinationDir);
            AddRow("Categoria", task.Category);
            AddRow("Stato", $"{task.StatusDisplay} ({task.ProgressPercentageFormatted})");
            AddRow("Progresso Dimensioni", task.DownloadedFormatted);
            AddRow("Velocità Corrente", task.SpeedFormatted);
            AddRow("Tempo Stimato Rimanente (ETA)", task.EtaFormatted);

            var dialog = new ContentDialog
            {
                Title = "Dettagli del Download",
                CloseButtonText = "Chiudi",
                DefaultButton = ContentDialogButton.Close,
                XamlRoot = Content.XamlRoot,
                Content = new ScrollViewer
                {
                    MaxHeight = 420,
                    Content = contentPanel
                }
            };

            await dialog.ShowAsync();
        }
        finally
        {
            _isDialogOpen = false;
        }
    }

    private async Task ShowSettingsDialogAsync()
    {
        if (_isDialogOpen) return;
        _isDialogOpen = true;

        try
        {
            var folderTextBox = new TextBox
            {
                Text = ViewModel.DefaultDownloadDir,
                HorizontalAlignment = HorizontalAlignment.Stretch
            };

            var browseBtn = new Button
            {
                Content = "Sfoglia...",
                VerticalAlignment = VerticalAlignment.Center
            };

            browseBtn.Click += async (s, e) =>
            {
                try
                {
                    var picker = new FolderPicker();
                    var hwnd = WinRT.Interop.WindowNative.GetWindowHandle(this);
                    WinRT.Interop.InitializeWithWindow.Initialize(picker, hwnd);
                    picker.FileTypeFilter.Add("*");
                    var folder = await picker.PickSingleFolderAsync();
                    if (folder != null)
                    {
                        folderTextBox.Text = folder.Path;
                    }
                }
                catch
                {
                    // Fallback to manual path input if picker fails
                }
            };

            var folderRow = new Grid();
            folderRow.ColumnDefinitions.Add(new ColumnDefinition { Width = new GridLength(1, GridUnitType.Star) });
            folderRow.ColumnDefinitions.Add(new ColumnDefinition { Width = GridLength.Auto });
            Grid.SetColumn(folderTextBox, 0);
            Grid.SetColumn(browseBtn, 1);
            browseBtn.Margin = new Thickness(8, 0, 0, 0);
            folderRow.Children.Add(folderTextBox);
            folderRow.Children.Add(browseBtn);

            var connsBox = new NumberBox
            {
                Value = ViewModel.MaxConcurrentConnections,
                Minimum = 1,
                Maximum = 32,
                SpinButtonPlacementMode = NumberBoxSpinButtonPlacementMode.Inline,
                SmallChange = 1,
                LargeChange = 4,
                Width = 140,
                HorizontalAlignment = HorizontalAlignment.Left
            };

            var speedLimitBox = new NumberBox
            {
                Value = ViewModel.GlobalSpeedLimitBps > 0 ? ViewModel.GlobalSpeedLimitBps / 1024 : 0,
                Minimum = 0,
                Maximum = 1000000,
                SpinButtonPlacementMode = NumberBoxSpinButtonPlacementMode.Inline,
                SmallChange = 50,
                LargeChange = 500,
                Width = 160,
                HorizontalAlignment = HorizontalAlignment.Left
            };

            var vpnToggle = new ToggleSwitch
            {
                Header = "Protezione automatica NordLynx / Killswitch",
                IsOn = ViewModel.VpnShieldActive
            };

            var content = new StackPanel
            {
                Spacing = 14,
                Width = 460,
                Children =
                {
                    new StackPanel
                    {
                        Spacing = 4,
                        Children =
                        {
                            new TextBlock { Text = "Cartella predefinita per i download", FontWeight = Microsoft.UI.Text.FontWeights.SemiBold, FontSize = 12 },
                            folderRow
                        }
                    },
                    new StackPanel
                    {
                        Spacing = 4,
                        Children =
                        {
                            new TextBlock { Text = "Connessioni concorrenti per file (Chunk)", FontWeight = Microsoft.UI.Text.FontWeights.SemiBold, FontSize = 12 },
                            connsBox
                        }
                    },
                    new StackPanel
                    {
                        Spacing = 4,
                        Children =
                        {
                            new TextBlock { Text = "Limitatore velocità globale (KB/s, 0 = Illimitata)", FontWeight = Microsoft.UI.Text.FontWeights.SemiBold, FontSize = 12 },
                            speedLimitBox
                        }
                    },
                    new Border
                    {
                        Height = 1,
                        Background = Application.Current.Resources["CardStrokeColorDefaultBrush"] as Microsoft.UI.Xaml.Media.Brush
                    },
                    vpnToggle
                }
            };

            var dialog = new ContentDialog
            {
                Title = "Impostazioni Getly",
                PrimaryButtonText = "Salva",
                CloseButtonText = "Annulla",
                DefaultButton = ContentDialogButton.Primary,
                XamlRoot = Content.XamlRoot,
                Content = new ScrollViewer
                {
                    MaxHeight = 440,
                    Content = content
                }
            };

            var result = await dialog.ShowAsync();
            if (result == ContentDialogResult.Primary)
            {
                if (!string.IsNullOrWhiteSpace(folderTextBox.Text))
                {
                    ViewModel.DefaultDownloadDir = folderTextBox.Text.Trim();
                }

                if (!double.IsNaN(connsBox.Value) && connsBox.Value >= 1)
                {
                    ViewModel.MaxConcurrentConnections = (int)connsBox.Value;
                }

                if (!double.IsNaN(speedLimitBox.Value) && speedLimitBox.Value >= 0)
                {
                    ViewModel.GlobalSpeedLimitBps = (long)(speedLimitBox.Value * 1024);
                    _ = ViewModel.IpcClient.SetSpeedLimitAsync(ViewModel.GlobalSpeedLimitBps);
                }

                ViewModel.VpnShieldActive = vpnToggle.IsOn;
                ViewModel.VpnShieldStatusText = vpnToggle.IsOn ? "VPN Shield Attivo" : "VPN Shield Disabilitato";
                ViewModel.StatusMessage = "Impostazioni salvate con successo";
            }
        }
        finally
        {
            _isDialogOpen = false;
        }
    }

    #endregion
}
