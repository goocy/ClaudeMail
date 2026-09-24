# Removes the 'Claude Mail Watcher' task and the ClaudeMail hook from ~/.claude/settings.json.
# The mailbox folders and their messages are left alone.
$ErrorActionPreference = "Stop"
$TaskName = "Claude Mail Watcher"

if (Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue) {
    Stop-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
    Write-Host "Removed scheduled task '$TaskName'."
} else {
    Write-Host "No scheduled task '$TaskName'."
}

$python = (Get-Command python -ErrorAction Stop).Source
& $python -B (Join-Path $PSScriptRoot "mail.py") uninstall-hook
