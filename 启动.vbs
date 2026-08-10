Set WshShell = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")
dir = fso.GetParentFolderName(WScript.ScriptFullName)

cmd = "cmd /c ""cd /d " & dir & " && start http://localhost:5000 && python app.py"""
WshShell.Run cmd, 0, False
