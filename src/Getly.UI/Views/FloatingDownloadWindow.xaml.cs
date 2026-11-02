using System;
using System.ComponentModel;
using System.Diagnostics;
using Getly.UI.ViewModels;
using Microsoft.UI.Windowing;
using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Controls;
using Windows.Graphics;

namespace Getly.UI.Views;

public sealed partial class FloatingDownloadWindow : Window
{
    private readonly MainViewModel _viewModel;
    private TaskItemViewModel? _currentTask;
    private DispatcherTimer? _autoCloseTimer;

    public FloatingDownloadWindow(MainViewModel viewModel)
    {
        _viewModel = viewModel;
        InitializeComponent();

        // 1. Configure OverlappedPresenter: Always on top, no borders, fixed size
        var presenter = AppWindow.Presenter as OverlappedPresenter;
        if (presenter != null)
        {
            presenter.IsAlwaysOnTop = true;
            presenter.IsResizable = false;
            presenter.IsMinimizable = false;
            presenter.IsMaximizable = false;
            presenter.SetBorderAndTitleBar(false, false);
        }

        // 2. Extends content into titlebar & enable dragging on header
        ExtendsContentIntoTitleBar = true;
        SetTitleBar(DragRegion);

        // 3. Position window at bottom-right above the taskbar
        PositionWindow();

        // 4. Default dark theme
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
            int height = 165;
            AppWindow.Resize(new SizeInt32(width, height));

            var displayArea = DisplayArea.Primary;
            if (displayArea != null)
            {
                int posX = displayArea.WorkArea.X + displayArea.WorkArea.Width - 400;
                int posY = displayArea.WorkArea.Y + displayArea.WorkArea.Height - 185;
                AppWindow.Move(new PointInt32(posX, posY));
            }
        }
        catch (Exception ex)
        {
            Debug.WriteLine($"[FloatingWindow] Position error: {ex.Message}");
        }
    }

    /// <summary>
    /// Binds the floating window to a task, synchronizing UI updates with task property changes.
    /// </summary>
    public void BindTask(TaskItemViewModel task)
    {
        if (_currentTask == task)
        {
            UpdateUI();
            return;
        }

        if (_currentTask != null)
        {
            _currentTask.PropertyChanged -= OnTaskPropertyChanged;
        }

        _currentTask = task;
        _currentTask.PropertyChanged += OnTaskPropertyChanged;
        UpdateUI();
    }

    private void OnTaskPropertyChanged(object? sender, PropertyChangedEventArgs e)
    {
        DispatcherQueue.TryEnqueue(UpdateUI);
    }

    private void UpdateUI()
    {
        if (_currentTask is null) return;

        FileNameTextBlock.Text = !string.IsNullOrWhiteSpace(_currentTask.Filename) ? _currentTask.Filename : "Download";
        SourceUrlTextBlock.Text = _currentTask.Url ?? string.Empty;

        // Progress percentage
        TaskProgressBar.Value = _currentTask.Progress;
        PercentageTextBlock.Text = _currentTask.ProgressPercentageFormatted;

        // Size & progress text: "35.4 MB di 100 MB (35%)"
        if (_currentTask.TotalSize > 0)
        {
            ProgressSizeTextBlock.Text = $"{TaskItemViewModel.FormatBytes(_currentTask.DownloadedBytes)} di {TaskItemViewModel.FormatBytes(_currentTask.TotalSize)} ({_currentTask.Progress:0.#}%)";
        }
        else
        {
            ProgressSizeTextBlock.Text = $"{TaskItemViewModel.FormatBytes(_currentTask.DownloadedBytes)} ({_currentTask.Progress:0.#}%)";
        }

        // Live speed & ETA: "⬇️ 4.2 MB/s • ETA: 00:15"
        SpeedTextBlock.Text = _currentTask.SpeedFormatted;
        EtaTextBlock.Text = _currentTask.EtaFormatted;
        StatusBadgeTextBlock.Text = _currentTask.StatusDisplay;

        // Icon & status color
        FileStatusIcon.Glyph = _currentTask.StatusIconGlyph;
        try
        {
            var brush = new Microsoft.UI.Xaml.Media.SolidColorBrush(ParseColor(_currentTask.StatusColor));
            FileStatusIcon.Foreground = brush;
            StatusBadgeTextBlock.Foreground = brush;
            TaskProgressBar.Foreground = brush;
        }
        catch { }

        // Dynamic footer actions
        if (_currentTask.IsCompleted)
        {
            // Completed: "▶️ Apri file" and "📂 Mostra nella cartella"
            ActionIcon1.Glyph = "\uE8E5"; // Open file / play glyph
            ActionText1.Text = "Apri file";

            ActionIcon2.Glyph = "\uED25"; // Folder glyph
            ActionText2.Text = "Mostra cartella";

            SpeedIconTextBlock.Text = "✔";
            SpeedTextBlock.Text = "Completato";
            EtaTextBlock.Text = "Pronto";

            StartAutoCloseTimer();
        }
        else if (_currentTask.IsPaused)
        {
            // Paused: "▶️ Riprendi" and "📂 Cartella"
            ActionIcon1.Glyph = "\uE768"; // Resume
            ActionText1.Text = "Riprendi";

            ActionIcon2.Glyph = "\uED25";
            ActionText2.Text = "Cartella";

            SpeedIconTextBlock.Text = "⏸";
            SpeedTextBlock.Text = "In pausa";
            StopAutoCloseTimer();
        }
        else
        {
            // Downloading: "⏸️ Pausa" and "📂 Cartella"
            ActionIcon1.Glyph = "\uE769"; // Pause
            ActionText1.Text = "Pausa";

            ActionIcon2.Glyph = "\uED25";
            ActionText2.Text = "Cartella";

            SpeedIconTextBlock.Text = "⬇";
            StopAutoCloseTimer();
        }
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

    private void ActionButton1_Click(object sender, RoutedEventArgs e)
    {
        StopAutoCloseTimer();
        if (_currentTask is null) return;

        if (_currentTask.IsCompleted)
        {
            _viewModel.OpenFileCommand.Execute(_currentTask);
        }
        else if (_currentTask.IsPaused)
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

    private void CloseButton_Click(object sender, RoutedEventArgs e)
    {
        StopAutoCloseTimer();
        this.Close();
    }

    private void OnWindowClosed(object sender, WindowEventArgs args)
    {
        StopAutoCloseTimer();
        if (_currentTask != null)
        {
            _currentTask.PropertyChanged -= OnTaskPropertyChanged;
            _currentTask = null;
        }
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
