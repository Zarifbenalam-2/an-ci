# Re-runnable Tailscale setup for the live Android device (see TAILSCALE.md).
# Safe to run any time, any number of times: every step skips itself if done.

$ErrorActionPreference = 'Continue'
$log = "$env:TEMP\tailscale-setup.log"
"=== tailscale-setup $(Get-Date) ===" | Out-File $log

function IsAdmin {
  ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()
  ).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
}

# ---- phase 1: elevated (install + restore-point suppression) ---------------
if (-not (IsAdmin)) {
  "elevating for the install phase (one UAC prompt)..." | Write-Host
  Start-Process powershell -Verb RunAs -Wait -ArgumentList `
    @('-NoProfile','-ExecutionPolicy','Bypass','-File', $PSCommandPath, '-install')
} else {
  $drive = if (Test-Path 'E:\') { 'E:' } else { 'C:' }
  $exe = "$drive\Tailscale\tailscale.exe"
  if (-not (Test-Path $exe)) { $exe = "$env:ProgramFiles\Tailscale\tailscale.exe" }

  # no new system restore points; reversible, see TAILSCALE.md
  reg add "HKLM\SOFTWARE\Microsoft\Windows NT\CurrentVersion\SystemRestore" `
    /v SystemRestorePointCreationFrequency /t REG_DWORD /d 2147483647 /f 2>&1 |
    Out-File $log -Append

  if (-not (Test-Path $exe)) {
    $exe = "$drive\Tailscale\tailscale.exe"
    $setup = "$env:TEMP\tailscale-setup.exe"
    Invoke-WebRequest 'https://pkgs.tailscale.com/stable/tailscale-setup-full-latest.exe' `
      -OutFile $setup -UseBasicParsing
    $p = Start-Process $setup -ArgumentList '/quiet',"INSTALLDIR=`"$drive\Tailscale`"" `
      -Wait -PassThru
    "installer exit: $($p.ExitCode)" | Out-File $log -Append
  }
  & $exe version 2>&1 | Select-Object -First 1 | Out-File $log -Append
}

# ---- phase 2: non-elevated (login + status) --------------------------------
$exe = "$env:ProgramFiles\Tailscale\tailscale.exe"
if (-not (Test-Path $exe)) { $exe = 'E:\Tailscale\tailscale.exe' }

$st = (& $exe status 2>&1) -join "`n"
Write-Host $st
if ($st -match 'Logged out|NeedsLogin') {
  Write-Host "not logged in yet -- opening the login page in your browser"
  & $exe login
  Write-Host ""
  Write-Host "After signing in, run this script again, or check:"
  & $exe status
}
Write-Host ""
Write-Host "next: make an auth key at https://login.tailscale.com/admin/settings/keys"
Write-Host "(tick Reusable + Ephemeral nodes) and add it as the TS_AUTHKEY secret at"
Write-Host "https://github.com/Zarifbenalam-2/an-ci/settings/secrets/actions"
