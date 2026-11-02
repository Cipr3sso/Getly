using System;
using System.Diagnostics;
using System.IO;
using System.Runtime.InteropServices;
using Microsoft.UI.Windowing;
using Microsoft.UI.Xaml;
using WinRT.Interop;
using Getly.UI.ViewModels;

namespace Getly.UI.Services;

/// <summary>
/// Manages the Windows System Tray (Notification Area) icon and its context menu
/// using native Win32 Shell_NotifyIconW and User32 APIs.
/// </summary>
public sealed class SystemTrayManager : IDisposable
{
    public const uint WM_USER = 0x0400;
    public const uint WM_TRAYICON = WM_USER + 2048;

    private const uint NIM_ADD = 0x00000000;
    private const uint NIM_MODIFY = 0x00000001;
    private const uint NIM_DELETE = 0x00000002;

    private const uint NIF_MESSAGE = 0x00000001;
    private const uint NIF_ICON = 0x00000002;
    private const uint NIF_TIP = 0x00000004;

    private const uint WM_LBUTTONUP = 0x0202;
    private const uint WM_LBUTTONDBLCLK = 0x0203;
    private const uint WM_RBUTTONUP = 0x0205;
    private const uint WM_CONTEXTMENU = 0x007B;
    private const uint WM_NULL = 0x0000;

    private const int SW_HIDE = 0;
    private const int SW_RESTORE = 9;

    private const uint TPM_RIGHTBUTTON = 0x0002;
    private const uint TPM_RETURNCMD = 0x0100;
    private const uint TPM_NONOTIFY = 0x0080;

    private const uint MF_STRING = 0x00000000;
    private const uint MF_SEPARATOR = 0x00000800;
    private const uint MF_DEFAULT = 0x00001000;

    private const int ID_OPEN = 1001;
    private const int ID_PAUSE_ALL = 1002;
    private const int ID_RESUME_ALL = 1003;
    private const int ID_EXIT = 1004;

    private const uint IMAGE_ICON = 1;
    private const uint LR_LOADFROMFILE = 0x00000010;

    private const uint TRAY_ICON_ID = 4242;

    [StructLayout(LayoutKind.Sequential)]
    private struct POINT
    {
        public int x;
        public int y;
    }

    [StructLayout(LayoutKind.Sequential, CharSet = CharSet.Unicode)]
    private struct NOTIFYICONDATA
    {
        public uint cbSize;
        public IntPtr hWnd;
        public uint uID;
        public uint uFlags;
        public uint uCallbackMessage;
        public IntPtr hIcon;
        [MarshalAs(UnmanagedType.ByValTStr, SizeConst = 128)]
        public string szTip;
        public uint dwState;
        public uint dwStateMask;
        [MarshalAs(UnmanagedType.ByValTStr, SizeConst = 256)]
        public string szInfo;
        public uint uTimeoutOrVersion;
        [MarshalAs(UnmanagedType.ByValTStr, SizeConst = 64)]
        public string szInfoTitle;
        public uint dwInfoFlags;
        public Guid guidItem;
        public IntPtr hBalloonIcon;
    }

    [DllImport("shell32.dll", CharSet = CharSet.Unicode, SetLastError = true)]
    [return: MarshalAs(UnmanagedType.Bool)]
    private static extern bool Shell_NotifyIconW(uint dwMessage, ref NOTIFYICONDATA lpData);

    [DllImport("user32.dll", CharSet = CharSet.Unicode)]
    private static extern uint RegisterWindowMessage(string lpString);

    [DllImport("user32.dll", SetLastError = true)]
    private static extern IntPtr CreatePopupMenu();

    [DllImport("user32.dll", CharSet = CharSet.Unicode, SetLastError = true)]
    [return: MarshalAs(UnmanagedType.Bool)]
    private static extern bool AppendMenu(IntPtr hMenu, uint uFlags, UIntPtr uIDNewItem, string lpNewItem);

    [DllImport("user32.dll", SetLastError = true)]
    [return: MarshalAs(UnmanagedType.Bool)]
    private static extern bool DestroyMenu(IntPtr hMenu);

    [DllImport("user32.dll", SetLastError = true)]
    [return: MarshalAs(UnmanagedType.Bool)]
    private static extern bool GetCursorPos(out POINT lpPoint);

    [DllImport("user32.dll", SetLastError = true)]
    private static extern int TrackPopupMenu(IntPtr hMenu, uint uFlags, int x, int y, int nReserved, IntPtr hWnd, IntPtr prcRect);

    [DllImport("user32.dll", SetLastError = true)]
    [return: MarshalAs(UnmanagedType.Bool)]
    private static extern bool PostMessage(IntPtr hWnd, uint msg, IntPtr wParam, IntPtr lParam);

    [DllImport("user32.dll")]
    [return: MarshalAs(UnmanagedType.Bool)]
    private static extern bool SetForegroundWindow(IntPtr hWnd);

    [DllImport("user32.dll")]
    [return: MarshalAs(UnmanagedType.Bool)]
    private static extern bool ShowWindow(IntPtr hWnd, int nCmdShow);

    [DllImport("shell32.dll", CharSet = CharSet.Unicode)]
    private static extern uint ExtractIconEx(string lpszFile, int nIconIndex, out IntPtr phiconLarge, out IntPtr phiconSmall, uint nIcons);

    [DllImport("user32.dll", CharSet = CharSet.Unicode, SetLastError = true)]
    private static extern IntPtr LoadImage(IntPtr hInst, string name, uint type, int cx, int cy, uint fuLoad);

    [DllImport("user32.dll", CharSet = CharSet.Unicode)]
    private static extern IntPtr LoadIcon(IntPtr hInstance, IntPtr lpIconName);

    [DllImport("user32.dll", SetLastError = true)]
    [return: MarshalAs(UnmanagedType.Bool)]
    private static extern bool DestroyIcon(IntPtr hIcon);

    private IntPtr _hWnd;
    private AppWindow? _appWindow;
    private Window? _window;
    private MainViewModel? _viewModel;

    private IntPtr _hIcon = IntPtr.Zero;
    private bool _ownIcon;
    private bool _trayIconAdded;
    private uint _wmTaskbarCreated;

    public bool IsExplicitExit { get; set; }

    public void Initialize(Window window, AppWindow appWindow, MainViewModel viewModel)
    {
        _window = window;
        _appWindow = appWindow;
        _viewModel = viewModel;
        _hWnd = WindowNative.GetWindowHandle(window);

        _wmTaskbarCreated = RegisterWindowMessage("TaskbarCreated");

        AddOrUpdateTrayIcon();
    }

    public void AddOrUpdateTrayIcon()
    {
        if (_hWnd == IntPtr.Zero) return;

        if (_hIcon == IntPtr.Zero)
        {
            _hIcon = LoadAppIcon(out _ownIcon);
        }

        var nid = new NOTIFYICONDATA
        {
            cbSize = (uint)Marshal.SizeOf<NOTIFYICONDATA>(),
            hWnd = _hWnd,
            uID = TRAY_ICON_ID,
            uFlags = NIF_MESSAGE | NIF_ICON | NIF_TIP,
            uCallbackMessage = WM_TRAYICON,
            hIcon = _hIcon,
            szTip = "Getly Download Manager"
        };

        if (!_trayIconAdded)
        {
            _trayIconAdded = Shell_NotifyIconW(NIM_ADD, ref nid);
        }
        else
        {
            Shell_NotifyIconW(NIM_MODIFY, ref nid);
        }
    }

    public void RemoveTrayIcon()
    {
        if (_trayIconAdded && _hWnd != IntPtr.Zero)
        {
            var nid = new NOTIFYICONDATA
            {
                cbSize = (uint)Marshal.SizeOf<NOTIFYICONDATA>(),
                hWnd = _hWnd,
                uID = TRAY_ICON_ID
            };
            Shell_NotifyIconW(NIM_DELETE, ref nid);
            _trayIconAdded = false;
        }

        if (_hIcon != IntPtr.Zero && _ownIcon)
        {
            DestroyIcon(_hIcon);
            _hIcon = IntPtr.Zero;
            _ownIcon = false;
        }
    }

    public IntPtr HandleWndProc(IntPtr hWnd, uint msg, IntPtr wParam, IntPtr lParam, out bool handled)
    {
        handled = false;

        if (msg == WM_TRAYICON)
        {
            uint mouseMsg = (uint)(lParam.ToInt64() & 0xFFFF);
            if (mouseMsg == WM_LBUTTONUP || mouseMsg == WM_LBUTTONDBLCLK)
            {
                RestoreWindow();
                handled = true;
                return IntPtr.Zero;
            }
            else if (mouseMsg == WM_RBUTTONUP || mouseMsg == WM_CONTEXTMENU)
            {
                ShowContextMenu();
                handled = true;
                return IntPtr.Zero;
            }
        }
        else if (_wmTaskbarCreated != 0 && msg == _wmTaskbarCreated)
        {
            AddOrUpdateTrayIcon();
            handled = false;
        }

        return IntPtr.Zero;
    }

    public void MinimizeToTray()
    {
        _window?.DispatcherQueue.TryEnqueue(() =>
        {
            _appWindow?.Hide();
            if (_hWnd != IntPtr.Zero)
            {
                ShowWindow(_hWnd, SW_HIDE);
            }
        });
    }

    public void RestoreWindow()
    {
        _window?.DispatcherQueue.TryEnqueue(() =>
        {
            _appWindow?.Show();
            if (_hWnd != IntPtr.Zero)
            {
                ShowWindow(_hWnd, SW_RESTORE);
                SetForegroundWindow(_hWnd);
            }
        });
    }

    private void ShowContextMenu()
    {
        if (_hWnd == IntPtr.Zero) return;

        GetCursorPos(out POINT pt);
        SetForegroundWindow(_hWnd);

        IntPtr hMenu = CreatePopupMenu();
        if (hMenu == IntPtr.Zero) return;

        try
        {
            AppendMenu(hMenu, MF_STRING | MF_DEFAULT, (UIntPtr)ID_OPEN, "Apri Getly");
            AppendMenu(hMenu, MF_SEPARATOR, UIntPtr.Zero, string.Empty);
            AppendMenu(hMenu, MF_STRING, (UIntPtr)ID_PAUSE_ALL, "Metti in pausa tutti");
            AppendMenu(hMenu, MF_STRING, (UIntPtr)ID_RESUME_ALL, "Riprendi tutti");
            AppendMenu(hMenu, MF_SEPARATOR, UIntPtr.Zero, string.Empty);
            AppendMenu(hMenu, MF_STRING, (UIntPtr)ID_EXIT, "Esci");

            int cmd = TrackPopupMenu(hMenu, TPM_RETURNCMD | TPM_RIGHTBUTTON | TPM_NONOTIFY, pt.x, pt.y, 0, _hWnd, IntPtr.Zero);
            PostMessage(_hWnd, WM_NULL, IntPtr.Zero, IntPtr.Zero);

            if (cmd == ID_OPEN)
            {
                RestoreWindow();
            }
            else if (cmd == ID_PAUSE_ALL)
            {
                _window?.DispatcherQueue.TryEnqueue(() => _ = _viewModel?.PauseAllTasksAsync());
            }
            else if (cmd == ID_RESUME_ALL)
            {
                _window?.DispatcherQueue.TryEnqueue(() => _ = _viewModel?.ResumeAllTasksAsync());
            }
            else if (cmd == ID_EXIT)
            {
                ExitApplication();
            }
        }
        finally
        {
            DestroyMenu(hMenu);
        }
    }

    public void ExitApplication()
    {
        IsExplicitExit = true;
        RemoveTrayIcon();

        _window?.DispatcherQueue.TryEnqueue(() =>
        {
            try
            {
                _viewModel?.Dispose();
            }
            catch { }

            try
            {
                _window?.Close();
            }
            catch { }

            try
            {
                Application.Current.Exit();
            }
            catch { }
        });
    }

    private static IntPtr LoadAppIcon(out bool ownIcon)
    {
        ownIcon = false;
        try
        {
            // 1. Try to extract icon from current executable
            string? exePath = Environment.ProcessPath;
            if (!string.IsNullOrEmpty(exePath) && File.Exists(exePath))
            {
                uint count = ExtractIconEx(exePath, 0, out _, out IntPtr hSmall, 1);
                if (count > 0 && hSmall != IntPtr.Zero)
                {
                    ownIcon = true;
                    return hSmall;
                }
            }

            // 2. Try to load from assets/app_icon.ico
            string[] candidates = new[]
            {
                Path.Combine(AppContext.BaseDirectory, "Assets", "app_icon.ico"),
                Path.Combine(AppContext.BaseDirectory, "assets", "app_icon.ico"),
                Path.Combine(AppContext.BaseDirectory, "app_icon.ico"),
                Path.Combine(Directory.GetCurrentDirectory(), "assets", "app_icon.ico")
            };

            foreach (var path in candidates)
            {
                if (File.Exists(path))
                {
                    IntPtr hIcon = LoadImage(IntPtr.Zero, path, IMAGE_ICON, 16, 16, LR_LOADFROMFILE);
                    if (hIcon != IntPtr.Zero)
                    {
                        ownIcon = true;
                        return hIcon;
                    }
                }
            }
        }
        catch
        {
            // Fallback below
        }

        // 3. Fallback to standard system application icon
        ownIcon = false;
        return LoadIcon(IntPtr.Zero, (IntPtr)32512); // IDI_APPLICATION
    }

    public void Dispose()
    {
        RemoveTrayIcon();
    }
}
