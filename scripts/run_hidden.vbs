' Run a PowerShell script with no window at all (for Task Scheduler).
' powershell.exe -WindowStyle Hidden still flashes a console window for a moment;
' wscript + Run(..., 0) never creates one.
' Usage: wscript.exe //B //Nologo run_hidden.vbs "C:\path\to\script.ps1"
' Keep this file ASCII-only: wscript reads .vbs files in the ANSI code page.
Option Explicit
Dim sh, ps1, cmd, rc
If WScript.Arguments.Count < 1 Then WScript.Quit 2
ps1 = WScript.Arguments(0)
Set sh = CreateObject("WScript.Shell")
cmd = "powershell.exe -NoProfile -NonInteractive -ExecutionPolicy Bypass -File """ & ps1 & """"
rc = sh.Run(cmd, 0, True)
WScript.Quit rc
