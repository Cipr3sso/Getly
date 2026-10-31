using System;
using System.ComponentModel;
using System.Diagnostics;
using System.Runtime.InteropServices;

namespace Getly.UI.Services;

/// <summary>
/// Manages Windows Job Object to enforce kernel-level lifecycle management.
/// Configures JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE so that any assigned child process
/// (e.g. the Python headless engine) is automatically terminated by Windows if the UI crashes or closes.
/// </summary>
public sealed class JobObjectManager : IDisposable
{
    private IntPtr _jobHandle;
    private bool _disposed;

    public const uint JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x00002000;

    public JobObjectManager()
    {
        _jobHandle = CreateJobObject(IntPtr.Zero, null);
        if (_jobHandle == IntPtr.Zero)
        {
            throw new Win32Exception(Marshal.GetLastWin32Error(), "Impossibile creare il Windows Job Object.");
        }

        var extendedInfo = new JOBOBJECT_EXTENDED_LIMIT_INFORMATION
        {
            BasicLimitInformation = new JOBOBJECT_BASIC_LIMIT_INFORMATION
            {
                LimitFlags = JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
            }
        };

        int length = Marshal.SizeOf(typeof(JOBOBJECT_EXTENDED_LIMIT_INFORMATION));
        IntPtr extendedInfoPtr = Marshal.AllocHGlobal(length);
        try
        {
            Marshal.StructureToPtr(extendedInfo, extendedInfoPtr, false);
            if (!SetInformationJobObject(_jobHandle, JobObjectInfoClass.JobObjectExtendedLimitInformation, extendedInfoPtr, (uint)length))
            {
                throw new Win32Exception(Marshal.GetLastWin32Error(), "Impossibile configurare JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE sul Job Object.");
            }
        }
        finally
        {
            Marshal.FreeHGlobal(extendedInfoPtr);
        }
    }

    /// <summary>
    /// Assigns a running process to the Job Object.
    /// </summary>
    public bool AssignProcess(Process process)
    {
        ObjectDisposedException.ThrowIf(_disposed, this);
        ArgumentNullException.ThrowIfNull(process);

        if (!AssignProcessToJobObject(_jobHandle, process.Handle))
        {
            int err = Marshal.GetLastWin32Error();
            // Error 5 = Access Denied, 6 = Invalid Handle
            return false;
        }

        return true;
    }

    public void Dispose()
    {
        if (!_disposed)
        {
            if (_jobHandle != IntPtr.Zero)
            {
                CloseHandle(_jobHandle);
                _jobHandle = IntPtr.Zero;
            }
            _disposed = true;
        }
    }

    #region Win32 P/Invoke Definitions

    private enum JobObjectInfoClass
    {
        JobObjectExtendedLimitInformation = 9
    }

    [StructLayout(LayoutKind.Sequential)]
    private struct IO_COUNTERS
    {
        public ulong ReadOperationCount;
        public ulong WriteOperationCount;
        public ulong OtherOperationCount;
        public ulong ReadTransferCount;
        public ulong WriteTransferCount;
        public ulong OtherTransferCount;
    }

    [StructLayout(LayoutKind.Sequential)]
    private struct JOBOBJECT_BASIC_LIMIT_INFORMATION
    {
        public long PerProcessUserTimeLimit;
        public long PerJobUserTimeLimit;
        public uint LimitFlags;
        public UIntPtr MinimumWorkingSetSize;
        public UIntPtr MaximumWorkingSetSize;
        public uint ActiveProcessLimit;
        public UIntPtr Affinity;
        public uint PriorityClass;
        public uint SchedulingClass;
    }

    [StructLayout(LayoutKind.Sequential)]
    private struct JOBOBJECT_EXTENDED_LIMIT_INFORMATION
    {
        public JOBOBJECT_BASIC_LIMIT_INFORMATION BasicLimitInformation;
        public IO_COUNTERS IoInfo;
        public UIntPtr ProcessMemoryLimit;
        public UIntPtr JobMemoryLimit;
        public UIntPtr PeakProcessMemoryLimit;
        public UIntPtr PeakJobMemoryLimit;
    }

    [DllImport("kernel32.dll", CharSet = CharSet.Unicode, SetLastError = true)]
    private static extern IntPtr CreateJobObject(IntPtr lpJobAttributes, string? lpName);

    [DllImport("kernel32.dll", SetLastError = true)]
    [return: MarshalAs(UnmanagedType.Bool)]
    private static extern bool SetInformationJobObject(
        IntPtr hJob,
        JobObjectInfoClass JobObjectInformationClass,
        IntPtr lpJobObjectInformation,
        uint cbJobObjectInformationLength);

    [DllImport("kernel32.dll", SetLastError = true)]
    [return: MarshalAs(UnmanagedType.Bool)]
    private static extern bool AssignProcessToJobObject(IntPtr hJob, IntPtr hProcess);

    [DllImport("kernel32.dll", SetLastError = true)]
    [return: MarshalAs(UnmanagedType.Bool)]
    private static extern bool CloseHandle(IntPtr hObject);

    #endregion
}
