<#
    Sets up ClaudeMail on this Windows machine:
      1. registers the Claude Code hooks in ~/.claude/settings.json: `mail.py hook` on SessionStart + UserPromptSubmit
         and `mail.py wait` on Stop (asyncRewake). These commands never change, so code updates need no re-run;
      2. registers the Scheduled Task 'Claude Mail Watcher' (at logon, hidden, continuous), which runs `mail.py watch`,
         the dispatcher's keeper; the dispatcher restarts itself whenever mail.py changes;
      3. starts (or restarts) that task right away.

    The task must run in the interactive session, because a toast from any other session is invisible.

    Usage:   powershell -ExecutionPolicy Bypass -File .\install_task.ps1
    Remove:  powershell -ExecutionPolicy Bypass -File .\uninstall_task.ps1
#>

$ErrorActionPreference = "Stop"
$TaskName   = "Claude Mail Watcher"
$ProjectDir = $PSScriptRoot
$Script     = Join-Path $ProjectDir "mail.py"
$User       = "$env:USERDOMAIN\$env:USERNAME"

$python  = (Get-Command python -ErrorAction Stop).Source
$pythonw = Join-Path (Split-Path $python) "pythonw.exe"
if (-not (Test-Path $pythonw)) { $pythonw = $python }   # fallback (will show a console)

# Fails loudly if this hostname is not one of the mailbox machines in config/defaults.json.
& $python -B $Script whoami
if ($LASTEXITCODE -ne 0) { throw "This machine is not in config/defaults.json (machines / hostnames)." }

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

Stop-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
Start-Sleep -Seconds 2
Start-ScheduledTask -TaskName $TaskName

Write-Host ""
Write-Host "Registered and started scheduled task '$TaskName' (at logon, continuous)." -ForegroundColor Green
Write-Host "Logs: $env:USERPROFILE\.claude\claudemail\logs\claudemail.<machine>.<yyMMdd>.log (machine-local)"
Write-Host "Next: open one desktop session in ResilioSync\Services\ClaudeMail\mail\<Machine>\post office and pin it (the Post office)."
