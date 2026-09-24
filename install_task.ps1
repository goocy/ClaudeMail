<#
    Sets up ClaudeMail on this Windows machine:
      1. registers the Claude Code hook (SessionStart + UserPromptSubmit) in ~/.claude/settings.json,
         which is what actually hands arriving mail to a Claude session;
      2. registers the Scheduled Task 'Claude Mail Watcher', which starts `mail.py watch` at logon,
         hidden, and keeps it up. The watcher only raises a desktop notification per new message.

    The task must run in the interactive session, because a toast from any other session is invisible.

    Usage:   powershell -ExecutionPolicy Bypass -File .\install_task.ps1
    Remove:  powershell -ExecutionPolicy Bypass -File .\uninstall_task.ps1
    Run now: Start-ScheduledTask -TaskName 'Claude Mail Watcher'
#>

$ErrorActionPreference = "Stop"
$TaskName   = "Claude Mail Watcher"
$ProjectDir = $PSScriptRoot
$Script     = Join-Path $ProjectDir "mail.py"
$User       = "$env:USERDOMAIN\$env:USERNAME"

$python  = (Get-Command python -ErrorAction Stop).Source
$pythonw = Join-Path (Split-Path $python) "pythonw.exe"
if (-not (Test-Path $pythonw)) { $pythonw = $python }   # fallback (will show a console)

# Fails loudly if this hostname is not one of the mailbox machines in config.json.
& $python -B $Script whoami
if ($LASTEXITCODE -ne 0) { throw "This machine is not in config.json (machines / hostnames)." }

& $python -B $Script install-hook
if ($LASTEXITCODE -ne 0) { throw "install-hook failed" }

# -B: no __pycache__ in the synced tree.
$action = New-ScheduledTaskAction -Execute $pythonw -Argument "-B `"$Script`" watch" -WorkingDirectory $ProjectDir
$trigger = New-ScheduledTaskTrigger -AtLogOn -User $User
$principal = New-ScheduledTaskPrincipal -UserId $User -LogonType Interactive -RunLevel Limited
$settings = New-ScheduledTaskSettingsSet `
    -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -DontStopOnIdleEnd `
    -StartWhenAvailable -ExecutionTimeLimit ([TimeSpan]::Zero) `
    -RestartCount 999 -RestartInterval (New-TimeSpan -Minutes 1) `
    -MultipleInstances IgnoreNew

Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger `
    -Principal $principal -Settings $settings -Force | Out-Null

Write-Host ""
Write-Host "Registered scheduled task '$TaskName' (at logon, continuous)." -ForegroundColor Green
Write-Host "Start now:  Start-ScheduledTask -TaskName '$TaskName'"
Write-Host "Logs:       $ProjectDir\logs\claudemail.<machine>.<yyMMdd>.log"
Write-Host "Restart any open Claude sessions so they pick up the hook."
