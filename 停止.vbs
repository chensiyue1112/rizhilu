Set WshShell = CreateObject("WScript.Shell")
WshShell.Run "taskkill /F /IM python.exe", 0, True
MsgBox "Server stopped.", 64, "RiZhiLu"
