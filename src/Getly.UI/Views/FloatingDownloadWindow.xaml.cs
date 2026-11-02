using System;
using System.Collections.ObjectModel;
using System.ComponentModel;
using System.Diagnostics;
using System.Linq;
using System.Threading.Tasks;
using Getly.UI.ViewModels;
using Microsoft.UI.Windowing;
using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Controls;
using Windows.Graphics;
using Windows.Storage.Pickers;
using WinRT.Interop;

namespace Getly.UI.Views;

public sealed partial class FloatingDownloadWindow : Window
{
    private readonly MainViewModel _viewModel;
    private TaskItemViewModel? _currentTask;
    private DispatcherTimer? _autoCloseTimer;
    private bool _isExpanded;

    public ObservableCollection<TaskItemViewModel> ActiveTasks { get; } = new();
    public ObservableCollection<TaskItemViewModel> MonitoredTasks => ActiveTasks;
    public int CurrentIndex { get; private set; }

    public FloatingDownloadWindow(MainViewModel viewModel)
    {
        _viewModel = viewModel;
        InitializeComponent();

        // 1. Remove from Windows taskbar and Alt+Tab switcher (Floating Overlay Widget)
        AppWindow.IsShownInSwitchers = false;

        // 2. Configure OverlappedPresenter: Always on top, no standard borders, fixed size
        if (AppWindow.Presenter is OverlappedPresenter presenter)
        {
            presenter.IsAlwaysOnTop = true;
            presenter.IsResizable = false;
            presenter.IsMinimizable = false;
            presenter.IsMaximizable = false;
            presenter.SetBorderAndTitleBar(false, false);
        }

        // 3. Extends content into titlebar & enable dragging on header
        ExtendsContentIntoTitleBar = true;
        SetTitleBar(DragRegion);

        // 4. Bind ListView ItemsSource
        TasksListView.ItemsSource = ActiveTasks;

        // 5. Position window at bottom-right above the taskbar
        PositionWindow();

        // 6. Default dark theme
        RootGrid.RequestedTheme = ElementTheme.Dark;

        // Cleanup on close
        Closed += OnWindowClosed;
    }

    public void SetTheme(ElementTheme theme)
    {
        RootGrid.RequestedTheme = theme;
    }

    private void PositionWindow()
    {
        try
        {
            int width = 380;
            int height = _isExpanded ? 350 : 175;
            AppWindow.Resize(new SizeInt32(width, height));

            var displayArea = DisplayArea.Primary;
            if (displayArea != null)
            {
                int posX = displayArea.WorkArea.X + displayArea.WorkArea.Width - 400;
                int posY = _isExpanded
                    ? displayArea.WorkArea.Y + displayArea.WorkArea.Height - 370
                    : displayArea.WorkArea.Y + displayArea.WorkArea.Height - 195;
                AppWindow.Move(new PointInt32(posX, posY));
            }
        }
        catch (Exception ex)
        {
            Debug.WriteLine($"[FloatingWindow] Position error: {ex.Message}");
        }
    }

    /// <summary>
    /// Adds or updates a task in the unified multi-download collection and focuses it.
    /// </summary>
    public void AddOrUpdateTask(TaskItemViewModel task)
    {
        int existingIndex = ActiveTasks.IndexOf(task);
        if (existingIndex < 0)
        {
            ActiveTasks.Add(task);
            CurrentIndex = ActiveTasks.Count - 1;
        }
        else
        {
            CurrentIndex = existingIndex;
        }

        BindCurrentTask();
        UpdateUI();
    }

    /// <summary>
    /// Backward-compatibility alias for AddOrUpdateTask.
    /// </summary>
    public void BindTask(TaskItemViewModel task)
    {
        AddOrUpdateTask(task);
    }

    private void BindCurrentTask()
    {
        if (_currentTask != null)
        {
            _currentTask.PropertyChanged -= OnTaskPropertyChanged;
        }

        if (CurrentIndex >= 0 && CurrentIndex < ActiveTasks.Count)
        {
            _currentTask = ActiveTasks[CurrentIndex];
            _currentTask.PropertyChanged += OnTaskPropertyChanged;
        }
        else
        {
            _currentTask = null;
        }
    }

    private void RemoveTaskFromMonitoring(TaskItemViewModel task)
    {
        task.PropertyChanged -= OnTaskPropertyChanged;
        ActiveTasks.Remove(task);

        if (ActiveTasks.Count == 0)
        {
            _currentTask = null;
            this.Close();
            return;
        }

        if (CurrentIndex >= ActiveTasks.Count)
        {
            CurrentIndex = ActiveTasks.Count - 1;
        }

        BindCurrentTask();
        UpdateUI();
    }

    private void OnTaskPropertyChanged(object? sender, PropertyChangedEventArgs e)
    {
        DispatcherQueue.TryEnqueue(UpdateUI);
    }

    private void UpdateUI()
    {
        // 1. Navigation Scroller & Counter
        int totalCount = ActiveTasks.Count;
        TaskCounterTextBlock.Text = $"{CurrentIndex + 1}/{Math.Max(1, totalCount)}";
        PrevTaskButton.IsEnabled = CurrentIndex > 0;
        NextTaskButton.IsEnabled = CurrentIndex < totalCount - 1;

        bool showNavigation = totalCount > 1;
        PrevTaskButton.Visibility = showNavigation ? Visibility.Visible : Visibility.Collapsed;
        TaskCounterTextBlock.Visibility = showNavigation ? Visibility.Visible : Visibility.Collapsed;
        NextTaskButton.Visibility = showNavigation ? Visibility.Visible : Visibility.Collapsed;

        if (_currentTask is null) return;

        FileNameTextBlock.Text = !string.IsNullOrWhiteSpace(_currentTask.Filename) ? _currentTask.Filename : "Download";
        SourceUrlTextBlock.Text = _currentTask.Url ?? string.Empty;
        DestinationPathTextBlock.Text = string.IsNullOrWhiteSpace(_currentTask.DestinationDir)
            ? "Cartella predefinita"
            : _currentTask.DestinationDir;

        // Progress percentage & bar
        TaskProgressBar.Value = _currentTask.Progress;
        PercentageTextBlock.Text = _currentTask.ProgressPercentageFormatted;

        // 2. Pre-Download Confirmation Flow (Waiting / Pending Confirmation)
        if (_currentTask.IsPendingConfirmation)
        {
            ConfirmationActionsPanel.Visibility = Visibility.Visible;
            StandardActionsPanel.Visibility = Visibility.Collapsed;
            NormalSpeedPanel.Visibility = Visibility.Visible;
            ErrorSpeedPanel.Visibility = Visibility.Collapsed;

            ProgressSizeTextBlock.Text = _currentTask.TotalSize > 0
                ? $"Dimensione: {TaskItemViewModel.FormatBytes(_currentTask.TotalSize)}"
                : "Dimensione non nota";

            PercentageTextBlock.Text = "In attesa";
            SpeedIconTextBlock.Text = "⏱";
            SpeedTextBlock.Text = "Pronto per l'avvio";
            EtaTextBlock.Text = "--:--";
            StatusBadgeTextBlock.Text = "In attesa di conferma";

            StopAutoCloseTimer();
        }
        // 3. Error Fallback (CDN / network errors)
        else if (_currentTask.IsError)
        {
            ConfirmationActionsPanel.Visibility = Visibility.Collapsed;
            StandardActionsPanel.Visibility = Visibility.Visible;
            NormalSpeedPanel.Visibility = Visibility.Collapsed;
            ErrorSpeedPanel.Visibility = Visibility.Visible;

            ErrorMessageTextBlock.Text = !string.IsNullOrWhiteSpace(_currentTask.ErrorMessage)
                ? _currentTask.ErrorMessage
                : "Connessione fallita - CDN";

            ProgressSizeTextBlock.Text = _currentTask.TotalSize > 0
                ? $"{TaskItemViewModel.FormatBytes(_currentTask.DownloadedBytes)} di {TaskItemViewModel.FormatBytes(_currentTask.TotalSize)}"
                : $"{TaskItemViewModel.FormatBytes(_currentTask.DownloadedBytes)}";

            ActionIcon1.Glyph = "\uE72C"; // Refresh / Retry glyph
            ActionText1.Text = "Riprova";
            ActionIcon2.Glyph = "\uED25";
            ActionText2.Text = "Cartella";

            StatusBadgeTextBlock.Text = "Errore";
            StopAutoCloseTimer();
        }
        // 4. Completed
        else if (_currentTask.IsCompleted)
        {
            ConfirmationActionsPanel.Visibility = Visibility.Collapsed;
            StandardActionsPanel.Visibility = Visibility.Visible;
            NormalSpeedPanel.Visibility = Visibility.Visible;
            ErrorSpeedPanel.Visibility = Visibility.Collapsed;

            ProgressSizeTextBlock.Text = $"{TaskItemViewModel.FormatBytes(_currentTask.DownloadedBytes)} di {TaskItemViewModel.FormatBytes(_currentTask.TotalSize)} (100%)";
            PercentageTextBlock.Text = "100%";

            ActionIcon1.Glyph = "\uE8E5"; // Play / Open
            ActionText1.Text = "Apri file";
            ActionIcon2.Glyph = "\uED25";
            ActionText2.Text = "Cartella";

            SpeedIconTextBlock.Text = "✔";
            SpeedTextBlock.Text = "Completato";
            EtaTextBlock.Text = "Pronto";
            StatusBadgeTextBlock.Text = "Completato";

            StartAutoCloseTimer();
        }
        // 5. Paused
        else if (_currentTask.IsPaused)
        {
            ConfirmationActionsPanel.Visibility = Visibility.Collapsed;
            StandardActionsPanel.Visibility = Visibility.Visible;
            NormalSpeedPanel.Visibility = Visibility.Visible;
            ErrorSpeedPanel.Visibility = Visibility.Collapsed;

            ProgressSizeTextBlock.Text = _currentTask.TotalSize > 0
                ? $"{TaskItemViewModel.FormatBytes(_currentTask.DownloadedBytes)} di {TaskItemViewModel.FormatBytes(_currentTask.TotalSize)} ({_currentTask.Progress:0.#}%)"
                : $"{TaskItemViewModel.FormatBytes(_currentTask.DownloadedBytes)} ({_currentTask.Progress:0.#}%)";

            ActionIcon1.Glyph = "\uE768"; // Resume
            ActionText1.Text = "Riprendi";
            ActionIcon2.Glyph = "\uED25";
            ActionText2.Text = "Cartella";

            SpeedIconTextBlock.Text = "⏸";
            SpeedTextBlock.Text = "In pausa";
            EtaTextBlock.Text = "--:--";
            StatusBadgeTextBlock.Text = "In pausa";

            StopAutoCloseTimer();
        }
        // 6. Active Downloading
        else
        {
            ConfirmationActionsPanel.Visibility = Visibility.Collapsed;
            StandardActionsPanel.Visibility = Visibility.Visible;
            NormalSpeedPanel.Visibility = Visibility.Visible;
            ErrorSpeedPanel.Visibility = Visibility.Collapsed;

            ProgressSizeTextBlock.Text = _currentTask.TotalSize > 0
                ? $"{TaskItemViewModel.FormatBytes(_currentTask.DownloadedBytes)} di {TaskItemViewModel.FormatBytes(_currentTask.TotalSize)} ({_currentTask.Progress:0.#}%)"
                : $"{TaskItemViewModel.FormatBytes(_currentTask.DownloadedBytes)} ({_currentTask.Progress:0.#}%)";

            ActionIcon1.Glyph = "\uE769"; // Pause
            ActionText1.Text = "Pausa";
            ActionIcon2.Glyph = "\uED25";
            ActionText2.Text = "Cartella";

            SpeedIconTextBlock.Text = "⬇";
            SpeedTextBlock.Text = _currentTask.SpeedFormatted;
            EtaTextBlock.Text = _currentTask.EtaFormatted;
            StatusBadgeTextBlock.Text = _currentTask.StatusDisplay;

            StopAutoCloseTimer();
        }

        // Status Glyph and Color
        FileStatusIcon.Glyph = _currentTask.StatusIconGlyph;
        try
        {
            var brush = new Microsoft.UI.Xaml.Media.SolidColorBrush(ParseColor(_currentTask.StatusColor));
            FileStatusIcon.Foreground = brush;
            StatusBadgeTextBlock.Foreground = brush;
            TaskProgressBar.Foreground = brush;
        }
        catch { }
    }

    private void PrevTaskButton_Click(object sender, RoutedEventArgs e)
    {
        if (CurrentIndex > 0)
        {
            CurrentIndex--;
            BindCurrentTask();
            UpdateUI();
        }
    }

    private void NextTaskButton_Click(object sender, RoutedEventArgs e)
    {
        if (CurrentIndex < ActiveTasks.Count - 1)
        {
            CurrentIndex++;
            BindCurrentTask();
            UpdateUI();
        }
    }

    private void ExpandCollapseButton_Click(object sender, RoutedEventArgs e)
    {
        _isExpanded = !_isExpanded;
        ExpandedListPanel.Visibility = _isExpanded ? Visibility.Visible : Visibility.Collapsed;
        ExpandCollapseIcon.Glyph = _isExpanded ? "\uE70E" : "\uE70D";
        ToolTipService.SetToolTip(ExpandCollapseButton, _isExpanded ? "Comprimi elenco" : "Elenco download");
        PositionWindow();
    }

    private void ActionButton1_Click(object sender, RoutedEventArgs e)
    {
        StopAutoCloseTimer();
        if (_currentTask is null) return;

        if (_currentTask.IsCompleted)
        {
            _viewModel.OpenFileCommand.Execute(_currentTask);
        }
        else if (_currentTask.IsPaused || _currentTask.IsError)
        {
            _viewModel.ResumeTaskCommand.Execute(_currentTask);
        }
        else
        {
            _viewModel.PauseTaskCommand.Execute(_currentTask);
        }
    }

    private void ActionButton2_Click(object sender, RoutedEventArgs e)
    {
        StopAutoCloseTimer();
        if (_currentTask is null) return;
        _viewModel.OpenContainingFolderCommand.Execute(_currentTask);
    }

    private void StartNowButton_Click(object sender, RoutedEventArgs e)
    {
        if (_currentTask is null) return;
        _viewModel.ResumeTaskCommand.Execute(_currentTask);
        UpdateUI();
    }

    private void EnqueueButton_Click(object sender, RoutedEventArgs e)
    {
        if (_currentTask is null) return;
        _viewModel.ResumeTaskCommand.Execute(_currentTask);
        UpdateUI();
    }

    private void CancelTaskButton_Click(object sender, RoutedEventArgs e)
    {
        if (_currentTask is null) return;
        var toRemove = _currentTask;
        _viewModel.DeleteTaskCommand.Execute(toRemove);
        RemoveTaskFromMonitoring(toRemove);
    }

    private async void ChangeFolderButton_Click(object sender, RoutedEventArgs e)
    {
        if (_currentTask is null) return;
        try
        {
            var picker = new FolderPicker();
            picker.SuggestedStartLocation = PickerLocationId.Downloads;
            picker.FileTypeFilter.Add("*");

            var hwnd = WindowNative.GetWindowHandle(this);
            InitializeWithWindow.Initialize(picker, hwnd);

            var folder = await picker.PickSingleFolderAsync();
            if (folder != null)
            {
                _currentTask.DestinationDir = folder.Path;
                DestinationPathTextBlock.Text = folder.Path;
            }
        }
        catch (Exception ex)
        {
            Debug.WriteLine($"[FloatingWindow] Folder pick error: {ex.Message}");
        }
    }

    private void SelectAllCheckBox_Click(object sender, RoutedEventArgs e)
    {
        bool isChecked = SelectAllCheckBox.IsChecked ?? false;
        foreach (var task in ActiveTasks)
        {
            task.IsSelected = isChecked;
        }
    }

    private void StartSelectedButton_Click(object sender, RoutedEventArgs e)
    {
        var selected = ActiveTasks.Where(t => t.IsSelected).ToList();
        foreach (var t in selected)
        {
            _viewModel.ResumeTaskCommand.Execute(t);
        }
        UpdateUI();
    }

    private void DeleteSelectedButton_Click(object sender, RoutedEventArgs e)
    {
        var selected = ActiveTasks.Where(t => t.IsSelected).ToList();
        foreach (var t in selected)
        {
            _viewModel.DeleteTaskCommand.Execute(t);
            RemoveTaskFromMonitoring(t);
        }
    }

    private void TasksListView_SelectionChanged(object sender, SelectionChangedEventArgs e)
    {
        if (TasksListView.SelectedItem is TaskItemViewModel clicked)
        {
            int idx = ActiveTasks.IndexOf(clicked);
            if (idx >= 0)
            {
                CurrentIndex = idx;
                BindCurrentTask();
                UpdateUI();
            }
        }
    }

    private void CloseButton_Click(object sender, RoutedEventArgs e)
    {
        StopAutoCloseTimer();
        this.Close();
    }

    private void StartAutoCloseTimer()
    {
        if (_autoCloseTimer is null)
        {
            _autoCloseTimer = new DispatcherTimer
            {
                Interval = TimeSpan.FromSeconds(15)
            };
            _autoCloseTimer.Tick += (s, e) =>
            {
                _autoCloseTimer.Stop();
                this.Close();
            };
        }

        if (!_autoCloseTimer.IsEnabled)
        {
            _autoCloseTimer.Start();
        }
    }

    private void StopAutoCloseTimer()
    {
        if (_autoCloseTimer != null && _autoCloseTimer.IsEnabled)
        {
            _autoCloseTimer.Stop();
        }
    }

    private void OnWindowClosed(object sender, WindowEventArgs args)
    {
        StopAutoCloseTimer();
        if (_currentTask != null)
        {
            _currentTask.PropertyChanged -= OnTaskPropertyChanged;
            _currentTask = null;
        }
        ActiveTasks.Clear();
    }

    private static Windows.UI.Color ParseColor(string hex)
    {
        if (string.IsNullOrEmpty(hex)) return Microsoft.UI.Colors.DodgerBlue;
        hex = hex.TrimStart('#');
        if (hex.Length == 6)
        {
            byte r = Convert.ToByte(hex.Substring(0, 2), 16);
            byte g = Convert.ToByte(hex.Substring(2, 2), 16);
            byte b = Convert.ToByte(hex.Substring(4, 2), 16);
            return Windows.UI.Color.FromArgb(255, r, g, b);
        }
        if (hex.Length == 8)
        {
            byte a = Convert.ToByte(hex.Substring(0, 2), 16);
            byte r = Convert.ToByte(hex.Substring(2, 2), 16);
            byte g = Convert.ToByte(hex.Substring(4, 2), 16);
            byte b = Convert.ToByte(hex.Substring(6, 2), 16);
            return Windows.UI.Color.FromArgb(a, r, g, b);
        }
        return Microsoft.UI.Colors.DodgerBlue;
    }
}