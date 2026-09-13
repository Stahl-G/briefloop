// Windows-only environment preparation supervisor. No Python installation is
// needed to own Python probes and their descendants. Runs under Windows PowerShell.
using System;
using System.ComponentModel;
using System.IO;
using System.Runtime.InteropServices;
using System.Text;
using System.Threading;
using Microsoft.Win32.SafeHandles;

public static class BriefLoopOwnedProcess
{
    [StructLayout(LayoutKind.Sequential)] struct SecurityAttributes { public int length; public IntPtr descriptor; public int inherit; }
    [StructLayout(LayoutKind.Sequential, CharSet = CharSet.Unicode)] struct StartupInfo {
        public int cb; public string reserved, desktop, title; public int x, y, xSize, ySize, xCount, yCount, fill, flags;
        public short show, reservedSize; public IntPtr reservedBytes, stdin, stdout, stderr;
    }
    [StructLayout(LayoutKind.Sequential)] struct ProcessInformation { public IntPtr process, thread; public uint pid, tid; }
    [StructLayout(LayoutKind.Sequential)] struct BasicLimit {
        public long processTime, jobTime; public uint flags; public UIntPtr minWorking, maxWorking;
        public uint activeLimit; public UIntPtr affinity; public uint priority, scheduling;
    }
    [StructLayout(LayoutKind.Sequential)] struct IoCounters { public ulong readOps, writeOps, otherOps, readBytes, writeBytes, otherBytes; }
    [StructLayout(LayoutKind.Sequential)] struct ExtendedLimit { public BasicLimit basic; public IoCounters io; public UIntPtr processMemory, jobMemory, peakProcess, peakJob; }
    [StructLayout(LayoutKind.Sequential)] struct Accounting { public long user, kernel, periodUser, periodKernel; public uint faults, total, active, terminated; }
    [DllImport("kernel32.dll", CharSet = CharSet.Unicode, SetLastError = true)] static extern IntPtr CreateJobObjectW(IntPtr attributes, string name);
    [DllImport("kernel32.dll", SetLastError = true)] static extern bool SetInformationJobObject(IntPtr job, int kind, ref ExtendedLimit info, uint size);
    [DllImport("kernel32.dll", SetLastError = true)] static extern bool QueryInformationJobObject(IntPtr job, int kind, out Accounting info, uint size, IntPtr returned);
    [DllImport("kernel32.dll", SetLastError = true)] static extern bool AssignProcessToJobObject(IntPtr job, IntPtr process);
    [DllImport("kernel32.dll", SetLastError = true)] static extern bool TerminateJobObject(IntPtr job, uint code);
    [DllImport("kernel32.dll", SetLastError = true)] static extern bool CreatePipe(out IntPtr read, out IntPtr write, ref SecurityAttributes attributes, uint size);
    [DllImport("kernel32.dll", SetLastError = true)] static extern bool SetHandleInformation(IntPtr handle, uint mask, uint flags);
    [DllImport("kernel32.dll", CharSet = CharSet.Unicode, SetLastError = true)] static extern IntPtr CreateFileW(string name, uint access, uint share, ref SecurityAttributes attributes, uint creation, uint flags, IntPtr template);
    [DllImport("kernel32.dll", CharSet = CharSet.Unicode, SetLastError = true)] static extern bool CreateProcessW(string application, StringBuilder command, IntPtr processAttributes, IntPtr threadAttributes, bool inherit, uint flags, IntPtr environment, string directory, ref StartupInfo startup, out ProcessInformation process);
    [DllImport("kernel32.dll", SetLastError = true)] static extern uint ResumeThread(IntPtr thread);
    [DllImport("kernel32.dll", SetLastError = true)] static extern bool TerminateProcess(IntPtr process, uint code);
    [DllImport("kernel32.dll", SetLastError = true)] static extern uint WaitForSingleObject(IntPtr handle, uint milliseconds);
    [DllImport("kernel32.dll", SetLastError = true)] static extern bool GetExitCodeProcess(IntPtr process, out uint code);
    [DllImport("kernel32.dll")] static extern bool CloseHandle(IntPtr handle);

    static readonly object outputLock = new object();
    static void Emit(string json) { lock (outputLock) { Console.Out.WriteLine(json); Console.Out.Flush(); } }
    static void Require(bool ok) { if (!ok) throw new Win32Exception(Marshal.GetLastWin32Error()); }
    static void Close(ref IntPtr handle) { if (handle != IntPtr.Zero && handle != new IntPtr(-1)) CloseHandle(handle); handle = IntPtr.Zero; }
    static uint Active(IntPtr job) { Accounting info; Require(QueryInformationJobObject(job, 1, out info, (uint)Marshal.SizeOf(typeof(Accounting)), IntPtr.Zero)); return info.active; }
    static bool WaitEmpty(IntPtr job) {
        DateTime deadline = DateTime.UtcNow.AddSeconds(8);
        do { if (Active(job) == 0) return true; Thread.Sleep(20); } while (DateTime.UtcNow < deadline);
        return Active(job) == 0;
    }
    static string Quote(string value) {
        StringBuilder result = new StringBuilder("\""); int slashes = 0;
        foreach (char c in value) {
            if (c == '\\') { slashes++; continue; }
            if (c == '"') { result.Append('\\', slashes * 2 + 1); result.Append(c); }
            else { result.Append('\\', slashes); result.Append(c); }
            slashes = 0;
        }
        return result.Append('\\', slashes * 2).Append('"').ToString();
    }
    static Thread Reader(IntPtr pipe, string kind) {
        Thread thread = new Thread(() => {
            try {
                using (FileStream stream = new FileStream(new SafeFileHandle(pipe, true), FileAccess.Read, 4096, false)) {
                    byte[] buffer = new byte[4096]; int count;
                    while ((count = stream.Read(buffer, 0, buffer.Length)) > 0)
                        Emit("{\"type\":\"" + kind + "\",\"data\":\"" + Convert.ToBase64String(buffer, 0, count) + "\"}");
                }
            } catch { /* Cleanup status remains separate from optional output. */ }
        });
        thread.IsBackground = true; thread.Start(); return thread;
    }
    public static void Run(string executable, string[] args) {
        IntPtr job = IntPtr.Zero, outputRead = IntPtr.Zero, outputWrite = IntPtr.Zero;
        IntPtr errorRead = IntPtr.Zero, errorWrite = IntPtr.Zero, input = IntPtr.Zero;
        ProcessInformation process = new ProcessInformation(); bool assigned = false, finished = false;
        object jobLock = new object();
        try {
            job = CreateJobObjectW(IntPtr.Zero, null); Require(job != IntPtr.Zero);
            ExtendedLimit limits = new ExtendedLimit(); limits.basic.flags = 0x2000; // KILL_ON_JOB_CLOSE; no breakaway.
            Require(SetInformationJobObject(job, 9, ref limits, (uint)Marshal.SizeOf(typeof(ExtendedLimit))));
            SecurityAttributes attributes = new SecurityAttributes(); attributes.length = Marshal.SizeOf(typeof(SecurityAttributes)); attributes.inherit = 1;
            Require(CreatePipe(out outputRead, out outputWrite, ref attributes, 0)); Require(SetHandleInformation(outputRead, 1, 0));
            Require(CreatePipe(out errorRead, out errorWrite, ref attributes, 0)); Require(SetHandleInformation(errorRead, 1, 0));
            input = CreateFileW("NUL", 0x80000000, 3, ref attributes, 3, 0, IntPtr.Zero); Require(input != new IntPtr(-1));
            StartupInfo startup = new StartupInfo(); startup.cb = Marshal.SizeOf(typeof(StartupInfo)); startup.flags = 0x100;
            startup.stdin = input; startup.stdout = outputWrite; startup.stderr = errorWrite;
            StringBuilder command = new StringBuilder(Quote(executable));
            foreach (string arg in args ?? new string[0]) command.Append(' ').Append(Quote(arg));
            Require(CreateProcessW(executable, command, IntPtr.Zero, IntPtr.Zero, true, 0x08000404, IntPtr.Zero, null, ref startup, out process));
            Require(AssignProcessToJobObject(job, process.process)); assigned = true;
            Require(ResumeThread(process.thread) != UInt32.MaxValue);
            Close(ref outputWrite); Close(ref errorWrite); Close(ref input);
            Thread output = Reader(outputRead, "stdout"); outputRead = IntPtr.Zero;
            Thread error = Reader(errorRead, "stderr"); errorRead = IntPtr.Zero;
            Thread control = new Thread(() => {
                // EOF means the owning app disappeared. Never leave its job running.
                try { Console.In.ReadLine(); } catch { }
                lock (jobLock) { if (!finished) TerminateJobObject(job, 1); }
            });
            control.IsBackground = true; control.Start();
            Require(WaitForSingleObject(process.process, UInt32.MaxValue) == 0);
            uint exitCode; Require(GetExitCodeProcess(process.process, out exitCode));
            if (Active(job) != 0) Require(TerminateJobObject(job, 1));
            if (!WaitEmpty(job)) throw new IOException("Job cleanup not confirmed");
            output.Join(2000); error.Join(2000);
            lock (jobLock) { finished = true; }
            Emit("{\"type\":\"exit\",\"exitCode\":" + exitCode + ",\"cleanupConfirmed\":true}");
        } catch {
            bool cleaned = false;
            try {
                if (assigned) { TerminateJobObject(job, 1); cleaned = WaitEmpty(job); }
                else if (process.process != IntPtr.Zero) { TerminateProcess(process.process, 1); cleaned = WaitForSingleObject(process.process, 8000) == 0; }
                else cleaned = true;
            } catch { }
            lock (jobLock) { finished = true; }
            Emit("{\"type\":\"error\",\"code\":\"" + (cleaned ? "spawn_failed" : "cleanup_failed") + "\",\"cleanupConfirmed\":" + (cleaned ? "true" : "false") + "}");
        } finally {
            lock (jobLock) { finished = true; Close(ref job); }
            Close(ref process.thread); Close(ref process.process);
            Close(ref outputRead); Close(ref outputWrite); Close(ref errorRead); Close(ref errorWrite); Close(ref input);
        }
    }
}
