using System;
using System.Diagnostics;
using System.IO;
using System.Linq;
using System.Runtime.InteropServices;
using System.Threading.Tasks;
using Getly.UI.ViewModels;
using Microsoft.UI.Windowing;
using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Controls;
using Microsoft.UI.Xaml.Input;
using Windows.ApplicationModel.DataTransfer;
using Windows.Graphics;
using Windows.Storage.Pickers;
using WinRT.Interop;

namespace Getly.UI.Views;

public sealed partial class MainWindow : Window
{
    public MainViewModel ViewModel { get; }
    private bool _initialized;
    private bool _isDialogOpen;

    #region Win32 WndProc & Minimum Window Size (WM_GETMINMAXINFO)

    private delegate IntPtr WndProcDelegate(IntPtr hWnd, uint msg, IntPtr wParam, IntPtr lParam);
    private readonly WndProcDelegate _wndProcDelegate;
    private readonly IntPtr _oldWndProc;

    private const int GWLP_WNDPROC = -4;
    private const uint WM_GETMINMAXINFO = 0x0024;

    [StructLayout(LayoutKind.Sequential)]
    private struct POINT
    {
        public int x;
        public int y;
    }

    [StructLayout(LayoutKind.Sequential)]
    private struct MINMAXINFO
    {
        public POINT ptReserved;
        public POINT ptMaxSize;
        public POINT ptMaxPosition;
        public POINT ptMinTrackSize;
        public POINT ptMaxTrackSize;
    }

    [DllImport("user32.dll", EntryPoint = "SetWindowLongPtrW", SetLastError = true)]
    private static extern IntPtr SetWindowLongPtr64(IntPtr hWnd, int nIndex, IntPtr dwNewLong);

    [DllImport("user32.dll", EntryPoint = "SetWindowLongW", SetLastError = true)]
    private static extern int SetWindowLong32(IntPtr hWnd, int nIndex, int dwNewLong);

    private static IntPtr SetWindowLongPtr(IntPtr hWnd, int nIndex, IntPtr dwNewLong)
    {
        return IntPtr.Size == 8
            ? SetWindowLongPtr64(hWnd, nIndex, dwNewLong)
            : new IntPtr(SetWindowLong32(hWnd, nIndex, dwNewLong.ToInt32()));
    }

    [DllImport("user32.dll", EntryPoint = "CallWindowProcW")]
    private static extern IntPtr CallWindowProc(IntPtr lpPrevWndFunc, IntPtr hWnd, uint msg, IntPtr wParam, IntPtr lParam);

    [DllImport("user32.dll")]
    private static extern uint GetDpiForWindow(IntPtr hWnd);

    private IntPtr HookWndProc(IntPtr hWnd, uint msg, IntPtr wParam, IntPtr lParam)
    {
        if (msg == WM_GETMINMAXINFO)
        {
            // Populate default metrics and borders first
            CallWindowProc(_oldWndProc, hWnd, msg, wParam, lParam);

            var mmi = Marshal.PtrToStructure<MINMAXINFO>(lParam);
            uint dpi = GetDpiForWindow(hWnd);
            float scale = dpi > 0 ? (float)dpi / 96f : 1.0f;

            // Physical unpassable minimum window size: 800px width x 520px height
            mmi.ptMinTrackSize.x = Math.Max(mmi.ptMinTrackSize.x, (int)(800 * scale));
            mmi.ptMinTrackSize.y = Math.Max(mmi.ptMinTrackSize.y, (int)(520 * scale));

            Marshal.StructureToPtr(mmi, lParam, false);
            return IntPtr.Zero;
        }

        return CallWindowProc(_oldWndProc, hWnd, msg, wParam, lParam);
    }

    #endregion

    public MainWindow()
    {
        ViewModel = new MainViewModel();
        InitializeComponent();

        // Customise window title and dimensions
        Title = "Getly Download Manager";
        AppWindow.Resize(new SizeInt32(1100, 680));

        // 1. Integrated Dark Titlebar (Full Mica / Fluent TitleBar)
        ExtendsContentIntoTitleBar = true;
        UpdateTitleBarColors(ElementTheme.Dark);
        SetTitleBar(AppTitleBar);

        // 2. Intercept Win32 WndProc to enforce physical unpassable min dimensions
        _wndProcDelegate = HookWndProc;
        var hwnd = WindowNative.GetWindowHandle(this);
        _oldWndProc = SetWindowLongPtr(hwnd, GWLP_WNDPROC, Marshal.GetFunctionPointerForDelegate(_wndProcDelegate));

        // Launch background supervisor and connect IPC
        Activated += MainWindow_Activated;

        // Default initial selection
        if (NavView.MenuItems.Count > 0)
        {
            NavView.SelectedItem = NavView.MenuItems[0];
        }
    }

    private async void MainWindow_Activated(object sender, WindowActivatedEventArgs args)
    {
        if (!_initialized)
        {
            _initialized = true;
            await ViewModel.InitializeAsync();
        }
    }

    #region Theme Switcher (Light / Dark Mode)

    private void ThemeToggleButton_Click(object sender, RoutedEventArgs e)
    {
        var currentTheme = RootGrid.ActualTheme;
        var newTheme = currentTheme == ElementTheme.Dark ? ElementTheme.Light : ElementTheme.Dark;
        RootGrid.RequestedTheme = newTheme;
        UpdateThemeIcon(newTheme);
        UpdateTitleBarColors(newTheme);
    }

    private void UpdateThemeIcon(ElementTheme theme)
    {
        // Sole (E706) in Dark mode, Luna (E708) in Light mode
        ThemeToggleIcon.Glyph = theme == ElementTheme.Light ? "\uE708" : "\uE706";
        ToolTipService.SetToolTip(ThemeToggleButton, theme == ElementTheme.Light ? "Passa a tema Scuro" : "Passa a tema Chiaro");
    }

    private void UpdateTitleBarColors(ElementTheme theme)
    {
        if (AppWindowTitleBar.IsCustomizationSupported())
        {
            var titleBar = AppWindow.TitleBar;
            bool isDark = theme != ElementTheme.Light;
            var fg = isDark ? Microsoft.UI.Colors.White : Microsoft.UI.Colors.Black;

            titleBar.ButtonBackgroundColor = Microsoft.UI.Colors.Transparent;
            titleBar.ButtonInactiveBackgroundColor = Microsoft.UI.Colors.Transparent;
            titleBar.ButtonForegroundColor = fg;
            titleBar.ButtonHoverForegroundColor = fg;
            titleBar.ButtonHoverBackgroundColor = isDark
                ? Windows.UI.Color.FromArgb(30, 255, 255, 255)
                : Windows.UI.Color.FromArgb(30, 0, 0, 0);
            titleBar.ButtonPressedBackgroundColor = isDark
                ? Windows.UI.Color.FromArgb(50, 255, 255, 255)
                : Windows.UI.Color.FromArgb(50, 0, 0, 0);
        }
    }

    #endregion

    #region Navigation & View Switching

    private void NavView_SelectionChanged(NavigationView sender, NavigationViewSelectionChangedEventArgs args)
    {
        if (args.IsSettingsSelected)
        {
            SwitchToView("SETTINGS");
            return;
        }

        if (args.SelectedItemContainer is NavigationViewItem item && item.Tag is string tag)
        {
            if (tag == "Network")
            {
                SwitchToView("NETWORK");
            }
            else
            {
                SwitchToView("DOWNLOADS");
                ViewModel.SelectedNavCategory = tag;
            }
        }
    }

    private void NavView_ItemInvoked(NavigationView sender, NavigationViewItemInvokedEventArgs args)
    {
        if (args.IsSettingsInvoked)
        {
            SwitchToView("SETTINGS");
        }
    }

    private void SwitchToView(string viewKey)
    {
        DownloadsView.Visibility = viewKey == "DOWNLOADS" ? Visibility.Visible : Visibility.Collapsed;
        NetworkInterfacesView.Visibility = viewKey == "NETWORK" ? Visibility.Visible : Visibility.Collapsed;
        SettingsView.Visibility = viewKey == "SETTINGS" ? Visibility.Visible : Visibility.Collapsed;

        if (viewKey == "SETTINGS")
        {
            SpeedLimitNumberBox.Value = ViewModel.GlobalSpeedLimitBps > 0 ? ViewModel.GlobalSpeedLimitBps / 1024 : 0;
        }
    }

    #endregion

    #region Batch Selection & Multi-Delete

    private void SelectAllCheckBox_Click(object sender, RoutedEventArgs e)
    {
        if (sender is CheckBox cb)
        {
            ViewModel.SelectAllTasks(cb.IsChecked == true);
        }
    }

    private async void OnDeleteButtonClick(object sender, RoutedEventArgs e)
    {
        var selectedTasks = ViewModel.Tasks.Where(t => t.IsSelected).ToList();
        if (selectedTasks.Count == 0 && ViewModel.SelectedTask is not null)
        {
            selectedTasks.Add(ViewModel.SelectedTask);
        }

        if (selectedTasks.Count == 0)
        {
            ViewModel.StatusMessage = "Nessun download selezionato da eliminare.";
            return;
        }

        if (_isDialogOpen) return;
        _isDialogOpen = true;

        try
        {
            var deleteDiskFilesBox = new CheckBox
            {
                Content = "Rimuovi anche i file scaricati dal disco fisso",
                IsChecked = false,
                Margin = new Thickness(0, 8, 0, 0)
            };

            string title = selectedTasks.Count == 1
                ? $"Eliminare il download '{selectedTasks[0].Filename}'?"
                : $"Eliminare i {selectedTasks.Count} download selezionati?";

            var dialog = new ContentDialog
            {
                Title = title,
                PrimaryButtonText = "Elimina",
                CloseButtonText = "Annulla",
                DefaultButton = ContentDialogButton.Close,
                XamlRoot = Content.XamlRoot,
                Content = new StackPanel
                {
                    Spacing = 8,
                    Children =
                    {
                        new TextBlock
                        {
                            Text = $"Stai per rimuovere {selectedTasks.Count} download dalla coda di Getly.",
                            TextWrapping = TextWrapping.Wrap
                        },
                        deleteDiskFilesBox
                    }
                }
            };

            var result = await dialog.ShowAsync();
            if (result == ContentDialogResult.Primary)
            {
                bool deleteFiles = deleteDiskFilesBox.IsChecked == true;
                await ViewModel.DeleteTasksAsync(selectedTasks, deleteFiles);
                if (SelectAllCheckBox is not null)
                {
                    SelectAllCheckBox.IsChecked = false;
                }
            }
        }
        finally
        {
            _isDialogOpen = false;
        }
    }

    #endregion

    #region Network Adapters Actions

    private async void OnAdapterToggleSwitch_Toggled(object sender, RoutedEventArgs e)
    {
        if (sender is ToggleSwitch ts && ts.DataContext is NetworkAdapterItemViewModel adapter)
        {
            await ViewModel.ToggleAdapterAsync(adapter);
        }
    }

    #endregion

    #region Settings Actions

    private async void OnBrowseDownloadDirClick(object sender, RoutedEventArgs e)
    {
        try
        {
            var picker = new FolderPicker();
            var hwnd = WindowNative.GetWindowHandle(this);
            InitializeWithWindow.Initialize(picker, hwnd);
            picker.FileTypeFilter.Add("*");
            var folder = await picker.PickSingleFolderAsync();
            if (folder != null)
            {
                ViewModel.DefaultDownloadDir = folder.Path;
            }
        }
        catch (Exception ex)
        {
            ViewModel.StatusMessage = $"Errore selezione cartella: {ex.Message}";
        }
    }

    private void OnOpenDownloadDirClick(object sender, RoutedEventArgs e)
    {
        if (!string.IsNullOrWhiteSpace(ViewModel.DefaultDownloadDir))
        {
            if (!Directory.Exists(ViewModel.DefaultDownloadDir))
            {
                Directory.CreateDirectory(ViewModel.DefaultDownloadDir);
            }
            Process.Start("explorer.exe", $"\"{ViewModel.DefaultDownloadDir}\"");
        }
    }

    private async void OnApplySettingsClick(object sender, RoutedEventArgs e)
    {
        if (!double.IsNaN(SpeedLimitNumberBox.Value) && SpeedLimitNumberBox.Value >= 0)
        {
            ViewModel.GlobalSpeedLimitBps = (long)(SpeedLimitNumberBox.Value * 1024);
        }

        await ViewModel.SaveSettingsAsync();
    }

    #endregion

    #region Context Menu & Row Action Handlers

    private TaskItemViewModel? GetTask(object sender)
    {
        if (sender is FrameworkElement fe && fe.DataContext is TaskItemViewModel task)
        {
            return task;
        }
        return ViewModel.SelectedTask;
    }

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

            // Prominent detailed error box if task encountered an error
            if (task.IsError || string.Equals(task.Status, "Error", StringComparison.OrdinalIgnoreCase) || !string.IsNullOrWhiteSpace(task.ErrorMessage))
            {
                var errorPanel = new StackPanel { Spacing = 4 };
                errorPanel.Children.Add(new TextBlock
                {
                    Text = "Motivo dell'Errore / Eccezione:",
                    FontSize = 11,
                    FontWeight = Microsoft.UI.Text.FontWeights.SemiBold,
                    Foreground = new Microsoft.UI.Xaml.Media.SolidColorBrush(Windows.UI.Color.FromArgb(255, 239, 68, 68))
                });

                var errorTextBox = new TextBox
                {
                    Text = !string.IsNullOrWhiteSpace(task.ErrorMessage) ? task.ErrorMessage : "Errore non specificato durante il download o probe",
                    IsReadOnly = true,
                    FontSize = 11,
                    TextWrapping = TextWrapping.Wrap,
                    Foreground = new Microsoft.UI.Xaml.Media.SolidColorBrush(Windows.UI.Color.FromArgb(255, 252, 165, 165)),
                    Background = new Microsoft.UI.Xaml.Media.SolidColorBrush(Windows.UI.Color.FromArgb(40, 239, 68, 68)),
                    BorderBrush = new Microsoft.UI.Xaml.Media.SolidColorBrush(Windows.UI.Color.FromArgb(255, 220, 38, 38)),
                    BorderThickness = new Thickness(1),
                    Padding = new Thickness(8, 6, 8, 6)
                };
                errorPanel.Children.Add(errorTextBox);
                contentPanel.Children.Add(errorPanel);
            }

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

    #endregion
}
