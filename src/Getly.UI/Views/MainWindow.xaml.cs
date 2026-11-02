using System;
using System.Collections.ObjectModel;
using System.Diagnostics;
using System.IO;
using System.Linq;
using System.Runtime.InteropServices;
using System.Text.RegularExpressions;
using System.Threading.Tasks;
using Getly.UI.Services;
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
    private FloatingDownloadWindow? _floatingWindow;
    private readonly SystemTrayManager _trayManager = new();

    #region Win32 WndProc & Minimum Window Size (WM_GETMINMAXINFO)

    private delegate IntPtr WndProcDelegate(IntPtr hWnd, uint msg, IntPtr wParam, IntPtr lParam);
    private readonly WndProcDelegate _wndProcDelegate;
    private readonly IntPtr _oldWndProc;

    private const int GWLP_WNDPROC = -4;
    private const uint WM_CLOSE = 0x0010;
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

    [DllImport("user32.dll")]
    [return: MarshalAs(UnmanagedType.Bool)]
    private static extern bool IsIconic(IntPtr hWnd);

    [DllImport("user32.dll")]
    private static extern IntPtr GetForegroundWindow();

    private IntPtr HookWndProc(IntPtr hWnd, uint msg, IntPtr wParam, IntPtr lParam)
    {
        if (_trayManager.HandleWndProc(hWnd, msg, wParam, lParam, out bool handled) != IntPtr.Zero || handled)
        {
            return IntPtr.Zero;
        }

        if (msg == WM_CLOSE && !_trayManager.IsExplicitExit && ViewModel.MinimizeToTrayOnClose)
        {
            _trayManager.MinimizeToTray();
            return IntPtr.Zero;
        }

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

        // 1. Integrated Titlebar & Theme (Full Mica / Fluent TitleBar)
        ExtendsContentIntoTitleBar = true;
        ApplyTheme(ViewModel.AppTheme);
        SetTitleBar(TitleDragArea);

        if (this.Content is FrameworkElement rootElem)
        {
            rootElem.ActualThemeChanged += (s, e) =>
            {
                UpdateTitleBarColors(rootElem.ActualTheme);
                _floatingWindow?.SetTheme(rootElem.ActualTheme);
            };
        }

        // 2. Intercept Win32 WndProc to enforce physical unpassable min dimensions
        _wndProcDelegate = HookWndProc;
        var hwnd = WindowNative.GetWindowHandle(this);
        _oldWndProc = SetWindowLongPtr(hwnd, GWLP_WNDPROC, Marshal.GetFunctionPointerForDelegate(_wndProcDelegate));

        // Launch background supervisor and connect IPC
        Activated += MainWindow_Activated;

        // Mini Floating Download Window integration
        ViewModel.TaskNotificationTriggered += OnTaskNotificationTriggered;
        AppWindow.Changed += AppWindow_Changed;

        // System Tray Integration & Window Icon
        _trayManager.Initialize(this, AppWindow, ViewModel);
        AppWindow.Closing += AppWindow_Closing;
        Closed += (s, e) =>
        {
            _trayManager.Dispose();
            ViewModel.Dispose();
        };
        SetAppIcon();

        // Default initial selection
        if (NavView.MenuItems.Count > 0)
        {
            NavView.SelectedItem = NavView.MenuItems[0];
        }
    }

    private void AppWindow_Closing(AppWindow sender, AppWindowClosingEventArgs args)
    {
        if (!_trayManager.IsExplicitExit && ViewModel.MinimizeToTrayOnClose)
        {
            args.Cancel = true;
            _trayManager.MinimizeToTray();
        }
        else
        {
            _trayManager.RemoveTrayIcon();
        }
    }

    private void SetAppIcon()
    {
        try
        {
            string[] iconCandidates = new[]
            {
                System.IO.Path.Combine(AppContext.BaseDirectory, "Assets", "app_icon.ico"),
                System.IO.Path.Combine(AppContext.BaseDirectory, "assets", "app_icon.ico"),
                System.IO.Path.Combine(AppContext.BaseDirectory, "app_icon.ico")
            };
            foreach (var p in iconCandidates)
            {
                if (System.IO.File.Exists(p))
                {
                    AppWindow.SetIcon(p);
                    break;
                }
            }
        }
        catch { }
    }

    private async void MainWindow_Activated(object sender, WindowActivatedEventArgs args)
    {
        if (!_initialized)
        {
            _initialized = true;
            await ViewModel.InitializeAsync();
        }
    }

    #region Theme Management (Light / Dark Mode)

    public void ApplyTheme(string themeName)
    {
        ElementTheme requestedTheme;
        if (string.Equals(themeName, "Light", StringComparison.OrdinalIgnoreCase))
        {
            requestedTheme = ElementTheme.Light;
        }
        else if (string.Equals(themeName, "Dark", StringComparison.OrdinalIgnoreCase))
        {
            requestedTheme = ElementTheme.Dark;
        }
        else
        {
            requestedTheme = ElementTheme.Default; // Segui sistema
        }

        if (this.Content is FrameworkElement root)
        {
            root.RequestedTheme = requestedTheme;
            UpdateTitleBarColors(root.ActualTheme);
            _floatingWindow?.SetTheme(root.ActualTheme);
        }

        if (ThemeModeComboBox is not null)
        {
            ThemeModeComboBox.SelectedIndex = requestedTheme switch
            {
                ElementTheme.Dark => 1,
                ElementTheme.Light => 2,
                _ => 0
            };
        }
    }

    private async void ThemeModeComboBox_SelectionChanged(object sender, SelectionChangedEventArgs e)
    {
        if (ThemeModeComboBox?.SelectedItem is ComboBoxItem item && item.Tag is string tag)
        {
            if (!string.Equals(ViewModel.AppTheme, tag, StringComparison.OrdinalIgnoreCase))
            {
                ViewModel.AppTheme = tag;
                ApplyTheme(tag);
                await ViewModel.SaveSettingsAsync();
            }
        }
    }

    private void UpdateTitleBarColors(ElementTheme theme)
    {
        if (AppWindowTitleBar.IsCustomizationSupported())
        {
            var titleBar = AppWindow.TitleBar;
            bool isDark = theme == ElementTheme.Dark;
            var fg = isDark ? Microsoft.UI.Colors.White : Microsoft.UI.Colors.Black;

            titleBar.ButtonBackgroundColor = Microsoft.UI.Colors.Transparent;
            titleBar.ButtonInactiveBackgroundColor = Microsoft.UI.Colors.Transparent;
            titleBar.ButtonForegroundColor = fg;
            titleBar.ButtonHoverForegroundColor = fg;
            titleBar.ButtonHoverBackgroundColor = isDark
                ? Windows.UI.Color.FromArgb(30, 255, 255, 255)
                : Windows.UI.Color.FromArgb(20, 0, 0, 0);
            titleBar.ButtonPressedBackgroundColor = isDark
                ? Windows.UI.Color.FromArgb(50, 255, 255, 255)
                : Windows.UI.Color.FromArgb(40, 0, 0, 0);
            titleBar.ButtonPressedForegroundColor = fg;
            titleBar.ButtonInactiveForegroundColor = isDark
                ? Windows.UI.Color.FromArgb(128, 255, 255, 255)
                : Windows.UI.Color.FromArgb(128, 0, 0, 0);
        }
    }

    private Microsoft.UI.Xaml.Media.Brush GetThemeSecondaryBrush()
    {
        bool isDark = (this.Content as FrameworkElement)?.ActualTheme == ElementTheme.Dark;
        return isDark
            ? new Microsoft.UI.Xaml.Media.SolidColorBrush(Windows.UI.Color.FromArgb(204, 255, 255, 255))
            : new Microsoft.UI.Xaml.Media.SolidColorBrush(Windows.UI.Color.FromArgb(170, 0, 0, 0));
    }

    private Microsoft.UI.Xaml.Media.Brush GetThemePrimaryBrush()
    {
        bool isDark = (this.Content as FrameworkElement)?.ActualTheme == ElementTheme.Dark;
        return isDark
            ? new Microsoft.UI.Xaml.Media.SolidColorBrush(Microsoft.UI.Colors.White)
            : new Microsoft.UI.Xaml.Media.SolidColorBrush(Microsoft.UI.Colors.Black);
    }

    private Microsoft.UI.Xaml.Media.Brush GetThemeBorderBrush()
    {
        bool isDark = (this.Content as FrameworkElement)?.ActualTheme == ElementTheme.Dark;
        return isDark
            ? new Microsoft.UI.Xaml.Media.SolidColorBrush(Windows.UI.Color.FromArgb(38, 255, 255, 255))
            : new Microsoft.UI.Xaml.Media.SolidColorBrush(Windows.UI.Color.FromArgb(25, 0, 0, 0));
    }

    #endregion

    #region Floating Mini Download Window

    public void ShowFloatingWindow(TaskItemViewModel task)
    {
        if (!ViewModel.EnableFloatingWindow) return;

        if (_floatingWindow is null)
        {
            _floatingWindow = new FloatingDownloadWindow(ViewModel);
            if (this.Content is FrameworkElement root)
            {
                _floatingWindow.SetTheme(root.ActualTheme);
            }
            _floatingWindow.Closed += (s, e) => _floatingWindow = null;
        }

        _floatingWindow.AddOrUpdateTask(task);
        _floatingWindow.AppWindow.IsShownInSwitchers = false;
        _floatingWindow.Activate();
    }

    private void OnTaskNotificationTriggered(TaskItemViewModel task)
    {
        if (ViewModel.EnableFloatingWindow && IsMainWindowMinimizedOrBackground())
        {
            DispatcherQueue.TryEnqueue(() => ShowFloatingWindow(task));
        }
    }

    private void AppWindow_Changed(AppWindow sender, AppWindowChangedEventArgs args)
    {
        if (AppWindow.Presenter is OverlappedPresenter op && op.State == OverlappedPresenterState.Minimized)
        {
            if (ViewModel.EnableFloatingWindow)
            {
                var activeTask = ViewModel.Tasks.FirstOrDefault(t => t.IsActive);
                if (activeTask != null)
                {
                    DispatcherQueue.TryEnqueue(() => ShowFloatingWindow(activeTask));
                }
            }
        }
    }

    private bool IsMainWindowMinimizedOrBackground()
    {
        if (AppWindow.Presenter is OverlappedPresenter op && op.State == OverlappedPresenterState.Minimized)
        {
            return true;
        }

        var hwnd = WindowNative.GetWindowHandle(this);
        if (IsIconic(hwnd))
        {
            return true;
        }

        var fgHwnd = GetForegroundWindow();
        return fgHwnd != hwnd;
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
                _ = ViewModel.RefreshNetworkAdaptersCommand.ExecuteAsync(null);
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

            bool isInDeleted = ViewModel.SelectedNavCategory == "DELETED";
            string title = isInDeleted
                ? (selectedTasks.Count == 1 ? $"Eliminare definitivamente '{selectedTasks[0].Filename}'?" : $"Eliminare definitivamente i {selectedTasks.Count} download?")
                : (selectedTasks.Count == 1 ? $"Spostare in Eliminati '{selectedTasks[0].Filename}'?" : $"Spostare in Eliminati i {selectedTasks.Count} download?");

            var dialog = new ContentDialog
            {
                Title = title,
                PrimaryButtonText = isInDeleted ? "Elimina definitivamente" : "Sposta in Eliminati",
                CloseButtonText = "Annulla",
                DefaultButton = ContentDialogButton.Close,
                Content = new StackPanel
                {
                    Spacing = 8,
                    Children =
                    {
                        new TextBlock
                        {
                            Text = isInDeleted
                                ? $"Stai per eliminare definitivamente {selectedTasks.Count} download dal database."
                                : $"Stai per spostare {selectedTasks.Count} download nella sezione 'Eliminati'. Potrai ripristinarli in qualsiasi momento.",
                            TextWrapping = TextWrapping.Wrap,
                            Foreground = GetThemeSecondaryBrush()
                        },
                        deleteDiskFilesBox
                    }
                }
            };
            PrepareDialog(dialog);

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

    private void OnRestoreTaskClick(object sender, RoutedEventArgs e)
    {
        var task = GetTask(sender);
        if (task != null)
        {
            ViewModel.RestoreTaskCommand.Execute(task);
        }
    }

    private void OnPermanentDeleteClick(object sender, RoutedEventArgs e)
    {
        var task = GetTask(sender);
        if (task != null)
        {
            ViewModel.DeleteTaskCommand.Execute(task);
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

    private void OnSolveCloudflareClick(object sender, RoutedEventArgs e)
    {
        var task = GetTask(sender);
        if (task == null) return;
        _ = ShowCloudflareSolveDialogAsync(task);
    }

    private void OnShowFloatingWindowClick(object sender, RoutedEventArgs e)
    {
        var task = GetTask(sender);
        if (task == null) return;
        ShowFloatingWindow(task);
    }

    private void OnShowDetailsClick(object sender, RoutedEventArgs e)
    {
        var task = GetTask(sender);
        if (task == null) return;
        _ = ShowTaskDetailsDialogAsync(task);
    }

    private void OnRowDoubleTapped(object sender, DoubleTappedRoutedEventArgs e)
    {
        e.Handled = true;
        var task = GetTask(sender) ?? ViewModel.SelectedTask;
        if (task == null) return;

        _ = ShowTaskDetailsDialogAsync(task);
    }

    #endregion

    #region Dialogs

    private void PrepareDialog(ContentDialog dialog)
    {
        if (this.Content is FrameworkElement root)
        {
            dialog.XamlRoot = root.XamlRoot;
            dialog.RequestedTheme = root.ActualTheme;
            if (dialog.Content is FrameworkElement contentElem)
            {
                contentElem.RequestedTheme = root.ActualTheme;
            }
        }
    }

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
                Content = new StackPanel
                {
                    Spacing = 10,
                    Children =
                    {
                        new TextBlock
                        {
                            Text = "Inserisci l'URL del file da aggiungere alla coda di Getly:",
                            FontSize = 12,
                            Foreground = GetThemeSecondaryBrush()
                        },
                        urlBox
                    }
                }
            };
            PrepareDialog(dialog);

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

    private static readonly Regex BatchUrlRegex = new(
        @"(https?://[^\s""'<>]+|magnet:\?[^\s""'<>]+)",
        RegexOptions.Compiled | RegexOptions.IgnoreCase
    );

    private async void OnBatchDownloadClick(object sender, RoutedEventArgs e)
    {
        if (_isDialogOpen) return;
        _isDialogOpen = true;

        try
        {
            var batchItems = new ObservableCollection<BatchDownloadItem>();

            var inputTextBox = new TextBox
            {
                PlaceholderText = "Incolla qui una lista di URL o del testo contenente link (HTTP, HTTPS, Magnet)...",
                AcceptsReturn = true,
                TextWrapping = TextWrapping.Wrap,
                Height = 110,
                Width = 520
            };

            var analyzeButton = new Button
            {
                Content = "Analizza URL",
                Style = Application.Current.Resources["AccentButtonStyle"] as Style,
                HorizontalAlignment = HorizontalAlignment.Left
            };

            var countTextBlock = new TextBlock
            {
                Text = "Nessun URL analizzato.",
                FontSize = 11,
                Foreground = GetThemeSecondaryBrush(),
                VerticalAlignment = VerticalAlignment.Center,
                Margin = new Thickness(12, 0, 0, 0)
            };

            var selectAllCheckBox = new CheckBox
            {
                Content = "Seleziona Tutti",
                IsChecked = true,
                FontSize = 11,
                VerticalAlignment = VerticalAlignment.Center,
                Visibility = Visibility.Collapsed
            };

            var itemsListView = new ListView
            {
                ItemsSource = batchItems,
                SelectionMode = ListViewSelectionMode.None,
                Height = 180,
                BorderThickness = new Thickness(1),
                BorderBrush = GetThemeBorderBrush(),
                CornerRadius = new CornerRadius(6),
                Padding = new Thickness(4),
                Visibility = Visibility.Collapsed
            };

            if (RootGrid.Resources.TryGetValue("BatchItemTemplate", out var templateObj) && templateObj is DataTemplate dt)
            {
                itemsListView.ItemTemplate = dt;
            }

            analyzeButton.Click += (s, args) =>
            {
                batchItems.Clear();
                string text = inputTextBox.Text ?? string.Empty;
                var matches = BatchUrlRegex.Matches(text);
                var seen = new System.Collections.Generic.HashSet<string>(StringComparer.OrdinalIgnoreCase);

                foreach (Match m in matches)
                {
                    string url = m.Value.Trim();
                    if (!string.IsNullOrEmpty(url) && seen.Add(url))
                    {
                        string filename = BatchDownloadItem.DeduceFilename(url);
                        string category = BatchDownloadItem.DeduceCategory(filename, url);
                        batchItems.Add(new BatchDownloadItem
                        {
                            Url = url,
                            Filename = filename,
                            Category = category,
                            IsSelected = true
                        });
                    }
                }

                if (batchItems.Count > 0)
                {
                    countTextBlock.Text = $"Trovati {batchItems.Count} link validi.";
                    itemsListView.Visibility = Visibility.Visible;
                    selectAllCheckBox.Visibility = Visibility.Visible;
                    selectAllCheckBox.IsChecked = true;
                }
                else
                {
                    countTextBlock.Text = "Nessun URL valido trovato nel testo inserito.";
                    itemsListView.Visibility = Visibility.Collapsed;
                    selectAllCheckBox.Visibility = Visibility.Collapsed;
                }
            };

            selectAllCheckBox.Click += (s, args) =>
            {
                bool isChecked = selectAllCheckBox.IsChecked ?? false;
                foreach (var item in batchItems)
                {
                    item.IsSelected = isChecked;
                }
            };

            var analyzeRow = new StackPanel
            {
                Orientation = Orientation.Horizontal,
                Spacing = 4,
                Children = { analyzeButton, countTextBlock }
            };

            var listHeader = new Grid
            {
                Margin = new Thickness(0, 4, 0, 0),
                Children = { selectAllCheckBox }
            };

            var dialogContent = new StackPanel
            {
                Spacing = 10,
                Width = 520,
                Children =
                {
                    new TextBlock
                    {
                        Text = "Incolla un testo o un elenco di link per estrarre e accodare i file in blocco:",
                        FontSize = 12,
                        Foreground = GetThemeSecondaryBrush()
                    },
                    inputTextBox,
                    analyzeRow,
                    listHeader,
                    itemsListView
                }
            };

            var dialog = new ContentDialog
            {
                Title = "Download Batch",
                PrimaryButtonText = "Avvia Selezionati",
                SecondaryButtonText = "Aggiungi in Coda",
                CloseButtonText = "Annulla",
                DefaultButton = ContentDialogButton.Primary,
                Content = dialogContent
            };

            PrepareDialog(dialog);

            var result = await dialog.ShowAsync();
            var selectedItems = batchItems.Where(i => i.IsSelected).ToList();

            if (result == ContentDialogResult.Primary && selectedItems.Count > 0)
            {
                foreach (var item in selectedItems)
                {
                    await ViewModel.AddDownloadWithParamsAsync(item.Url, item.Category, startPaused: false, filename: item.Filename);
                }
                ViewModel.StatusMessage = $"Avviati {selectedItems.Count} download batch.";
            }
            else if (result == ContentDialogResult.Secondary && selectedItems.Count > 0)
            {
                foreach (var item in selectedItems)
                {
                    await ViewModel.AddDownloadWithParamsAsync(item.Url, item.Category, startPaused: true, filename: item.Filename);
                }
                ViewModel.StatusMessage = $"Aggiunti in coda {selectedItems.Count} download batch.";
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
                    FontWeight = Microsoft.UI.Text.FontWeights.SemiBold,
                    Foreground = GetThemeSecondaryBrush()
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

                if (task.IsCloudflareError)
                {
                    var solveBtn = new Button
                    {
                        Content = new StackPanel
                        {
                            Orientation = Orientation.Horizontal,
                            Spacing = 6,
                            Children =
                            {
                                new FontIcon { Glyph = "\uE727", FontSize = 12, Foreground = new Microsoft.UI.Xaml.Media.SolidColorBrush(Windows.UI.Color.FromArgb(255, 245, 158, 11)) },
                                new TextBlock { Text = "Risolvi verifica Cloudflare nel browser", FontWeight = Microsoft.UI.Text.FontWeights.SemiBold, Foreground = new Microsoft.UI.Xaml.Media.SolidColorBrush(Windows.UI.Color.FromArgb(255, 245, 158, 11)) }
                            }
                        },
                        Background = new Microsoft.UI.Xaml.Media.SolidColorBrush(Windows.UI.Color.FromArgb(30, 245, 158, 11)),
                        BorderBrush = new Microsoft.UI.Xaml.Media.SolidColorBrush(Windows.UI.Color.FromArgb(120, 245, 158, 11)),
                        BorderThickness = new Thickness(1),
                        CornerRadius = new CornerRadius(4),
                        Margin = new Thickness(0, 8, 0, 0),
                        HorizontalAlignment = HorizontalAlignment.Left
                    };
                    solveBtn.Click += (s, args) =>
                    {
                        try
                        {
                            System.Diagnostics.Process.Start(new System.Diagnostics.ProcessStartInfo
                            {
                                FileName = task.Url,
                                UseShellExecute = true
                            });
                            ViewModel.StatusMessage = $"Aperto nel browser: risolvi la verifica per {task.Filename}";
                        }
                        catch { }
                    };
                    errorPanel.Children.Add(solveBtn);
                }

                contentPanel.Children.Add(errorPanel);
            }

            AddRow("Progresso Dimensioni", task.DownloadedFormatted);
            AddRow("Velocità Corrente", task.SpeedFormatted);
            AddRow("Tempo Stimato Rimanente (ETA)", task.EtaFormatted);

            bool isTorrent = task.IsTorrent
                || (task.Url != null && task.Url.StartsWith("magnet:", StringComparison.OrdinalIgnoreCase))
                || (task.Filename != null && task.Filename.EndsWith(".torrent", StringComparison.OrdinalIgnoreCase));

            if (isTorrent)
            {
                contentPanel.Width = 560;
                var files = task.Files;
                if (files == null || files.Count == 0)
                {
                    files = await ViewModel.GetTorrentFilesAsync(task.TaskId);
                }

                if (task.PiecesTotal > 0 || task.Seeds > 0 || task.Peers > 0)
                {
                    AddRow("Parti / Pezzi Torrent", $"{task.PiecesCompleted} su {task.PiecesTotal} completati ({task.Seeds} Seed, {task.Peers} Peer)");
                }

                if (files != null && files.Count > 0)
                {
                    var filesSection = new StackPanel { Spacing = 6, Margin = new Thickness(0, 6, 0, 0) };
                    filesSection.Children.Add(new TextBlock
                    {
                        Text = $"Parti e File del Torrent ({files.Count} elementi):",
                        FontSize = 12,
                        FontWeight = Microsoft.UI.Text.FontWeights.SemiBold,
                        Foreground = GetThemeSecondaryBrush()
                    });

                    var headerGrid = new Grid
                    {
                        Margin = new Thickness(8, 4, 8, 4),
                        ColumnDefinitions =
                        {
                            new ColumnDefinition { Width = new GridLength(1, GridUnitType.Star) },
                            new ColumnDefinition { Width = new GridLength(90, GridUnitType.Pixel) },
                            new ColumnDefinition { Width = new GridLength(90, GridUnitType.Pixel) },
                            new ColumnDefinition { Width = new GridLength(110, GridUnitType.Pixel) }
                        }
                    };

                    TextBlock MakeHeader(string title, int col, TextAlignment align = TextAlignment.Left)
                    {
                        var tb = new TextBlock
                        {
                            Text = title,
                            FontSize = 11,
                            FontWeight = Microsoft.UI.Text.FontWeights.SemiBold,
                            Foreground = GetThemeSecondaryBrush(),
                            TextAlignment = align
                        };
                        Grid.SetColumn(tb, col);
                        return tb;
                    }

                    headerGrid.Children.Add(MakeHeader("File / Parte", 0));
                    headerGrid.Children.Add(MakeHeader("Scaricato", 1, TextAlignment.Right));
                    headerGrid.Children.Add(MakeHeader("Totale", 2, TextAlignment.Right));
                    headerGrid.Children.Add(MakeHeader("Progresso", 3, TextAlignment.Center));

                    var filesStack = new StackPanel { Spacing = 4 };
                    foreach (var f in files)
                    {
                        var rowGrid = new Grid
                        {
                            Padding = new Thickness(8, 6, 8, 6),
                            Background = new Microsoft.UI.Xaml.Media.SolidColorBrush(Windows.UI.Color.FromArgb(12, 255, 255, 255)),
                            CornerRadius = new CornerRadius(4),
                            ColumnDefinitions =
                            {
                                new ColumnDefinition { Width = new GridLength(1, GridUnitType.Star) },
                                new ColumnDefinition { Width = new GridLength(90, GridUnitType.Pixel) },
                                new ColumnDefinition { Width = new GridLength(90, GridUnitType.Pixel) },
                                new ColumnDefinition { Width = new GridLength(110, GridUnitType.Pixel) }
                            }
                        };

                        var nameBlock = new TextBlock
                        {
                            Text = !string.IsNullOrEmpty(f.Name) ? f.Name : (!string.IsNullOrEmpty(f.Path) ? System.IO.Path.GetFileName(f.Path) : $"Parte #{f.Index + 1}"),
                            FontSize = 11,
                            TextTrimming = TextTrimming.CharacterEllipsis,
                            VerticalAlignment = VerticalAlignment.Center
                        };
                        ToolTipService.SetToolTip(nameBlock, f.Path ?? f.Name);
                        Grid.SetColumn(nameBlock, 0);
                        rowGrid.Children.Add(nameBlock);

                        var dlBlock = new TextBlock
                        {
                            Text = TaskItemViewModel.FormatBytes(f.Downloaded),
                            FontSize = 11,
                            TextAlignment = TextAlignment.Right,
                            Foreground = GetThemeSecondaryBrush(),
                            VerticalAlignment = VerticalAlignment.Center
                        };
                        Grid.SetColumn(dlBlock, 1);
                        rowGrid.Children.Add(dlBlock);

                        var sizeBlock = new TextBlock
                        {
                            Text = TaskItemViewModel.FormatBytes(f.Size),
                            FontSize = 11,
                            TextAlignment = TextAlignment.Right,
                            Foreground = GetThemeSecondaryBrush(),
                            VerticalAlignment = VerticalAlignment.Center
                        };
                        Grid.SetColumn(sizeBlock, 2);
                        rowGrid.Children.Add(sizeBlock);

                        var progPanel = new StackPanel
                        {
                            Spacing = 2,
                            VerticalAlignment = VerticalAlignment.Center,
                            Margin = new Thickness(8, 0, 0, 0)
                        };
                        var pBar = new ProgressBar
                        {
                            Minimum = 0,
                            Maximum = 100,
                            Value = Math.Clamp(f.Progress * 100.0, 0.0, 100.0),
                            Height = 4,
                            Foreground = new Microsoft.UI.Xaml.Media.SolidColorBrush(Windows.UI.Color.FromArgb(255, 16, 185, 129))
                        };
                        var pText = new TextBlock
                        {
                            Text = $"{f.Progress * 100.0:0.#}%",
                            FontSize = 10,
                            HorizontalAlignment = HorizontalAlignment.Center,
                            Foreground = GetThemeSecondaryBrush()
                        };
                        progPanel.Children.Add(pBar);
                        progPanel.Children.Add(pText);
                        Grid.SetColumn(progPanel, 3);
                        rowGrid.Children.Add(progPanel);

                        filesStack.Children.Add(rowGrid);
                    }

                    var filesContainer = new Border
                    {
                        BorderBrush = GetThemeBorderBrush(),
                        BorderThickness = new Thickness(1),
                        CornerRadius = new CornerRadius(6),
                        Padding = new Thickness(4),
                        Child = new ScrollViewer
                        {
                            MaxHeight = 220,
                            Content = filesStack
                        }
                    };

                    filesSection.Children.Add(headerGrid);
                    filesSection.Children.Add(filesContainer);
                    contentPanel.Children.Add(filesSection);
                }
            }

            var dialog = new ContentDialog
            {
                Title = isTorrent ? "Dettagli del Download (Torrent/P2P)" : "Dettagli del Download",
                CloseButtonText = "Chiudi",
                DefaultButton = ContentDialogButton.Close,
                Content = new ScrollViewer
                {
                    MaxHeight = isTorrent ? 520 : 420,
                    Content = contentPanel
                }
            };
            PrepareDialog(dialog);

            await dialog.ShowAsync();
        }
        finally
        {
            _isDialogOpen = false;
        }
    }

    private async Task ShowCloudflareSolveDialogAsync(TaskItemViewModel task)
    {
        if (_isDialogOpen) return;
        _isDialogOpen = true;

        try
        {
            var dialog = new ContentDialog
            {
                Title = "Verifica Cloudflare / WAF Richiesta",
                PrimaryButtonText = "Apri nel Browser",
                SecondaryButtonText = "Riprova / Riprendi",
                CloseButtonText = "Chiudi",
                DefaultButton = ContentDialogButton.Primary
            };

            var stack = new StackPanel { Spacing = 12 };
            stack.Children.Add(new TextBlock
            {
                Text = $"Il download di \"{task.Filename}\" è protetto dalla verifica di sicurezza Cloudflare (Turnstile / WAF).",
                TextWrapping = TextWrapping.Wrap,
                FontSize = 13,
                Foreground = GetThemePrimaryBrush()
            });

            stack.Children.Add(new TextBlock
            {
                Text = "Vuoi aprire il collegamento nel tuo browser per confermare di non essere un bot? Una volta completata la verifica nel browser, potrai riprendere il download.",
                TextWrapping = TextWrapping.Wrap,
                FontSize = 12,
                Foreground = GetThemeSecondaryBrush()
            });

            var urlBox = new TextBox
            {
                Text = task.Url,
                IsReadOnly = true,
                FontSize = 11,
                Margin = new Thickness(0, 4, 0, 0)
            };
            stack.Children.Add(urlBox);

            dialog.Content = stack;
            PrepareDialog(dialog);

            var result = await dialog.ShowAsync();
            if (result == ContentDialogResult.Primary)
            {
                try
                {
                    System.Diagnostics.Process.Start(new System.Diagnostics.ProcessStartInfo
                    {
                        FileName = task.Url,
                        UseShellExecute = true
                    });
                    ViewModel.StatusMessage = $"Aperto nel browser: risolvi la verifica per {task.Filename}";
                }
                catch (Exception ex)
                {
                    System.Diagnostics.Debug.WriteLine($"[MainWindow] Could not launch browser: {ex.Message}");
                }
            }
            else if (result == ContentDialogResult.Secondary)
            {
                ViewModel.ResumeTaskCommand.Execute(task);
            }
        }
        finally
        {
            _isDialogOpen = false;
        }
    }

    #endregion
}
