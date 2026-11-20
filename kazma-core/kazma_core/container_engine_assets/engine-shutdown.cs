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
// Kazma's own server is stopped first, the same way (2026-11-20). At a
// restart the guard is ended with the other tasks and nothing stopped the
// server: it heard of the shutdown only at the very end, after the database
// service had stopped, and was ended mid-stop ("the last run did not shut down
// cleanly"). Each Kazma server connects to a third pipe, KazmaShutdownNotice
// (kazma_core/shutdown_notice.py); at the preshutdown notice this service
// writes "stop" to every one and waits, within its own budget, until each has
// closed its end -- its process has ended -- and only then stops the engine.
// Windows stops the database service after every preshutdown service is done.
//
// Control 128 (`sc.exe control KazmaEngineShutdown 128`, administrators only)
// makes the engine's stop without a shutdown: a drill. keep-engine.ps1 then
// starts the engine again, as after any end of the distro. Control 129 makes
// Kazma's stop the same way (its guard starts the server again).
//
// Built at setup with the .NET Framework's own csc.exe (part of every Windows 10
// and 11); runs as LocalSystem from a folder only administrators can write;
// says what it did in the Application event log (source KazmaEngineShutdown).
//
//   kazma-engine-shutdown.exe --budget <seconds> --kazma-budget <seconds>
//   kazma-engine-shutdown.exe --notice-drill <seconds before the stop>   (not a service: tests)

using System;
using System.Collections.Concurrent;
using System.Collections.Generic;
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
    const int NOTICE_INSTANCES = 16;               // Kazma servers at once (installs on this machine)
    const int DRILL_CONTROL = 128;
    const int KAZMA_DRILL_CONTROL = 129;
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
    static int kazmaBudgetSeconds = 75;
    // Kazma's servers connect here (--notice-pipe names another, for a test).
    static string pipeNotice = "KazmaShutdownNotice";
    // --notice-drill: not a service; says what it did on the console.
    static bool consoleOnly;

    static IntPtr statusHandle;
    static SERVICE_STATUS status;
    static readonly object statusLock = new object();
    static readonly ManualResetEvent finished = new ManualResetEvent(false);
    static int stopping;
    static int checkPoint;
    // Kazma's servers now connected: each one's pipe and its end.
    sealed class Listener
    {
        public NamedPipeServerStream Pipe;
        public readonly ManualResetEvent Gone = new ManualResetEvent(false);
    }
    static readonly object listenersLock = new object();
    static readonly List<Listener> listeners = new List<Listener>();
    static string lastWaitMessage = "";
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
        int noticeDrill = -1;
        for (int i = 0; i < args.Length; i++)
        {
            string next = i + 1 < args.Length ? args[i + 1] : "";
            if (args[i] == "--budget") { budgetSeconds = Math.Max(10, int.Parse(next)); i++; }
            else if (args[i] == "--kazma-budget") { kazmaBudgetSeconds = Math.Max(1, int.Parse(next)); i++; }
            else if (args[i] == "--notice-pipe") { pipeNotice = next; i++; }
            else if (args[i] == "--notice-drill") { noticeDrill = Math.Max(0, int.Parse(next)); i++; }
        }
        if (noticeDrill >= 0)
        {
            // A test of Kazma's stop with no service and no shutdown: listen,
            // then make the stop a preshutdown notice makes.
            consoleOnly = true;
            new Thread(AcceptListeners) { IsBackground = true }.Start();
            Say("notice drill: listening on " + pipeNotice + " for " + noticeDrill + " s");
            Thread.Sleep(noticeDrill * 1000);
            bool stopped = StopKazma("a notice drill");
            finished.Set();
            return stopped ? 0 : 2;
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
        new Thread(AcceptListeners) { IsBackground = true }.Start();
        Report(SERVICE_RUNNING, 0, 0);
        Say("ready: at a shutdown, Kazma gets up to " + kazmaBudgetSeconds + " s to stop, then its engine up to "
            + budgetSeconds + " s");
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
                        // Kazma first, while the database and the engine still
                        // run; then the engine. Windows waits for both.
                        var ticking = Ticking(true);
                        try
                        {
                            StopKazma("Windows is shutting down");
                            StopEngine("Windows is shutting down", true);
                        }
                        finally { ticking.Dispose(); finished.Set(); }
                    }) { IsBackground = true }.Start();
                }
                return NO_ERROR;
            case DRILL_CONTROL:
                if (Interlocked.CompareExchange(ref stopping, 0, 0) == 0)
                {
                    new Thread(() => StopEngine("a drill (control 128)", false)) { IsBackground = true }.Start();
                }
                return NO_ERROR;
            case KAZMA_DRILL_CONTROL:
                if (Interlocked.CompareExchange(ref stopping, 0, 0) == 0)
                {
                    new Thread(() => StopKazma("a drill (control 129)")) { IsBackground = true }.Start();
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
            var info = new SERVICE_PRESHUTDOWN_INFO
            {
                dwPreshutdownTimeout = (kazmaBudgetSeconds + budgetSeconds + 30) * 1000
            };
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
        // The account this runs as makes the pipe's next instances (LocalSystem
        // as a service; the person running a --notice-drill).
        security.AddAccessRule(new PipeAccessRule(WindowsIdentity.GetCurrent().User,
            PipeAccessRights.FullControl, AccessControlType.Allow));
        return security;
    }

    // Kazma's servers, any number at once: each connects and says nothing; its
    // end closes when its process ends, which is how this service knows it
    // stopped.
    static void AcceptListeners()
    {
        while (!finished.WaitOne(0))
        {
            NamedPipeServerStream pipe = null;
            try
            {
                pipe = new NamedPipeServerStream(pipeNotice, PipeDirection.InOut, NOTICE_INSTANCES,
                    PipeTransmissionMode.Byte, PipeOptions.Asynchronous, 0, 0, Security());
                var waiting = pipe.BeginWaitForConnection(null, null);
                if (WaitHandle.WaitAny(new WaitHandle[] { waiting.AsyncWaitHandle, finished }) != 0)
                {
                    return;
                }
                pipe.EndWaitForConnection(waiting);
                var listener = new Listener { Pipe = pipe };
                pipe = null;  // the listener's now
                lock (listenersLock) { listeners.Add(listener); }
                new Thread(() => WatchListener(listener)) { IsBackground = true }.Start();
                lastWaitMessage = "";
            }
            catch (Exception e)
            {
                // Said once, not every two seconds (seventeen installs, say).
                if (!finished.WaitOne(0) && e.Message != lastWaitMessage)
                {
                    lastWaitMessage = e.Message;
                    Say("waiting for Kazma's servers: " + e.Message);
                }
                Thread.Sleep(2000);
            }
            finally
            {
                if (pipe != null) { try { pipe.Dispose(); } catch (Exception) { } }
            }
        }
    }

    static void WatchListener(Listener listener)
    {
        try
        {
            var buffer = new byte[64];
            while (listener.Pipe.Read(buffer, 0, buffer.Length) > 0) { }
        }
        catch (Exception)
        {
        }
        finally
        {
            lock (listenersLock) { listeners.Remove(listener); }
            listener.Gone.Set();
            try { listener.Pipe.Dispose(); } catch (Exception) { }
        }
    }

    // Tell every Kazma server to stop, and wait until each has ended. True when
    // every one did (or none listens).
    static bool StopKazma(string why)
    {
        DateTime asked = DateTime.UtcNow;
        Listener[] now;
        lock (listenersLock) { now = listeners.ToArray(); }
        if (now.Length == 0)
        {
            Say(why + ": no Kazma server is listening: nothing to stop");
            return true;
        }
        Say(why + ": stopping Kazma (" + now.Length + " server" + (now.Length == 1 ? "" : "s") + ")");
        var told = new List<Listener>();
        foreach (var listener in now)
        {
            try { Send(listener.Pipe, "stop\n"); told.Add(listener); }
            catch (Exception e) { Say("a Kazma server could not be told to stop: " + e.Message); }
        }
        DateTime until = asked.AddSeconds(kazmaBudgetSeconds);
        int left = 0;
        foreach (var listener in told)
        {
            int ms = (int)Math.Max(0, (until - DateTime.UtcNow).TotalMilliseconds);
            if (!listener.Gone.WaitOne(ms)) { left++; }
        }
        string took = (DateTime.UtcNow - asked).TotalSeconds.ToString("0.0");
        if (left == 0 && told.Count == now.Length)
        {
            Say("Kazma stopped after " + took + " s");
            return true;
        }
        Say((left + now.Length - told.Count) + " of " + now.Length + " Kazma server(s) did not stop within "
            + kazmaBudgetSeconds + " s; going on");
        return false;
    }

    static Timer Ticking(bool forWindows)
    {
        return new Timer(_ => {
            if (forWindows) Report(SERVICE_STOP_PENDING, Interlocked.Increment(ref checkPoint), 10000);
        }, null, 0, 2000);
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
        // Windows is told the stop goes on by the caller's Ticking.
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
    }

    static void Send(Stream pipe, string text)
    {
        byte[] bytes = Encoding.ASCII.GetBytes(text);
        pipe.Write(bytes, 0, bytes.Length);
        pipe.Flush();
    }

    static void Say(string message)
    {
        if (consoleOnly)
        {
            Console.WriteLine(message);
            Console.Out.Flush();
            return;
        }
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
