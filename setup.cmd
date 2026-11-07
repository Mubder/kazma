@echo off
rem Kazma setup for Windows, from PowerShell or Command Prompt: .\setup.cmd
rem
rem A fresh Windows refuses to run PowerShell script files ("running scripts
rem is disabled on this system": the execution policy is Restricted until
rem someone changes it), so .\setup.ps1 cannot start there. This file is not
rem a PowerShell script and the policy does not apply to it: it starts
rem Windows PowerShell with the policy bypassed for that one process. The
rem machine's policy is left as it is, and a policy set by Group Policy still
rem wins. Arguments pass through:  .\setup.cmd -Mode full -Yes
rem tests/test_windows_setup_command.py runs it under a Restricted policy.
setlocal
cd /d "%~dp0"
"%SystemRoot%\System32\WindowsPowerShell\v1.0\powershell.exe" -NoProfile -ExecutionPolicy Bypass -File "%~dp0setup.ps1" %*
exit /b %ERRORLEVEL%
