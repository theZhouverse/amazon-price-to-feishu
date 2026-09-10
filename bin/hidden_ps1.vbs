Option Explicit

' GUI-subsystem launcher for Windows Task Scheduler.  Calling PowerShell from
' a .bat file can briefly create a visible cmd.exe window; wscript.exe does
' not create a console and waits for the real exit code.
Dim fso, shell, scriptPath, commandLine, exitCode, quote
Set fso = CreateObject("Scripting.FileSystemObject")
Set shell = CreateObject("WScript.Shell")
quote = Chr(34)

If WScript.Arguments.Count < 1 Then
    WScript.Quit 64
End If

scriptPath = fso.GetAbsolutePathName(WScript.Arguments(0))
If Not fso.FileExists(scriptPath) Then
    WScript.Quit 2
End If

shell.CurrentDirectory = fso.GetParentFolderName(fso.GetParentFolderName(scriptPath))
commandLine = "powershell.exe -NoLogo -NoProfile -NonInteractive -ExecutionPolicy Bypass -WindowStyle Hidden -File " _
              & quote & scriptPath & quote
Dim i
For i = 1 To WScript.Arguments.Count - 1
    commandLine = commandLine & " " & quote & WScript.Arguments(i) & quote
Next
exitCode = shell.Run(commandLine, 0, True)
WScript.Quit exitCode
