using System;
using System.Collections.Generic;
using System.Collections.ObjectModel;
using System.Diagnostics;
using System.IO;
using System.Linq;
using System.Runtime.InteropServices;
using System.Text.RegularExpressions;
using System.Threading.Tasks;
using Getly.UI.Services;
using Getly.UI.ViewModels;
using Microsoft.Win32;
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
        ViewModel.ConfirmTorrentWithoutVpnPromptAsync = ShowTorrentWithoutVpnWarningDialogAsync;
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

    private Microsoft.UI.Xaml.Media.Brush GetThemeCardBackgroundBrush()
    {
        bool isDark = (this.Content as FrameworkElement)?.ActualTheme == ElementTheme.Dark;
        return isDark
            ? new Microsoft.UI.Xaml.Media.SolidColorBrush(Windows.UI.Color.FromArgb(18, 255, 255, 255))
            : new Microsoft.UI.Xaml.Media.SolidColorBrush(Windows.UI.Color.FromArgb(10, 0, 0, 0));
    }

    private static Microsoft.UI.Xaml.Media.SolidColorBrush TryParseColorBrush(string? hex, Windows.UI.Color fallback)
    {
        if (!string.IsNullOrWhiteSpace(hex) && hex.StartsWith("#") && hex.Length >= 7)
        {
            try
            {
                byte r = Convert.ToByte(hex.Substring(1, 2), 16);
                byte g = Convert.ToByte(hex.Substring(3, 2), 16);
                byte b = Convert.ToByte(hex.Substring(5, 2), 16);
                return new Microsoft.UI.Xaml.Media.SolidColorBrush(Windows.UI.Color.FromArgb(255, r, g, b));
            }
            catch { }
        }
        return new Microsoft.UI.Xaml.Media.SolidColorBrush(fallback);
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
            if (tag == "SiteGrabber")
            {
                // Restore selection so sidebar highlight remains on previous category
                sender.SelectedItem = sender.MenuItems.OfType<NavigationViewItem>().FirstOrDefault(i => (i.Tag as string) == ViewModel.SelectedNavCategory)
                    ?? sender.MenuItems.OfType<NavigationViewItem>().First();
                OnSiteGrabberClick(this, new RoutedEventArgs());
                return;
            }

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

    #region Browser Extension Installation Helpers

    private sealed record BrowserInfo(string Name, string ExePath, string ExtensionsUrl);

    private string GetExtensionDirectory()
    {
        string[] candidates =
        [
            Path.Combine(AppDomain.CurrentDomain.BaseDirectory, "extensions", "chrome"),
            Path.Combine(AppDomain.CurrentDomain.BaseDirectory, "extension"),
        ];

        foreach (var c in candidates)
        {
            if (Directory.Exists(c) && File.Exists(Path.Combine(c, "manifest.json")))
                return c;
        }

        var dir = new DirectoryInfo(AppDomain.CurrentDomain.BaseDirectory);
        while (dir != null)
        {
            string c1 = Path.Combine(dir.FullName, "extensions", "chrome");
            if (Directory.Exists(c1) && File.Exists(Path.Combine(c1, "manifest.json")))
                return c1;

            string c2 = Path.Combine(dir.FullName, "extension");
            if (Directory.Exists(c2) && File.Exists(Path.Combine(c2, "manifest.json")))
                return c2;

            dir = dir.Parent;
        }

        return candidates[0];
    }

    private BrowserInfo GetDefaultBrowserInfo()
    {
        try
        {
            using var userChoiceKey = Registry.CurrentUser.OpenSubKey(@"Software\Microsoft\Windows\Shell\Associations\UrlAssociations\https\UserChoice");
            string progId = userChoiceKey?.GetValue("ProgId") as string ?? "";

            if (!string.IsNullOrWhiteSpace(progId))
            {
                using var cmdKey = Registry.ClassesRoot.OpenSubKey($@"{progId}\shell\open\command");
                string rawCmd = cmdKey?.GetValue("") as string ?? "";
                string exePath = "";

                if (!string.IsNullOrWhiteSpace(rawCmd))
                {
                    if (rawCmd.StartsWith("\""))
                    {
                        int nextQuote = rawCmd.IndexOf('\"', 1);
                        if (nextQuote > 1) exePath = rawCmd.Substring(1, nextQuote - 1);
                    }
                    else
                    {
                        exePath = rawCmd.Split(' ')[0];
                    }
                }

                if (File.Exists(exePath))
                {
                    string lower = exePath.ToLowerInvariant();
                    string progLower = progId.ToLowerInvariant();

                    if (lower.Contains("brave") || progLower.Contains("brave"))
                    {
                        return new BrowserInfo("Brave Browser", exePath, "brave://extensions");
                    }
                    if (lower.Contains("chrome") || progLower.Contains("chrome"))
                    {
                        return new BrowserInfo("Google Chrome", exePath, "chrome://extensions");
                    }
                    if (lower.Contains("msedge") || lower.Contains("edge") || progLower.Contains("edge"))
                    {
                        return new BrowserInfo("Microsoft Edge", exePath, "edge://extensions");
                    }
                    if (lower.Contains("opera") || progLower.Contains("opera"))
                    {
                        return new BrowserInfo("Opera", exePath, "opera://extensions");
                    }
                    if (lower.Contains("vivaldi") || progLower.Contains("vivaldi"))
                    {
                        return new BrowserInfo("Vivaldi", exePath, "vivaldi://extensions");
                    }
                    if (lower.Contains("firefox") || progLower.Contains("firefox"))
                    {
                        return new BrowserInfo("Mozilla Firefox", exePath, "about:debugging#/runtime/this-firefox");
                    }

                    return new BrowserInfo(Path.GetFileNameWithoutExtension(exePath), exePath, "chrome://extensions");
                }
            }
        }
        catch { }

        // Fallback checks
        string[] fallbacks =
        [
            @"C:\Program Files\BraveSoftware\Brave-Browser\Application\brave.exe",
            @"C:\Program Files\Google\Chrome\Application\chrome.exe",
            @"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
        ];

        foreach (var fb in fallbacks)
        {
            if (File.Exists(fb))
            {
                string fn = Path.GetFileNameWithoutExtension(fb).ToLowerInvariant();
                string name = fn.Contains("brave") ? "Brave Browser" : (fn.Contains("chrome") ? "Google Chrome" : "Microsoft Edge");
                string url = fn.Contains("brave") ? "brave://extensions" : (fn.Contains("chrome") ? "chrome://extensions" : "edge://extensions");
                return new BrowserInfo(name, fb, url);
            }
        }

        return new BrowserInfo("Browser Web", "", "chrome://extensions");
    }

    private List<BrowserInfo> GetOtherInstalledBrowsers(string currentBrowserExe)
    {
        var list = new List<BrowserInfo>();
        var candidates = new (string Name, string Path, string Url)[]
        {
            ("Brave Browser", @"C:\Program Files\BraveSoftware\Brave-Browser\Application\brave.exe", "brave://extensions"),
            ("Google Chrome", @"C:\Program Files\Google\Chrome\Application\chrome.exe", "chrome://extensions"),
            ("Google Chrome", @"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe", "chrome://extensions"),
            ("Microsoft Edge", @"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe", "edge://extensions"),
            ("Microsoft Edge", @"C:\Program Files\Microsoft\Edge\Application\msedge.exe", "edge://extensions"),
            ("Opera", Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData), @"Programs\Opera\launcher.exe"), "opera://extensions"),
            ("Mozilla Firefox", @"C:\Program Files\Mozilla Firefox\firefox.exe", "about:debugging#/runtime/this-firefox"),
        };

        var seenNames = new HashSet<string>(StringComparer.OrdinalIgnoreCase);
        foreach (var (name, path, url) in candidates)
        {
            if (File.Exists(path) && !string.Equals(path, currentBrowserExe, StringComparison.OrdinalIgnoreCase) && seenNames.Add(name))
            {
                list.Add(new BrowserInfo(name, path, url));
            }
        }
        return list;
    }

    private async void OnInstallExtensionClick(object sender, RoutedEventArgs e)
    {
        string extDir = GetExtensionDirectory();
        var defaultBrowser = GetDefaultBrowserInfo();
        var otherBrowsers = GetOtherInstalledBrowsers(defaultBrowser.ExePath);

        // 1. Copy folder path to clipboard
        try
        {
            var dataPackage = new DataPackage();
            dataPackage.SetText(extDir);
            Clipboard.SetContent(dataPackage);
        }
        catch { }

        // Helper to launch browser to extensions page
        void OpenBrowserExtensions(BrowserInfo browser)
        {
            try
            {
                if (!string.IsNullOrWhiteSpace(browser.ExePath) && File.Exists(browser.ExePath))
                {
                    Process.Start(new ProcessStartInfo
                    {
                        FileName = browser.ExePath,
                        Arguments = browser.ExtensionsUrl,
                        UseShellExecute = false
                    });
                }
                else
                {
                    Process.Start(new ProcessStartInfo
                    {
                        FileName = "cmd.exe",
                        Arguments = $"/c start {browser.ExtensionsUrl}",
                        CreateNoWindow = true,
                        UseShellExecute = false
                    });
                }
            }
            catch { }
        }

        // 2. Open default browser to extensions page
        OpenBrowserExtensions(defaultBrowser);

        // 3. Open Explorer directly inside the extension folder
        try
        {
            if (Directory.Exists(extDir))
            {
                Process.Start("explorer.exe", $"\"{extDir}\"");
            }
        }
        catch { }

        if (_isDialogOpen) return;
        _isDialogOpen = true;

        try
        {
            var contentStack = new StackPanel { Spacing = 14, Width = 520 };

            var badge = new Border
            {
                Background = new Microsoft.UI.Xaml.Media.SolidColorBrush(Windows.UI.Color.FromArgb(30, 16, 185, 129)),
                BorderBrush = new Microsoft.UI.Xaml.Media.SolidColorBrush(Windows.UI.Color.FromArgb(120, 16, 185, 129)),
                BorderThickness = new Thickness(1),
                CornerRadius = new CornerRadius(6),
                Padding = new Thickness(12, 10, 12, 10)
            };
            var badgeStack = new StackPanel { Orientation = Orientation.Horizontal, Spacing = 8, VerticalAlignment = VerticalAlignment.Center };
            badgeStack.Children.Add(new FontIcon { Glyph = "\uE73E", FontSize = 16, Foreground = new Microsoft.UI.Xaml.Media.SolidColorBrush(Windows.UI.Color.FromArgb(255, 16, 185, 129)) });
            badgeStack.Children.Add(new TextBlock
            {
                Text = $"Scheda '{defaultBrowser.ExtensionsUrl}' aperta in {defaultBrowser.Name} e cartella copiata!",
                FontSize = 12,
                FontWeight = Microsoft.UI.Text.FontWeights.SemiBold,
                Foreground = GetThemePrimaryBrush(),
                VerticalAlignment = VerticalAlignment.Center,
                TextWrapping = TextWrapping.Wrap
            });
            badge.Child = badgeStack;
            contentStack.Children.Add(badge);

            var pathStack = new StackPanel { Spacing = 4 };
            pathStack.Children.Add(new TextBlock { Text = "Percorso cartella estensione (già copiato negli appunti):", FontSize = 11, Foreground = GetThemeSecondaryBrush() });

            var copyPathBox = new TextBox { Text = extDir, IsReadOnly = true, FontSize = 11, Width = 430 };
            var copyBtn = new Button { Content = "Copia", VerticalAlignment = VerticalAlignment.Center };
            copyBtn.Click += (s, a) =>
            {
                var dp = new DataPackage();
                dp.SetText(extDir);
                Clipboard.SetContent(dp);
                ViewModel.StatusMessage = "Percorso cartella estensione copiato negli appunti.";
            };
            var pathRow = new StackPanel { Orientation = Orientation.Horizontal, Spacing = 8, Children = { copyPathBox, copyBtn } };
            pathStack.Children.Add(pathRow);
            contentStack.Children.Add(pathStack);

            var stepsBorder = new Border
            {
                Background = GetThemeCardBackgroundBrush(),
                BorderBrush = GetThemeBorderBrush(),
                BorderThickness = new Thickness(1),
                CornerRadius = new CornerRadius(8),
                Padding = new Thickness(14, 12, 14, 12)
            };

            var stepsStack = new StackPanel { Spacing = 10 };
            stepsStack.Children.Add(new TextBlock { Text = "Come completare l'installazione in 5 secondi:", FontSize = 12, FontWeight = Microsoft.UI.Text.FontWeights.SemiBold, Foreground = GetThemePrimaryBrush() });

            var step1 = new TextBlock
            {
                Text = $"1️⃣  Nella pagina appena aperta in {defaultBrowser.Name}, attiva la \"Modalità sviluppatore\" (levetta in alto a destra).",
                FontSize = 11,
                Foreground = GetThemePrimaryBrush(),
                TextWrapping = TextWrapping.Wrap
            };
            var step2 = new TextBlock
            {
                Text = "2️⃣  Trascina la cartella aperta di Esplora File direttamente dentro la finestra del browser (oppure clicca su \"Carica estensione non pacchettizzata\" e selezionala).",
                FontSize = 11,
                Foreground = GetThemePrimaryBrush(),
                TextWrapping = TextWrapping.Wrap
            };
            var step3 = new TextBlock
            {
                Text = "3️⃣  Fatto! L'icona Getly apparirà nella barra delle estensioni del browser e catturerà automaticamente download e flussi video/audio.",
                FontSize = 11,
                Foreground = GetThemePrimaryBrush(),
                TextWrapping = TextWrapping.Wrap
            };

            stepsStack.Children.Add(step1);
            stepsStack.Children.Add(step2);
            stepsStack.Children.Add(step3);
            stepsBorder.Child = stepsStack;
            contentStack.Children.Add(stepsBorder);

            if (otherBrowsers.Count > 0)
            {
                var otherStack = new StackPanel { Spacing = 6 };
                otherStack.Children.Add(new TextBlock { Text = "Vuoi installarla anche su un altro browser rilevato?", FontSize = 11, Foreground = GetThemeSecondaryBrush() });
                var browserBtnsRow = new StackPanel { Orientation = Orientation.Horizontal, Spacing = 8 };
                foreach (var b in otherBrowsers)
                {
                    var bBtn = new Button { Content = $"Apri su {b.Name}", FontSize = 11, Padding = new Thickness(10, 4, 10, 4) };
                    bBtn.Click += (s, a) => OpenBrowserExtensions(b);
                    browserBtnsRow.Children.Add(bBtn);
                }
                otherStack.Children.Add(browserBtnsRow);
                contentStack.Children.Add(otherStack);
            }

            var dialog = new ContentDialog
            {
                Title = $"Installazione Estensione ({defaultBrowser.Name})",
                PrimaryButtonText = "Riapri Pagina Estensioni",
                SecondaryButtonText = "Riapri Cartella",
                CloseButtonText = "Ho Fatto!",
                DefaultButton = ContentDialogButton.Close,
                Content = contentStack
            };
            PrepareDialog(dialog);

            var res = await dialog.ShowAsync();
            if (res == ContentDialogResult.Primary)
            {
                OpenBrowserExtensions(defaultBrowser);
            }
            else if (res == ContentDialogResult.Secondary)
            {
                if (Directory.Exists(extDir))
                {
                    Process.Start("explorer.exe", $"\"{extDir}\"");
                }
            }
        }
        finally
        {
            _isDialogOpen = false;
        }
    }

    private void OnOpenExtensionFolderClick(object sender, RoutedEventArgs e)
    {
        string extDir = GetExtensionDirectory();
        if (Directory.Exists(extDir))
        {
            Process.Start("explorer.exe", $"\"{extDir}\"");
        }
        else
        {
            ViewModel.StatusMessage = $"Cartella estensione non trovata in: {extDir}";
        }
    }

    #endregion

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

    private void OnDownloadNowClick(object sender, RoutedEventArgs e)
    {
        var task = GetTask(sender);
        if (task != null)
        {
            ViewModel.DownloadNowCommand.Execute(task);
        }
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

        if (task.IsCompleted)
        {
            if (ViewModel.OpenFileCommand.CanExecute(task))
            {
                ViewModel.OpenFileCommand.Execute(task);
            }
            return;
        }

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

            if ((result == ContentDialogResult.Primary || result == ContentDialogResult.Secondary) && selectedItems.Count > 0)
            {
                bool startPaused = result == ContentDialogResult.Secondary;
                var torrentsInBatch = selectedItems.Where(i => i.Url.StartsWith("magnet:", StringComparison.OrdinalIgnoreCase) || i.Url.EndsWith(".torrent", StringComparison.OrdinalIgnoreCase)).ToList();

                if (torrentsInBatch.Count > 0 && ViewModel.WarnOnTorrentWithoutVpn && !ViewModel.HasActiveVpn)
                {
                    bool proceed = await ShowTorrentWithoutVpnWarningDialogAsync(torrentsInBatch[0].Url);
                    if (!proceed)
                    {
                        selectedItems.RemoveAll(i => i.Url.StartsWith("magnet:", StringComparison.OrdinalIgnoreCase) || i.Url.EndsWith(".torrent", StringComparison.OrdinalIgnoreCase));
                        if (selectedItems.Count == 0)
                        {
                            ViewModel.StatusMessage = "Download batch annullato per tutela privacy (nessuna VPN attiva).";
                            return;
                        }
                    }
                }

                bool originalWarnSetting = ViewModel.WarnOnTorrentWithoutVpn;
                try
                {
                    // Suppress repeated warning prompts within this single batch execution
                    ViewModel.WarnOnTorrentWithoutVpn = false;
                    foreach (var item in selectedItems)
                    {
                        await ViewModel.AddDownloadWithParamsAsync(item.Url, item.Category, startPaused: startPaused, filename: item.Filename);
                    }
                }
                finally
                {
                    ViewModel.WarnOnTorrentWithoutVpn = originalWarnSetting;
                }

                ViewModel.StatusMessage = startPaused
                    ? $"Aggiunti in coda {selectedItems.Count} download batch."
                    : $"Avviati {selectedItems.Count} download batch.";
            }
        }
        finally
        {
            _isDialogOpen = false;
        }
    }

    private async void OnSiteGrabberClick(object sender, RoutedEventArgs e)
    {
        if (_isDialogOpen) return;
        _isDialogOpen = true;

        try
        {
            var grabbedItems = new ObservableCollection<GrabbedResourceItem>();

            var urlTextBox = new TextBox
            {
                PlaceholderText = "Incolla l'URL del sito web o forum (es. https://fitgirl-repacks.site/...)",
                Width = 540
            };

            var depthCombo = new ComboBox
            {
                Width = 320,
                SelectedIndex = 0
            };
            depthCombo.Items.Add(new ComboBoxItem { Content = "Livello 1: Solo questa pagina (Veloce)", Tag = 1 });
            depthCombo.Items.Add(new ComboBoxItem { Content = "Livello 2: Segui link interni (1 salto - Consigliato per paste/hoster)", Tag = 2 });
            depthCombo.Items.Add(new ComboBoxItem { Content = "Livello 3: Esplorazione profonda (2 salti, fino a 40 pagine)", Tag = 3 });

            var scanButton = new Button
            {
                Content = "Avvia Scansione",
                Style = Application.Current.Resources["AccentButtonStyle"] as Style,
                HorizontalAlignment = HorizontalAlignment.Left
            };

            var progressBar = new ProgressBar
            {
                IsIndeterminate = true,
                Visibility = Visibility.Collapsed,
                Width = 540,
                Margin = new Thickness(0, 4, 0, 4)
            };

            var statusTextBlock = new TextBlock
            {
                Text = "Inserisci un URL e seleziona la profondità per catturare i file scaricabili.",
                FontSize = 11,
                Foreground = GetThemeSecondaryBrush(),
                VerticalAlignment = VerticalAlignment.Center,
                TextWrapping = TextWrapping.Wrap
            };

            var countTextBlock = new TextBlock
            {
                Text = "0 risorse trovate",
                FontSize = 11,
                FontWeight = Microsoft.UI.Text.FontWeights.SemiBold,
                Foreground = GetThemePrimaryBrush(),
                VerticalAlignment = VerticalAlignment.Center,
                Margin = new Thickness(0, 4, 0, 0)
            };

            void UpdateCounts()
            {
                int selectedCount = grabbedItems.Count(i => i.IsSelected);
                countTextBlock.Text = $"Trovate {grabbedItems.Count} risorse ({selectedCount} selezionate)";
            }

            // Quick filter buttons
            var btnAll = new Button { Content = "Tutti", FontSize = 11, Padding = new Thickness(8, 2, 8, 2) };
            var btnArchives = new Button { Content = "📦 Solo Archivi", FontSize = 11, Padding = new Thickness(8, 2, 8, 2) };
            var btnDocs = new Button { Content = "📄 Documenti", FontSize = 11, Padding = new Thickness(8, 2, 8, 2) };
            var btnMedia = new Button { Content = "🎬 Media", FontSize = 11, Padding = new Thickness(8, 2, 8, 2) };
            var btnSoftware = new Button { Content = "💻 Software", FontSize = 11, Padding = new Thickness(8, 2, 8, 2) };
            var btnInvert = new Button { Content = "Inverti", FontSize = 11, Padding = new Thickness(8, 2, 8, 2) };
            var btnNone = new Button { Content = "Nessuno", FontSize = 11, Padding = new Thickness(8, 2, 8, 2) };

            void ApplyFilter(Func<GrabbedResourceItem, bool> predicate)
            {
                foreach (var item in grabbedItems)
                {
                    item.IsSelected = predicate(item);
                }
                UpdateCounts();
            }

            btnAll.Click += (s, a) => ApplyFilter(_ => true);
            btnNone.Click += (s, a) => ApplyFilter(_ => false);
            btnInvert.Click += (s, a) => ApplyFilter(it => !it.IsSelected);
            btnArchives.Click += (s, a) => ApplyFilter(it =>
                it.Category == "Compressi" ||
                it.Filename.EndsWith(".rar", StringComparison.OrdinalIgnoreCase) ||
                it.Filename.EndsWith(".zip", StringComparison.OrdinalIgnoreCase) ||
                it.Filename.EndsWith(".7z", StringComparison.OrdinalIgnoreCase) ||
                it.Filename.EndsWith(".bin", StringComparison.OrdinalIgnoreCase) ||
                it.Filename.EndsWith(".iso", StringComparison.OrdinalIgnoreCase));
            btnDocs.Click += (s, a) => ApplyFilter(it => it.Category == "Documenti");
            btnMedia.Click += (s, a) => ApplyFilter(it => it.Category == "Video" || it.Category == "Audio" || it.Category == "Immagini");
            btnSoftware.Click += (s, a) => ApplyFilter(it => it.Category == "Software");

            var filterPanel = new StackPanel
            {
                Orientation = Orientation.Horizontal,
                Spacing = 6,
                Visibility = Visibility.Collapsed
            };
            filterPanel.Children.Add(btnAll);
            filterPanel.Children.Add(btnArchives);
            filterPanel.Children.Add(btnDocs);
            filterPanel.Children.Add(btnMedia);
            filterPanel.Children.Add(btnSoftware);
            filterPanel.Children.Add(btnInvert);
            filterPanel.Children.Add(btnNone);

            var itemsListView = new ListView
            {
                ItemsSource = grabbedItems,
                SelectionMode = ListViewSelectionMode.None,
                Height = 190,
                Width = 540,
                BorderThickness = new Thickness(1),
                BorderBrush = GetThemeBorderBrush(),
                CornerRadius = new CornerRadius(6),
                Padding = new Thickness(4),
                Visibility = Visibility.Collapsed
            };

            if (RootGrid.Resources.TryGetValue("GrabbedResourceItemTemplate", out var templateObj) && templateObj is DataTemplate dt)
            {
                itemsListView.ItemTemplate = dt;
            }

            var destPathTextBox = new TextBox
            {
                Text = ViewModel.DefaultDownloadDir,
                Width = 430
            };

            var browseDestButton = new Button
            {
                Content = "Sfoglia...",
                VerticalAlignment = VerticalAlignment.Center
            };

            browseDestButton.Click += async (s, a) =>
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
                        destPathTextBox.Text = folder.Path;
                    }
                }
                catch { }
            };

            var destRow = new StackPanel
            {
                Orientation = Orientation.Horizontal,
                Spacing = 8,
                Children = { destPathTextBox, browseDestButton }
            };

            var organizeByCatBox = new CheckBox
            {
                Content = "Organizza automaticamente in sottocartelle (Compressi, Documenti, Video, ecc.)",
                IsChecked = true,
                FontSize = 11
            };

            var destPanel = new StackPanel
            {
                Spacing = 6,
                Visibility = Visibility.Collapsed,
                Children =
                {
                    new TextBlock { Text = "Cartella di salvataggio:", FontSize = 11, Foreground = GetThemeSecondaryBrush() },
                    destRow,
                    organizeByCatBox
                }
            };

            scanButton.Click += async (s, a) =>
            {
                string rawUrl = urlTextBox.Text?.Trim() ?? string.Empty;
                if (string.IsNullOrWhiteSpace(rawUrl) || (!rawUrl.StartsWith("http://", StringComparison.OrdinalIgnoreCase) && !rawUrl.StartsWith("https://", StringComparison.OrdinalIgnoreCase)))
                {
                    statusTextBlock.Text = "Inserisci un URL valido (deve iniziare con http:// o https://).";
                    return;
                }

                scanButton.IsEnabled = false;
                progressBar.Visibility = Visibility.Visible;
                statusTextBlock.Text = "Scansione del sito in corso... Seguo i link e catturo le risorse...";

                int depth = 1;
                if (depthCombo.SelectedItem is ComboBoxItem cbi && cbi.Tag is int d)
                {
                    depth = d;
                }
                int maxPages = depth == 1 ? 1 : (depth == 2 ? 20 : 40);

                try
                {
                    var grabResult = await ViewModel.IpcClient.GrabSiteAsync(rawUrl, depth: depth, maxPages: maxPages, timeoutMs: 90000);
                    grabbedItems.Clear();

                    foreach (var r in grabResult.Items)
                    {
                        grabbedItems.Add(new GrabbedResourceItem
                        {
                            Url = r.Url,
                            Filename = r.Filename,
                            Category = r.Category,
                            SourceTag = r.SourceTag,
                            IsSelected = true
                        });
                    }

                    if (grabbedItems.Count > 0)
                    {
                        statusTextBlock.Text = $"Scansione completata: trovati {grabbedItems.Count} file scaricabili.";
                        itemsListView.Visibility = Visibility.Visible;
                        filterPanel.Visibility = Visibility.Visible;
                        destPanel.Visibility = Visibility.Visible;
                        countTextBlock.Visibility = Visibility.Visible;
                        UpdateCounts();
                    }
                    else
                    {
                        statusTextBlock.Text = "Nessun file scaricabile trovato nella pagina o nei link esplorati.";
                        itemsListView.Visibility = Visibility.Collapsed;
                        filterPanel.Visibility = Visibility.Collapsed;
                        destPanel.Visibility = Visibility.Collapsed;
                    }
                }
                catch (Exception ex)
                {
                    statusTextBlock.Text = $"Errore durante la scansione: {ex.Message}";
                }
                finally
                {
                    scanButton.IsEnabled = true;
                    progressBar.Visibility = Visibility.Collapsed;
                }
            };

            var scanOptionsRow = new StackPanel
            {
                Orientation = Orientation.Horizontal,
                Spacing = 10,
                Children =
                {
                    new StackPanel
                    {
                        Spacing = 4,
                        Children =
                        {
                            new TextBlock { Text = "Profondità di scansione:", FontSize = 11, Foreground = GetThemeSecondaryBrush() },
                            depthCombo
                        }
                    },
                    new StackPanel
                    {
                        VerticalAlignment = VerticalAlignment.Bottom,
                        Children = { scanButton }
                    }
                }
            };

            var dialogContent = new StackPanel
            {
                Spacing = 10,
                Width = 540,
                Children =
                {
                    new TextBlock
                    {
                        Text = "Scansiona un sito web per estrarre tutti i file scaricabili e pacchetti divisi (es. FitGirl, forum, siti paste):",
                        FontSize = 12,
                        Foreground = GetThemeSecondaryBrush()
                    },
                    urlTextBox,
                    scanOptionsRow,
                    progressBar,
                    statusTextBlock,
                    filterPanel,
                    countTextBlock,
                    itemsListView,
                    destPanel
                }
            };

            var dialog = new ContentDialog
            {
                Title = "Site Grabber",
                PrimaryButtonText = "Scarica Selezionati",
                SecondaryButtonText = "Accoda in Pausa",
                CloseButtonText = "Chiudi",
                DefaultButton = ContentDialogButton.Primary,
                Content = dialogContent
            };

            PrepareDialog(dialog);

            var dialogResult = await dialog.ShowAsync();
            var selectedItems = grabbedItems.Where(i => i.IsSelected).ToList();

            if ((dialogResult == ContentDialogResult.Primary || dialogResult == ContentDialogResult.Secondary) && selectedItems.Count > 0)
            {
                bool startPaused = dialogResult == ContentDialogResult.Secondary;
                string baseDir = string.IsNullOrWhiteSpace(destPathTextBox.Text) ? ViewModel.DefaultDownloadDir : destPathTextBox.Text;
                bool organize = organizeByCatBox.IsChecked == true;

                foreach (var item in selectedItems)
                {
                    string finalDest = baseDir;
                    if (organize && !string.IsNullOrWhiteSpace(item.Category))
                    {
                        finalDest = System.IO.Path.Combine(baseDir, item.Category);
                    }
                    await ViewModel.AddDownloadWithParamsAsync(item.Url, item.Category, startPaused: startPaused, filename: item.Filename, destDir: finalDest);
                }

                ViewModel.StatusMessage = startPaused
                    ? $"Accodati in pausa {selectedItems.Count} download da Site Grabber."
                    : $"Avviati {selectedItems.Count} download da Site Grabber.";
            }
        }
        finally
        {
            _isDialogOpen = false;
        }
    }

    private async Task<bool> ShowTorrentWithoutVpnWarningDialogAsync(string url)
    {
        if (_isDialogOpen) return false;
        _isDialogOpen = true;

        try
        {
            bool isDark = (this.Content as FrameworkElement)?.ActualTheme == ElementTheme.Dark;

            var doNotAskAgainCheckBox = new CheckBox
            {
                Content = "Non mostrare più questo avviso di sicurezza",
                IsChecked = false,
                FontSize = 12,
                Margin = new Thickness(0, 10, 0, 0)
            };

            var warningCard = new Border
            {
                Background = new Microsoft.UI.Xaml.Media.SolidColorBrush(Windows.UI.Color.FromArgb(28, 245, 158, 11)),
                BorderBrush = new Microsoft.UI.Xaml.Media.SolidColorBrush(Windows.UI.Color.FromArgb(120, 245, 158, 11)),
                BorderThickness = new Thickness(1),
                CornerRadius = new CornerRadius(8),
                Padding = new Thickness(14, 12, 14, 12)
            };

            var cardStack = new StackPanel { Spacing = 8 };

            var alertHeader = new StackPanel { Orientation = Orientation.Horizontal, Spacing = 8, VerticalAlignment = VerticalAlignment.Center };
            alertHeader.Children.Add(new FontIcon
            {
                Glyph = "\uE7BA", // Warning shield icon
                FontSize = 20,
                Foreground = new Microsoft.UI.Xaml.Media.SolidColorBrush(Windows.UI.Color.FromArgb(255, 245, 158, 11))
            });
            alertHeader.Children.Add(new TextBlock
            {
                Text = "Il tuo indirizzo IP pubblico sarà visibile",
                FontSize = 13,
                FontWeight = Microsoft.UI.Text.FontWeights.SemiBold,
                Foreground = isDark
                    ? new Microsoft.UI.Xaml.Media.SolidColorBrush(Windows.UI.Color.FromArgb(255, 252, 211, 77))
                    : new Microsoft.UI.Xaml.Media.SolidColorBrush(Windows.UI.Color.FromArgb(255, 180, 83, 9))
            });
            cardStack.Children.Add(alertHeader);

            var descBlock = new TextBlock
            {
                Text = "Stai per avviare il download di un link Magnet o Torrent, ma sul tuo computer non risulta connessa alcuna VPN protetta.\r\n\r\nI protocolli P2P / BitTorrent condividono pubblicamente il tuo indirizzo IP a tutti i nodi e tracker della rete swarm. Senza una VPN attiva, il tuo provider e chiunque monitori questo torrent potranno vedere il tuo indirizzo IP reale.\r\n\r\nVuoi procedere comunque con il download prima di connettere una VPN?",
                FontSize = 12,
                TextWrapping = TextWrapping.Wrap,
                Foreground = GetThemeSecondaryBrush(),
                LineHeight = 18
            };
            cardStack.Children.Add(descBlock);
            warningCard.Child = cardStack;

            var contentPanel = new StackPanel
            {
                Spacing = 10,
                Width = 460,
                Children =
                {
                    warningCard,
                    doNotAskAgainCheckBox
                }
            };

            var dialog = new ContentDialog
            {
                Title = "Avviso Sicurezza Torrent (Nessuna VPN Rilevata)",
                PrimaryButtonText = "Procedi senza VPN",
                CloseButtonText = "Annulla (Consigliato)",
                DefaultButton = ContentDialogButton.Close,
                Content = contentPanel
            };
            PrepareDialog(dialog);

            var result = await dialog.ShowAsync();
            if (result == ContentDialogResult.Primary)
            {
                if (doNotAskAgainCheckBox.IsChecked == true)
                {
                    ViewModel.WarnOnTorrentWithoutVpn = false;
                    _ = ViewModel.SaveSettingsAsync();
                }
                return true;
            }

            return false;
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

        DispatcherTimer? liveTimer = null;

        try
        {
            bool isDark = (this.Content as FrameworkElement)?.ActualTheme == ElementTheme.Dark;
            bool isTorrent = task.IsTorrent
                || (task.Url != null && task.Url.StartsWith("magnet:", StringComparison.OrdinalIgnoreCase))
                || (task.Filename != null && task.Filename.EndsWith(".torrent", StringComparison.OrdinalIgnoreCase));

            var contentPanel = new StackPanel { Spacing = 12, Width = isTorrent ? 540 : 480 };

            // 1. Header Card (Icon + Title + Badges)
            var headerCard = new Border
            {
                Background = GetThemeCardBackgroundBrush(),
                BorderBrush = GetThemeBorderBrush(),
                BorderThickness = new Thickness(1),
                CornerRadius = new CornerRadius(8),
                Padding = new Thickness(14, 12, 14, 12)
            };

            var headerLayout = new Grid
            {
                ColumnDefinitions =
                {
                    new ColumnDefinition { Width = GridLength.Auto },
                    new ColumnDefinition { Width = new GridLength(1, GridUnitType.Star) }
                }
            };

            var iconFont = new FontIcon
            {
                Glyph = isTorrent ? "\uE753" : task.StatusIconGlyph,
                FontSize = 18,
                Foreground = isTorrent
                    ? new Microsoft.UI.Xaml.Media.SolidColorBrush(Windows.UI.Color.FromArgb(255, 168, 85, 247))
                    : new Microsoft.UI.Xaml.Media.SolidColorBrush(Windows.UI.Color.FromArgb(255, 59, 130, 246)),
                HorizontalAlignment = HorizontalAlignment.Center,
                VerticalAlignment = VerticalAlignment.Center
            };

            var iconBorder = new Border
            {
                Width = 42,
                Height = 42,
                CornerRadius = new CornerRadius(8),
                Background = isTorrent
                    ? new Microsoft.UI.Xaml.Media.SolidColorBrush(Windows.UI.Color.FromArgb(35, 139, 92, 246))
                    : new Microsoft.UI.Xaml.Media.SolidColorBrush(Windows.UI.Color.FromArgb(35, 59, 130, 246)),
                Margin = new Thickness(0, 0, 12, 0),
                Child = iconFont
            };
            Grid.SetColumn(iconBorder, 0);
            headerLayout.Children.Add(iconBorder);

            var titleBadgeStack = new StackPanel { Spacing = 6, VerticalAlignment = VerticalAlignment.Center };

            var titleBlock = new TextBlock
            {
                Text = task.Filename,
                FontSize = 14,
                FontWeight = Microsoft.UI.Text.FontWeights.SemiBold,
                Foreground = GetThemePrimaryBrush(),
                TextWrapping = TextWrapping.Wrap,
                MaxLines = 2,
                TextTrimming = TextTrimming.CharacterEllipsis
            };
            ToolTipService.SetToolTip(titleBlock, task.Filename);
            titleBadgeStack.Children.Add(titleBlock);

            var badgeRow = new StackPanel { Orientation = Orientation.Horizontal, Spacing = 8 };

            // Category Pill
            var catTextBlock = new TextBlock
            {
                Text = task.Category,
                FontSize = 11,
                FontWeight = Microsoft.UI.Text.FontWeights.Medium,
                Foreground = new Microsoft.UI.Xaml.Media.SolidColorBrush(Windows.UI.Color.FromArgb(255, 168, 85, 247))
            };
            var catBorder = new Border
            {
                Background = new Microsoft.UI.Xaml.Media.SolidColorBrush(Windows.UI.Color.FromArgb(30, 139, 92, 246)),
                CornerRadius = new CornerRadius(4),
                Padding = new Thickness(7, 2, 7, 2),
                Child = catTextBlock
            };
            badgeRow.Children.Add(catBorder);

            // Status Pill
            var statusBrush = TryParseColorBrush(task.StatusColor, Windows.UI.Color.FromArgb(255, 59, 130, 246));
            var statusTextBlock = new TextBlock
            {
                Text = task.StatusDisplay,
                FontSize = 11,
                FontWeight = Microsoft.UI.Text.FontWeights.SemiBold,
                Foreground = statusBrush
            };
            var statusBorder = new Border
            {
                Background = new Microsoft.UI.Xaml.Media.SolidColorBrush(Windows.UI.Color.FromArgb(30, statusBrush.Color.R, statusBrush.Color.G, statusBrush.Color.B)),
                CornerRadius = new CornerRadius(4),
                Padding = new Thickness(7, 2, 7, 2),
                Child = statusTextBlock
            };
            badgeRow.Children.Add(statusBorder);

            // Percentage badge
            var progTextBlock = new TextBlock
            {
                Text = task.ProgressPercentageFormatted,
                FontSize = 11,
                FontWeight = Microsoft.UI.Text.FontWeights.Medium,
                Foreground = GetThemeSecondaryBrush()
            };
            var progBorder = new Border
            {
                Background = new Microsoft.UI.Xaml.Media.SolidColorBrush(Windows.UI.Color.FromArgb(25, 255, 255, 255)),
                CornerRadius = new CornerRadius(4),
                Padding = new Thickness(7, 2, 7, 2),
                Child = progTextBlock
            };
            badgeRow.Children.Add(progBorder);

            titleBadgeStack.Children.Add(badgeRow);
            Grid.SetColumn(titleBadgeStack, 1);
            headerLayout.Children.Add(titleBadgeStack);
            headerCard.Child = headerLayout;
            contentPanel.Children.Add(headerCard);

            // 2. Error / Warning Alert Card (dynamic visibility)
            bool isInitialError = task.IsError || string.Equals(task.Status, "Error", StringComparison.OrdinalIgnoreCase) || !string.IsNullOrWhiteSpace(task.ErrorMessage);
            var errCard = new Border
            {
                Background = new Microsoft.UI.Xaml.Media.SolidColorBrush(Windows.UI.Color.FromArgb(25, 239, 68, 68)),
                BorderBrush = new Microsoft.UI.Xaml.Media.SolidColorBrush(Windows.UI.Color.FromArgb(120, 239, 68, 68)),
                BorderThickness = new Thickness(1),
                CornerRadius = new CornerRadius(8),
                Padding = new Thickness(12, 10, 12, 10),
                Visibility = isInitialError ? Visibility.Visible : Visibility.Collapsed
            };

            var errStack = new StackPanel { Spacing = 6 };
            var errHeader = new StackPanel { Orientation = Orientation.Horizontal, Spacing = 6 };
            errHeader.Children.Add(new FontIcon { Glyph = "\uEB90", FontSize = 13, Foreground = new Microsoft.UI.Xaml.Media.SolidColorBrush(Windows.UI.Color.FromArgb(255, 239, 68, 68)) });
            errHeader.Children.Add(new TextBlock
            {
                Text = "Stato / Dettagli Errore",
                FontSize = 12,
                FontWeight = Microsoft.UI.Text.FontWeights.SemiBold,
                Foreground = new Microsoft.UI.Xaml.Media.SolidColorBrush(Windows.UI.Color.FromArgb(255, 239, 68, 68))
            });
            errStack.Children.Add(errHeader);

            var errText = new TextBlock
            {
                Text = !string.IsNullOrWhiteSpace(task.ErrorMessage) ? task.ErrorMessage : "Errore non specificato durante il download",
                FontSize = 11,
                TextWrapping = TextWrapping.Wrap,
                Foreground = isDark
                    ? new Microsoft.UI.Xaml.Media.SolidColorBrush(Windows.UI.Color.FromArgb(255, 252, 165, 165))
                    : new Microsoft.UI.Xaml.Media.SolidColorBrush(Windows.UI.Color.FromArgb(255, 185, 28, 28))
            };
            errStack.Children.Add(errText);

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
                    Margin = new Thickness(0, 4, 0, 0),
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
                errStack.Children.Add(solveBtn);
            }

            errCard.Child = errStack;
            contentPanel.Children.Add(errCard);

            // 3. Compact URL / Magnet Card with Copy button
            var urlCard = new Border
            {
                Background = GetThemeCardBackgroundBrush(),
                BorderBrush = GetThemeBorderBrush(),
                BorderThickness = new Thickness(1),
                CornerRadius = new CornerRadius(8),
                Padding = new Thickness(12, 10, 12, 10)
            };

            var urlStack = new StackPanel { Spacing = 6 };
            urlStack.Children.Add(new TextBlock
            {
                Text = isTorrent ? "Link Magnet / URL Sorgente" : "URL Sorgente",
                FontSize = 11,
                FontWeight = Microsoft.UI.Text.FontWeights.SemiBold,
                Foreground = GetThemeSecondaryBrush()
            });

            var urlRow = new Grid
            {
                ColumnDefinitions =
                {
                    new ColumnDefinition { Width = new GridLength(1, GridUnitType.Star) },
                    new ColumnDefinition { Width = GridLength.Auto }
                }
            };

            var urlTextBox = new TextBox
            {
                Text = task.Url ?? string.Empty,
                IsReadOnly = true,
                FontSize = 11,
                TextWrapping = TextWrapping.NoWrap,
                Height = 32,
                CornerRadius = new CornerRadius(4),
                Margin = new Thickness(0, 0, 8, 0),
                VerticalAlignment = VerticalAlignment.Center
            };
            ToolTipService.SetToolTip(urlTextBox, task.Url);
            Grid.SetColumn(urlTextBox, 0);
            urlRow.Children.Add(urlTextBox);

            var copyBtn = new Button
            {
                Height = 32,
                Padding = new Thickness(10, 0, 10, 0),
                CornerRadius = new CornerRadius(4),
                VerticalAlignment = VerticalAlignment.Center
            };
            var copyStack = new StackPanel { Orientation = Orientation.Horizontal, Spacing = 6 };
            var copyIcon = new FontIcon { Glyph = "\uE8C8", FontSize = 12 };
            var copyText = new TextBlock { Text = "Copia", FontSize = 11 };
            copyStack.Children.Add(copyIcon);
            copyStack.Children.Add(copyText);
            copyBtn.Content = copyStack;
            copyBtn.Click += (s, e) =>
            {
                try
                {
                    var dp = new Windows.ApplicationModel.DataTransfer.DataPackage();
                    dp.SetText(task.Url ?? string.Empty);
                    Windows.ApplicationModel.DataTransfer.Clipboard.SetContent(dp);
                    copyText.Text = "Copiato!";
                    copyIcon.Glyph = "\uE73E";
                }
                catch { }
            };
            Grid.SetColumn(copyBtn, 1);
            urlRow.Children.Add(copyBtn);

            urlStack.Children.Add(urlRow);
            urlCard.Child = urlStack;
            contentPanel.Children.Add(urlCard);

            // 4. Key Metrics Grid Card (2 columns, 2 rows)
            var metricsCard = new Border
            {
                Background = GetThemeCardBackgroundBrush(),
                BorderBrush = GetThemeBorderBrush(),
                BorderThickness = new Thickness(1),
                CornerRadius = new CornerRadius(8),
                Padding = new Thickness(14, 12, 14, 12)
            };

            var metricsGrid = new Grid
            {
                RowSpacing = 10,
                ColumnSpacing = 20,
                ColumnDefinitions =
                {
                    new ColumnDefinition { Width = new GridLength(1, GridUnitType.Star) },
                    new ColumnDefinition { Width = new GridLength(1, GridUnitType.Star) }
                },
                RowDefinitions =
                {
                    new RowDefinition { Height = GridLength.Auto },
                    new RowDefinition { Height = GridLength.Auto }
                }
            };

            (TextBlock ValBlock, TextBlock? SubvalBlock) AddMetric(string label, string val, int col, int row, string? subval = null)
            {
                var sp = new StackPanel { Spacing = 2 };
                sp.Children.Add(new TextBlock
                {
                    Text = label,
                    FontSize = 11,
                    FontWeight = Microsoft.UI.Text.FontWeights.SemiBold,
                    Foreground = GetThemeSecondaryBrush()
                });
                var valBlock = new TextBlock
                {
                    Text = val,
                    FontSize = 13,
                    FontWeight = Microsoft.UI.Text.FontWeights.SemiBold,
                    Foreground = GetThemePrimaryBrush()
                };
                sp.Children.Add(valBlock);
                TextBlock? subvalBlock = null;
                if (!string.IsNullOrEmpty(subval))
                {
                    subvalBlock = new TextBlock
                    {
                        Text = subval,
                        FontSize = 10,
                        Foreground = GetThemeSecondaryBrush()
                    };
                    sp.Children.Add(subvalBlock);
                }
                Grid.SetColumn(sp, col);
                Grid.SetRow(sp, row);
                metricsGrid.Children.Add(sp);
                return (valBlock, subvalBlock);
            }

            // Col 0, Row 0: Dimensioni
            var (dimValBlock, _) = AddMetric("Dimensioni", task.DownloadedFormatted, 0, 0);

            // Col 1, Row 0: Velocità
            string speedText = task.IsTorrent && task.UploadSpeedBps > 0
                ? $"⬇ {task.SpeedFormatted}  ⬆ {TaskItemViewModel.FormatSpeed(task.UploadSpeedBps)}"
                : task.SpeedFormatted;
            var (speedValBlock, _) = AddMetric("Velocità Corrente", speedText, 1, 0);

            // Col 0, Row 1: Tempo Stimato (ETA)
            var (etaValBlock, _) = AddMetric("Tempo Rimanente (ETA)", task.EtaFormatted, 0, 1);

            // Col 1, Row 1: Parti / Peer (if Torrent) or Categoria
            TextBlock? valBlockParts = null;
            TextBlock? subvalBlockPeers = null;
            if (isTorrent)
            {
                string partsInfo = task.PiecesTotal > 0
                    ? $"{task.PiecesCompleted} / {task.PiecesTotal} parti"
                    : "In risoluzione...";
                string peerInfo = $"({task.Seeds} Seed, {task.Peers} Peer connessi)";
                var res = AddMetric("Parti & Peer Torrent", partsInfo, 1, 1, peerInfo);
                valBlockParts = res.ValBlock;
                subvalBlockPeers = res.SubvalBlock;
            }
            else
            {
                AddMetric("Tipo Connessione", "Download Multi-Segmento HTTP", 1, 1);
            }

            metricsCard.Child = metricsGrid;
            contentPanel.Children.Add(metricsCard);

            // 5. Destination Directory Card with "Apri Cartella" button
            var destCard = new Border
            {
                Background = GetThemeCardBackgroundBrush(),
                BorderBrush = GetThemeBorderBrush(),
                BorderThickness = new Thickness(1),
                CornerRadius = new CornerRadius(8),
                Padding = new Thickness(12, 10, 12, 10)
            };

            var destStack = new StackPanel { Spacing = 6 };
            destStack.Children.Add(new TextBlock
            {
                Text = "Cartella di Salvataggio",
                FontSize = 11,
                FontWeight = Microsoft.UI.Text.FontWeights.SemiBold,
                Foreground = GetThemeSecondaryBrush()
            });

            var destRow = new Grid
            {
                ColumnDefinitions =
                {
                    new ColumnDefinition { Width = new GridLength(1, GridUnitType.Star) },
                    new ColumnDefinition { Width = GridLength.Auto }
                }
            };

            var destText = new TextBlock
            {
                Text = task.DestinationDir,
                FontSize = 11,
                TextTrimming = TextTrimming.CharacterEllipsis,
                Foreground = GetThemeSecondaryBrush(),
                VerticalAlignment = VerticalAlignment.Center
            };
            ToolTipService.SetToolTip(destText, task.DestinationDir);
            Grid.SetColumn(destText, 0);
            destRow.Children.Add(destText);

            var openFolderBtn = new Button
            {
                Height = 32,
                Padding = new Thickness(10, 0, 10, 0),
                CornerRadius = new CornerRadius(4),
                VerticalAlignment = VerticalAlignment.Center
            };
            var openFolderStack = new StackPanel { Orientation = Orientation.Horizontal, Spacing = 6 };
            openFolderStack.Children.Add(new FontIcon { Glyph = "\uED25", FontSize = 12 });
            openFolderStack.Children.Add(new TextBlock { Text = "Apri Cartella", FontSize = 11 });
            openFolderBtn.Content = openFolderStack;
            openFolderBtn.Click += (s, e) =>
            {
                try
                {
                    if (System.IO.Directory.Exists(task.DestinationDir))
                    {
                        System.Diagnostics.Process.Start(new System.Diagnostics.ProcessStartInfo
                        {
                            FileName = "explorer.exe",
                            Arguments = $"\"{task.DestinationDir}\"",
                            UseShellExecute = true
                        });
                    }
                }
                catch { }
            };
            Grid.SetColumn(openFolderBtn, 1);
            destRow.Children.Add(openFolderBtn);

            destStack.Children.Add(destRow);
            destCard.Child = destStack;
            contentPanel.Children.Add(destCard);

            // 6. Torrent / P2P Files & Parts Table
            var fileRowBindings = new System.Collections.Generic.Dictionary<int, (TextBlock dlBlock, ProgressBar pBar, TextBlock pText)>();
            if (isTorrent)
            {
                var files = task.Files;
                if (files == null || files.Count == 0)
                {
                    files = await ViewModel.GetTorrentFilesAsync(task.TaskId);
                }

                if (files != null && files.Count > 0)
                {
                    var filesSection = new StackPanel { Spacing = 6, Margin = new Thickness(0, 4, 0, 0) };
                    filesSection.Children.Add(new TextBlock
                    {
                        Text = $"File & Parti del Torrent ({files.Count} elementi):",
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
                            new ColumnDefinition { Width = new GridLength(85, GridUnitType.Pixel) },
                            new ColumnDefinition { Width = new GridLength(85, GridUnitType.Pixel) },
                            new ColumnDefinition { Width = new GridLength(100, GridUnitType.Pixel) }
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
                            Background = GetThemeCardBackgroundBrush(),
                            CornerRadius = new CornerRadius(4),
                            ColumnDefinitions =
                            {
                                new ColumnDefinition { Width = new GridLength(1, GridUnitType.Star) },
                                new ColumnDefinition { Width = new GridLength(85, GridUnitType.Pixel) },
                                new ColumnDefinition { Width = new GridLength(85, GridUnitType.Pixel) },
                                new ColumnDefinition { Width = new GridLength(100, GridUnitType.Pixel) }
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

                        fileRowBindings[f.Index] = (dlBlock, pBar, pText);
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
                            MaxHeight = 180,
                            Content = filesStack
                        }
                    };

                    filesSection.Children.Add(headerGrid);
                    filesSection.Children.Add(filesContainer);
                    contentPanel.Children.Add(filesSection);
                }
                else
                {
                    // Metadata resolving indicator
                    var resolvingCard = new Border
                    {
                        Background = GetThemeCardBackgroundBrush(),
                        BorderBrush = GetThemeBorderBrush(),
                        BorderThickness = new Thickness(1),
                        CornerRadius = new CornerRadius(6),
                        Padding = new Thickness(12, 10, 12, 10)
                    };
                    var resolvingStack = new StackPanel { Orientation = Orientation.Horizontal, Spacing = 8, VerticalAlignment = VerticalAlignment.Center };
                    resolvingStack.Children.Add(new FontIcon { Glyph = "\uE895", FontSize = 14, Foreground = new Microsoft.UI.Xaml.Media.SolidColorBrush(Windows.UI.Color.FromArgb(255, 139, 92, 246)) });
                    resolvingStack.Children.Add(new TextBlock
                    {
                        Text = task.IsActive ? "Risoluzione metadati e tabella delle parti in corso dalla rete P2P..." : "Dettagli file e parti disponibili dopo l'avvio del download.",
                        FontSize = 11,
                        Foreground = GetThemeSecondaryBrush(),
                        VerticalAlignment = VerticalAlignment.Center
                    });
                    resolvingCard.Child = resolvingStack;
                    contentPanel.Children.Add(resolvingCard);
                }
            }

            // 7. Live real-time update timer while dialog is open (200 ms tick)
            liveTimer = new DispatcherTimer { Interval = TimeSpan.FromMilliseconds(200) };
            liveTimer.Tick += (s, e) =>
            {
                if (!_isDialogOpen)
                {
                    liveTimer?.Stop();
                    return;
                }

                // Update Header
                titleBlock.Text = task.Filename;
                catTextBlock.Text = task.Category;
                progTextBlock.Text = task.ProgressPercentageFormatted;

                // Update Status Pill
                statusTextBlock.Text = task.StatusDisplay;
                var currentStatusBrush = TryParseColorBrush(task.StatusColor, Windows.UI.Color.FromArgb(255, 59, 130, 246));
                statusTextBlock.Foreground = currentStatusBrush;
                statusBorder.Background = new Microsoft.UI.Xaml.Media.SolidColorBrush(
                    Windows.UI.Color.FromArgb(30, currentStatusBrush.Color.R, currentStatusBrush.Color.G, currentStatusBrush.Color.B));

                if (!isTorrent)
                {
                    iconFont.Glyph = task.StatusIconGlyph;
                }

                // Update Error Card if status changed
                bool curError = task.IsError || string.Equals(task.Status, "Error", StringComparison.OrdinalIgnoreCase) || !string.IsNullOrWhiteSpace(task.ErrorMessage);
                errCard.Visibility = curError ? Visibility.Visible : Visibility.Collapsed;
                if (curError && !string.IsNullOrWhiteSpace(task.ErrorMessage))
                {
                    errText.Text = task.ErrorMessage;
                }

                // Update Metrics
                dimValBlock.Text = task.DownloadedFormatted;

                string curSpeed = task.IsTorrent && task.UploadSpeedBps > 0
                    ? $"⬇ {task.SpeedFormatted}  ⬆ {TaskItemViewModel.FormatSpeed(task.UploadSpeedBps)}"
                    : task.SpeedFormatted;
                speedValBlock.Text = curSpeed;

                etaValBlock.Text = task.EtaFormatted;

                if (isTorrent && valBlockParts != null && subvalBlockPeers != null)
                {
                    valBlockParts.Text = task.PiecesTotal > 0
                        ? $"{task.PiecesCompleted} / {task.PiecesTotal} parti"
                        : "In risoluzione...";
                    subvalBlockPeers.Text = $"({task.Seeds} Seed, {task.Peers} Peer connessi)";
                }

                // Update Torrent File Rows if active
                if (isTorrent && task.Files != null && fileRowBindings.Count > 0)
                {
                    foreach (var f in task.Files)
                    {
                        if (fileRowBindings.TryGetValue(f.Index, out var binding))
                        {
                            binding.dlBlock.Text = TaskItemViewModel.FormatBytes(f.Downloaded);
                            binding.pBar.Value = Math.Clamp(f.Progress * 100.0, 0.0, 100.0);
                            binding.pText.Text = $"{f.Progress * 100.0:0.#}%";
                        }
                    }
                }
            };

            var dialog = new ContentDialog
            {
                Title = isTorrent ? "Dettagli del Download (Torrent / P2P)" : "Dettagli del Download",
                CloseButtonText = "Chiudi",
                DefaultButton = ContentDialogButton.Close,
                Content = new ScrollViewer
                {
                    MaxHeight = isTorrent ? 540 : 440,
                    Content = contentPanel
                }
            };
            PrepareDialog(dialog);

            dialog.Closed += (s, e) =>
            {
                liveTimer?.Stop();
            };

            liveTimer.Start();
            await dialog.ShowAsync();
        }
        finally
        {
            liveTimer?.Stop();
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
