' Starts the Claude Progress Bar window with no console flash. hook.py opens it through Explorer (via a shortcut)
' so the window lives outside Claude Code's process container.
' usage: wscript launch_window.vbs "<data folder>"
Set sh = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")
dir = fso.GetParentFolderName(WScript.ScriptFullName)
dataArg = ""
If WScript.Arguments.Count > 0 Then dataArg = " -DataDir """ & WScript.Arguments(0) & """"
' PowerShell 7 (pwsh) if installed, else the built-in Windows PowerShell 5.1
exe = "powershell"
On Error Resume Next
sh.Run "pwsh -NoProfile -Command exit", 0, True
If Err.Number = 0 Then exe = "pwsh"
On Error GoTo 0
sh.Run exe & " -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File """ & dir & "\progress_window.ps1""" & dataArg, 0, False
