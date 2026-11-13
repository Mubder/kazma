// KazmaEngineShutdown: stops Kazma's container engine cleanly when Windows shuts
// down or restarts (kazma_core/container_engine_setup.py; AGENTS.md section 63).
//
// What Windows does, measured on the drill VM (2026-11-13):
// - It gives a service time at a shutdown only through the preshutdown notice,
//   and neither WslService nor vmcompute takes it (both IGNORES_SHUTDOWN): with
//   nobody signed in, WSL's VM was ended like a power cut, about 2 s in, and a
//   container's last writes came back as NUL bytes.
// - Task Scheduler ends every running task, and what it started, at the very
//   start of a shutdown (event 111, 60 ms in), before this service hears of
//   it; and starts no new one (ERROR_SHUTDOWN_IN_PROGRESS). WSL refuses
//   LocalSystem. So neither the KazmaEngine task nor this service can start
//   anything in the distro once the shutdown has begun.
//
// So the channel is opened before. This service serves two named pipes;
// keep-engine.ps1 (the KazmaEngine task, as the engine's account) starts the
// distro's holder through WMI -- outside Task Scheduler's reach -- as
//   wsl.exe ... kazma-engine-hold piped  <\\.\pipe\KazmaEngineHold-in  >\\.\pipe\KazmaEngineHold-out
// At the preshutdown notice this service writes "stop" to the holder (the
// distro stops Docker the way a daemon shutdown does and writes its disks),
// reads its answer, writes "poweroff", and holds Windows meanwhile, within the
// budget. A holder that ends (an update, a restart of this service) is started
// again by keep-engine.ps1, which this service accepts in its place.
//
// Control 128 (`sc.exe control KazmaEngineShutdown 128`, administrators only)
// makes the same stop without a shutdown: a drill. keep-engine.ps1 then starts
// the engine again, as after any end of the distro.
//
// Built at setup with the .NET Framework's own csc.exe (part of every Windows 10
// and 11); runs as LocalSystem from a folder only administrators can write;
// says what it did in the Application event log (source KazmaEngineShutdown).
//
//   kazma-engine-shutdown.exe --budget <seconds>

using System;
using System.Collections.Concurrent;
using System.Diagnostics;
using System.IO;
using System.IO.Pipes;
using System.Runtime.InteropServices;
using System.Security.AccessControl;
using System.Security.Principal;
using System.Text;
using System.Threading;

public static class KazmaEngineShutdown
{
    const string Name = "KazmaEngineShutdown";
    const string PipeIn = "KazmaEngineHold-in";    // this service writes; the holder's stdin
    const string PipeOut = "KazmaEngineHold-out";  // the holder's stdout; this service reads
    const int DRILL_CONTROL = 128;
    const int SERVICE_WIN32_OWN_PROCESS = 0x10;
    const int SERVICE_STOPPED = 1, SERVICE_START_PENDING = 2, SERVICE_STOP_PENDING = 3, SERVICE_RUNNING = 4;
    const int SERVICE_ACCEPT_STOP = 0x1, SERVICE_ACCEPT_PRESHUTDOWN = 0x100;
    const int SERVICE_CONTROL_STOP = 1, SERVICE_CONTROL_INTERROGATE = 4, SERVICE_CONTROL_PRESHUTDOWN = 0xF;
    const int SERVICE_CONFIG_PRESHUTDOWN_INFO = 7;
    const int NO_ERROR = 0, ERROR_CALL_NOT_IMPLEMENTED = 120;

    [StructLayout(LayoutKind.Sequential)]
    struct SERVICE_STATUS
    {
        public int dwServiceType, dwCurrentState, dwControlsAccepted, dwWin32ExitCode,
            dwServiceSpecificExitCode, dwCheckPoint, dwWaitHint;
    }

    [StructLayout(LayoutKind.Sequential, CharSet = CharSet.Unicode)]
    struct SERVICE_TABLE_ENTRY
    {
        public string lpServiceName;
        public ServiceMainFn lpServiceProc;
    }

    [StructLayout(LayoutKind.Sequential)]
    struct SERVICE_PRESHUTDOWN_INFO
    {
        public int dwPreshutdownTimeout;
    }

    [UnmanagedFunctionPointer(CallingConvention.StdCall)]
    delegate void ServiceMainFn(int argc, IntPtr argv);

    [UnmanagedFunctionPointer(CallingConvention.StdCall)]
    delegate int HandlerEx(int control, int eventType, IntPtr eventData, IntPtr context);

    [DllImport("advapi32.dll", CharSet = CharSet.Unicode, SetLastError = true)]
    static extern bool StartServiceCtrlDispatcher(SERVICE_TABLE_ENTRY[] table);

    [DllImport("advapi32.dll", CharSet = CharSet.Unicode, SetLastError = true)]
    static extern IntPtr RegisterServiceCtrlHandlerEx(string name, HandlerEx handler, IntPtr context);

    [DllImport("advapi32.dll", SetLastError = true)]
    static extern bool SetServiceStatus(IntPtr handle, ref SERVICE_STATUS status);

    [DllImport("advapi32.dll", CharSet = CharSet.Unicode, SetLastError = true)]
    static extern IntPtr OpenSCManager(string machine, string database, int access);

    [DllImport("advapi32.dll", CharSet = CharSet.Unicode, SetLastError = true)]
    static extern IntPtr OpenService(IntPtr manager, string name, int access);

    [DllImport("advapi32.dll", SetLastError = true)]
    static extern bool ChangeServiceConfig2(IntPtr service, int level, ref SERVICE_PRESHUTDOWN_INFO info);

    [DllImport("advapi32.dll", SetLastError = true)]
    static extern bool CloseServiceHandle(IntPtr handle);

    static int budgetSeconds = 120;

    static IntPtr statusHandle;
    static SERVICE_STATUS status;
    static readonly object statusLock = new object();
    static readonly ManualResetEvent finished = new ManualResetEvent(false);
    static int stopping;
    // The holder now connected: the pipe to write to, its lines, and its end.
    static readonly object holderLock = new object();
    static NamedPipeServerStream holderIn;
    static BlockingCollection<string> holderLines;
    static ManualResetEvent holderGone;
    // Held so the garbage collector never frees what Windows calls back.
    static ServiceMainFn mainFn;
    static HandlerEx handlerFn;

    public static int Main(string[] args)
    {
        for (int i = 0; i < args.Length; i++)
        {
            string next = i + 1 < args.Length ? args[i + 1] : "";
            if (args[i] == "--budget") { budgetSeconds = Math.Max(10, int.Parse(next)); i++; }
        }
        mainFn = ServiceMain;
        handlerFn = Handler;
        var table = new SERVICE_TABLE_ENTRY[] {
            new SERVICE_TABLE_ENTRY { lpServiceName = Name, lpServiceProc = mainFn },
            new SERVICE_TABLE_ENTRY { lpServiceName = null, lpServiceProc = null },
        };
        if (!StartServiceCtrlDispatcher(table))
        {
            Console.Error.WriteLine("Windows did not start this as a service (error " + Marshal.GetLastWin32Error()
                                    + "): it is the KazmaEngineShutdown service's program.");
            return 1;
        }
        return 0;
    }

    static void ServiceMain(int argc, IntPtr argv)
    {
        statusHandle = RegisterServiceCtrlHandlerEx(Name, handlerFn, IntPtr.Zero);
        if (statusHandle == IntPtr.Zero)
        {
            return;
        }
        Report(SERVICE_START_PENDING, 0, 5000);
        SetOwnPreshutdownTimeout();
        new Thread(AcceptHolders) { IsBackground = true }.Start();
        Report(SERVICE_RUNNING, 0, 0);
        Say("ready: at a shutdown, Kazma's engine gets up to " + budgetSeconds + " s to stop");
        finished.WaitOne();
        Report(SERVICE_STOPPED, 0, 0);
    }

    static int Handler(int control, int eventType, IntPtr eventData, IntPtr context)
    {
        switch (control)
        {
            case SERVICE_CONTROL_INTERROGATE:
                return NO_ERROR;
            case SERVICE_CONTROL_STOP:
                // A person stopping the service does not stop the engine.
                Say("stopped (not a shutdown: the engine keeps running)");
                finished.Set();
                return NO_ERROR;
            case SERVICE_CONTROL_PRESHUTDOWN:
                if (Interlocked.Exchange(ref stopping, 1) == 0)
                {
                    new Thread(() => {
                        try { StopEngine("Windows is shutting down", true); } finally { finished.Set(); }
                    }) { IsBackground = true }.Start();
                }
                return NO_ERROR;
            case DRILL_CONTROL:
                if (Interlocked.CompareExchange(ref stopping, 0, 0) == 0)
                {
                    new Thread(() => StopEngine("a drill (control 128)", false)) { IsBackground = true }.Start();
                }
                return NO_ERROR;
            default:
                return ERROR_CALL_NOT_IMPLEMENTED;
        }
    }

    // The engine's stop takes as long as its slowest container; Windows waits for
    // a preshutdown service only as long as the service's own timeout says.
    static void SetOwnPreshutdownTimeout()
    {
        IntPtr scm = OpenSCManager(null, null, 0x0001 /* SC_MANAGER_CONNECT */);
        if (scm == IntPtr.Zero) { Say("the service manager could not be opened: " + Marshal.GetLastWin32Error()); return; }
        IntPtr svc = OpenService(scm, Name, 0x0002 /* SERVICE_CHANGE_CONFIG */);
        if (svc != IntPtr.Zero)
        {
            var info = new SERVICE_PRESHUTDOWN_INFO { dwPreshutdownTimeout = (budgetSeconds + 30) * 1000 };
            if (!ChangeServiceConfig2(svc, SERVICE_CONFIG_PRESHUTDOWN_INFO, ref info))
            {
                Say("the preshutdown timeout could not be set: " + Marshal.GetLastWin32Error());
            }
            CloseServiceHandle(svc);
        }
        CloseServiceHandle(scm);
    }

    static void Report(int state, int checkPoint, int waitHint)
    {
        lock (statusLock)
        {
            status.dwServiceType = SERVICE_WIN32_OWN_PROCESS;
            status.dwCurrentState = state;
            status.dwControlsAccepted = state == SERVICE_RUNNING ? SERVICE_ACCEPT_STOP | SERVICE_ACCEPT_PRESHUTDOWN : 0;
            status.dwWin32ExitCode = NO_ERROR;
            status.dwCheckPoint = checkPoint;
            status.dwWaitHint = waitHint;
            if (statusHandle != IntPtr.Zero)
            {
                SetServiceStatus(statusHandle, ref status);
            }
        }
    }

    // Signed-in accounts may open the pipes on this machine; nobody over the network.
    static PipeSecurity Security()
    {
        var security = new PipeSecurity();
        security.AddAccessRule(new PipeAccessRule(new SecurityIdentifier(WellKnownSidType.NetworkSid, null),
            PipeAccessRights.FullControl, AccessControlType.Deny));
        security.AddAccessRule(new PipeAccessRule(new SecurityIdentifier(WellKnownSidType.LocalSystemSid, null),
            PipeAccessRights.FullControl, AccessControlType.Allow));
        security.AddAccessRule(new PipeAccessRule(new SecurityIdentifier(WellKnownSidType.BuiltinAdministratorsSid, null),
            PipeAccessRights.FullControl, AccessControlType.Allow));
        security.AddAccessRule(new PipeAccessRule(new SecurityIdentifier(WellKnownSidType.AuthenticatedUserSid, null),
            PipeAccessRights.ReadWrite, AccessControlType.Allow));
        return security;
    }

    // One holder at a time: both pipes, then its lines until it goes away.
    static void AcceptHolders()
    {
        while (!finished.WaitOne(0))
        {
            NamedPipeServerStream inPipe = null, outPipe = null;
            try
            {
                inPipe = new NamedPipeServerStream(PipeIn, PipeDirection.Out, 1, PipeTransmissionMode.Byte,
                    PipeOptions.Asynchronous, 0, 0, Security());
                outPipe = new NamedPipeServerStream(PipeOut, PipeDirection.In, 1, PipeTransmissionMode.Byte,
                    PipeOptions.Asynchronous, 0, 0, Security());
                var a = inPipe.BeginWaitForConnection(null, null);
                var b = outPipe.BeginWaitForConnection(null, null);
                WaitHandle.WaitAny(new[] { a.AsyncWaitHandle, b.AsyncWaitHandle });
                // The holder opens both at once; one alone is a holder that failed.
                if (!a.AsyncWaitHandle.WaitOne(15000) || !b.AsyncWaitHandle.WaitOne(15000))
                {
                    throw new IOException("a holder opened one pipe only");
                }
                inPipe.EndWaitForConnection(a);
                outPipe.EndWaitForConnection(b);
                var lines = new BlockingCollection<string>();
                var gone = new ManualResetEvent(false);
                lock (holderLock)
                {
                    holderIn = inPipe;
                    holderLines = lines;
                    holderGone = gone;
                }
                Say("Kazma's engine is held: it can be stopped at a shutdown");
                using (var reader = new StreamReader(outPipe, new UTF8Encoding(false)))
                {
                    string line;
                    while ((line = reader.ReadLine()) != null)
                    {
                        lines.Add(line);
                    }
                }
                lock (holderLock)
                {
                    if (holderGone == gone) { holderIn = null; }
                }
                gone.Set();
                Say("the engine's holder went away");
            }
            catch (Exception e)
            {
                if (!finished.WaitOne(0)) { Say("waiting for the engine's holder: " + e.Message); }
                Thread.Sleep(2000);
            }
            finally
            {
                try { if (inPipe != null) inPipe.Dispose(); } catch (Exception) { }
                try { if (outPipe != null) outPipe.Dispose(); } catch (Exception) { }
            }
        }
    }

    // Ask the holder to stop the engine, and wait until it says it did.
    static void StopEngine(string why, bool forWindows)
    {
        DateTime asked = DateTime.UtcNow;
        NamedPipeServerStream pipe;
        BlockingCollection<string> lines;
        ManualResetEvent gone;
        lock (holderLock)
        {
            pipe = holderIn;
            lines = holderLines;
            gone = holderGone;
        }
        if (pipe == null)
        {
            Say(why + ": no holder is connected (is keep-engine.ps1 running?): nothing to stop");
            return;
        }
        Say(why + ": stopping Kazma's engine");
        int checkPoint = 0;
        var ticking = new Timer(_ => { if (forWindows) Report(SERVICE_STOP_PENDING, Interlocked.Increment(ref checkPoint), 10000); },
                                null, 0, 2000);
        try
        {
            string line;
            while (lines.TryTake(out line)) { }  // nothing said before this stop counts
            Send(pipe, "stop\n");
            string answer;
            if (lines.TryTake(out answer, budgetSeconds * 1000))
            {
                Say("the engine is stopped after " + (DateTime.UtcNow - asked).TotalSeconds.ToString("0.0") + " s: " + answer);
            }
            else
            {
                Say("the engine did not say it stopped within " + budgetSeconds + " s" + (forWindows ? "; Windows goes on" : ""));
                return;
            }
            Send(pipe, "poweroff\n");
            Say(gone.WaitOne(20000) ? "the engine's distro is powered off" : "the engine's distro did not power off within 20 s");
        }
        catch (Exception e)
        {
            Say("the stop failed: " + e.Message);
        }
        finally
        {
            ticking.Dispose();
        }
    }

    static void Send(Stream pipe, string text)
    {
        byte[] bytes = Encoding.ASCII.GetBytes(text);
        pipe.Write(bytes, 0, bytes.Length);
        pipe.Flush();
    }

    static void Say(string message)
    {
        try
        {
            // LocalSystem creates the source on first use.
            EventLog.WriteEntry(Name, message, EventLogEntryType.Information, 1);
        }
        catch (Exception)
        {
        }
    }
}
