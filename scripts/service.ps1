param([Parameter(Mandatory=$true)][ValidateSet('backend','frontend')][string]$Role,
      [Parameter(Mandatory=$true)][string]$Token)
$ErrorActionPreference = 'Stop'
$root = Split-Path $PSScriptRoot -Parent
$log = Join-Path $root 'logs\paperagent.log'
function Write-Event([string]$message) {
    $line = '{0} {1}' -f (Get-Date -Format o), $message
    try { Add-Content -LiteralPath $log -Value $line -Encoding UTF8 -ErrorAction Stop } catch {}
}
try {
    # Windows closes this handle when the supervisor exits, terminating its own
    # children even if the supervisor itself is forcibly stopped.
    Add-Type -TypeDefinition @'
using System;
using System.ComponentModel;
using System.Runtime.InteropServices;
public static class PaperAgentJob {
    [StructLayout(LayoutKind.Sequential)] struct BasicLimit {
        public long PerProcessUserTimeLimit, PerJobUserTimeLimit;
        public uint LimitFlags;
        public UIntPtr MinimumWorkingSetSize, MaximumWorkingSetSize;
        public uint ActiveProcessLimit;
        public UIntPtr Affinity;
        public uint PriorityClass, SchedulingClass;
    }
    [StructLayout(LayoutKind.Sequential)] struct IoCounters {
        public ulong ReadOperationCount, WriteOperationCount, OtherOperationCount;
        public ulong ReadTransferCount, WriteTransferCount, OtherTransferCount;
    }
    [StructLayout(LayoutKind.Sequential)] struct ExtendedLimit {
        public BasicLimit BasicLimitInformation;
        public IoCounters IoInfo;
        public UIntPtr ProcessMemoryLimit, JobMemoryLimit, PeakProcessMemoryUsed, PeakJobMemoryUsed;
    }
    [DllImport("kernel32.dll", CharSet=CharSet.Unicode, SetLastError=true)] static extern IntPtr CreateJobObject(IntPtr attributes, string name);
    [DllImport("kernel32.dll", SetLastError=true)] static extern bool SetInformationJobObject(IntPtr job, int infoClass, ref ExtendedLimit info, uint length);
    [DllImport("kernel32.dll", SetLastError=true)] static extern bool AssignProcessToJobObject(IntPtr job, IntPtr process);
    [DllImport("kernel32.dll")] static extern IntPtr GetCurrentProcess();
    [DllImport("kernel32.dll")] static extern bool CloseHandle(IntPtr handle);
    static IntPtr job;
    public static void Attach() {
        job = CreateJobObject(IntPtr.Zero, null);
        if (job == IntPtr.Zero) throw new Win32Exception();
        ExtendedLimit info = new ExtendedLimit();
        info.BasicLimitInformation.LimitFlags = 0x2000;
        if (!SetInformationJobObject(job, 9, ref info, (uint)Marshal.SizeOf(typeof(ExtendedLimit))) || !AssignProcessToJobObject(job, GetCurrentProcess())) {
            int error = Marshal.GetLastWin32Error(); CloseHandle(job); throw new Win32Exception(error);
        }
    }
}
'@
    [PaperAgentJob]::Attach()
    # Uvicorn writes normal startup messages to stderr; do not treat those as terminating errors.
    $ErrorActionPreference = 'Continue'
    if ($Role -eq 'backend') {
        Set-Location -LiteralPath (Join-Path $root '后端')
        & (Join-Path $root '后端\.venv\Scripts\python.exe') -m uvicorn app.main:app --host 127.0.0.1 --port 8000 *> $null
    } else {
        Set-Location -LiteralPath (Join-Path $root '前端')
        # Keep this launcher and its proxy on the verified local backend only.
        $env:VITE_API_PROXY_TARGET = 'http://127.0.0.1:8000'
        & node (Join-Path $root '前端\node_modules\vite\bin\vite.js') --host 127.0.0.1 --port 5173 --strictPort *> $null
    }
    Write-Event "$Role exited, code=$LASTEXITCODE, owner=$Token"
} catch { Write-Event "$Role exited unexpectedly, owner=$Token" }
