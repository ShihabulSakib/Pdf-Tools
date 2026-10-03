#Requires -Version 5.1
<#
.SYNOPSIS
    One-shot environment setup for PDF Layout Studio (Windows).

.DESCRIPTION
    Installs:
      system  : qpdf, Ghostscript (via winget / choco / scoop, elevated if needed)
      python  : .\.venv from requirements.txt

    If a system install is required and the session is not elevated, the script
    asks for confirmation and then re-launches itself elevated (the UAC prompt)
    before touching anything.

    Progress is rendered live by setup_progress.py (rich -> tqdm -> plain).

    Also creates .\bin\gs.cmd and .\bin\gs, shims for Ghostscript, which ships
    on Windows as gswin64c.exe -- merge_and_convert.sh calls plain `gs`.

.PARAMETER NoSystem
    Skip the qpdf / Ghostscript install.

.PARAMETER RecreateVenv
    Delete and rebuild .\.venv from scratch.

.PARAMETER SkipVerify
    Skip the import check and the process.py smoke test.

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File .\setup.ps1
#>
[CmdletBinding()]
param(
    [switch] $NoSystem,
    [switch] $RecreateVenv,
    [switch] $SkipVerify
)

$ErrorActionPreference = 'Stop'
$ProgressPreference    = 'SilentlyContinue'   # hides PowerShell's own progress bars

$ScriptFile = $PSCommandPath
$ScriptDir  = Split-Path -Parent $ScriptFile
$Venv       = Join-Path $ScriptDir '.venv'
$VenvPy     = Join-Path $Venv 'Scripts\python.exe'
$ReqFile    = Join-Path $ScriptDir 'requirements.txt'
$Renderer   = Join-Path $ScriptDir 'setup_progress.py'
$BinDir     = Join-Path $ScriptDir 'bin'

# ── Colour helpers (mirrors setup.sh) ───────────────────────────────────
# While the renderer is live everything goes through it as an event; writing
# straight to the host would tear the progress table apart.
function Say  { param($m = '') Write-Host $m }
function Info {
    param($m)
    if ($script:Proc) { Note 'info' $m } else { Write-Host "[INFO]  $m" -ForegroundColor Cyan }
}
function Ok {
    param($m)
    if ($script:Proc) { Note 'ok' $m } else { Write-Host "[ OK ]  $m" -ForegroundColor Green }
}
function Warn {
    param($m)
    if ($script:Proc) { Note 'warn' $m } else { Write-Host "[WARN]  $m" -ForegroundColor Yellow }
}
function Err  {
    param($m)
    if ($script:Proc) { Note 'err' $m } else { Write-Host "[FAIL] $m" -ForegroundColor Red }
}

# Die throws rather than exits so the `finally` around the main body always
# gets to shut the renderer down and draw the final table.
function Die  { param($m) Err $m; $script:Reported = $true; throw $m }

# ══════════════════════════════════════════════════════════════════════
# Progress plumbing  (mirrors the event protocol of setup.sh)
# ══════════════════════════════════════════════════════════════════════
$script:Proc      = $null
$script:RenderPy  = $null
$script:Tasks     = [ordered]@{}
$script:CurrentTask = $null

function Send-Json {
    param([hashtable] $Event)
    if ($null -eq $script:Proc) { return }
    try {
        $script:Proc.StandardInput.WriteLine(($Event | ConvertTo-Json -Compress -Depth 5))
        $script:Proc.StandardInput.Flush()
    } catch {
        $script:Proc = $null     # renderer died; keep going undecorated
    }
}

function Start-Renderer {
    param([string] $Py)
    if (-not $Py -or -not (Test-Path $Py)) { return $false }
    if (-not (Test-Path $Renderer))         { return $false }
    try {
        $psi = New-Object System.Diagnostics.ProcessStartInfo
        $psi.FileName        = $Py
        $psi.Arguments       = '"' + $Renderer + '"'
        $psi.UseShellExecute = $false
        $psi.CreateNoWindow  = $true
        $psi.RedirectStandardInput  = $true
        $psi.RedirectStandardOutput = $false   # must reach the console
        $psi.RedirectStandardError  = $false
        $script:Proc = [System.Diagnostics.Process]::Start($psi)
        $script:Proc.StandardInput.AutoFlush = $true
        $script:RenderPy = $Py
        return $true
    } catch {
        $script:Proc = $null
        return $false
    }
}

function Stop-Renderer {
    if ($null -eq $script:Proc) { return }
    try { $script:Proc.StandardInput.Close() } catch { }
    try { $script:Proc.WaitForExit(5000) | Out-Null } catch { }
    try { if (-not $script:Proc.HasExited) { $script:Proc.Kill() } } catch { }
    $script:Proc = $null
}

function Replay-Tasks {
    foreach ($id in $script:Tasks.Keys) {
        $t = $script:Tasks[$id]
        Send-Json @{ op = 'task_add'; id = $id; label = $t.Label }
        if ($t.State -eq 'pending') { continue }
        Send-Json @{ op = 'task_start'; id = $id; detail = $t.Detail }
        switch ($t.State) {
            'running' { }
            'done'    { Send-Json @{ op = 'task_done'; id = $id } }
            'failed'  { Send-Json @{ op = 'task_fail'; id = $id; detail = $t.Detail } }
            'skipped' { Send-Json @{ op = 'task_skip'; id = $id; detail = $t.Detail } }
        }
    }
}

function Add-Task {
    param([string] $Id, [string] $Label)
    $script:Tasks[$Id] = @{ Label = $Label; State = 'pending'; Detail = '' }
    Send-Json @{ op = 'task_add'; id = $Id; label = $Label }
}

function Start-Task {
    param([string] $Id, [string] $Detail = '')
    if (-not $script:Tasks.Contains($Id)) { return }
    $script:Tasks[$Id].State  = 'running'
    $script:Tasks[$Id].Detail = $Detail
    $script:CurrentTask = $Id
    Send-Json @{ op = 'task_start'; id = $Id; detail = $Detail }
}

function Write-TaskLog {
    param([string] $Id, [string] $Line)
    if ($null -eq $script:Proc) { return }
    if (-not $script:Tasks.Contains($Id)) { return }
    if ($script:Tasks[$Id].State -ne 'running') { return }
    $script:Tasks[$Id].Detail = $Line
    Send-Json @{ op = 'task_log'; id = $Id; line = $Line }
}

function End-Task {
    param([string] $Id, [string] $State, [string] $Detail = '')
    if (-not $script:Tasks.Contains($Id)) { return }
    $script:Tasks[$Id].State = $State
    if ($Detail) { $script:Tasks[$Id].Detail = $Detail }   # else keep the last log line
    $op = switch ($State) { 'done' { 'task_done' } 'failed' { 'task_fail' } 'skipped' { 'task_skip' } default { 'task_done' } }
    Send-Json @{ op = $op; id = $Id; detail = $script:Tasks[$Id].Detail }
}

function Note {
    param([string] $Level, [string] $Message)
    Send-Json @{ op = 'note'; level = $Level; msg = $Message }
}

# Run a native command under a task, streaming its output into the live display
function Invoke-Logged {
    param([string] $Id, [string] $Detail, [scriptblock] $Action)
    Start-Task $Id $Detail
    $global:LASTEXITCODE = 0
    $failed = $false
    try {
        & $Action 2>&1 | ForEach-Object {
            if ($_ -is [System.Management.Automation.ErrorRecord]) {
                Write-TaskLog $Id $_.ToString()
            } else {
                Write-TaskLog $Id ([string]$_)
            }
        }
        if ($LASTEXITCODE -ne 0) { $failed = $true }
    } catch {
        Write-TaskLog $Id $_.Exception.Message
        $failed = $true
    }
    if ($failed) { End-Task $Id 'failed' "exit $LASTEXITCODE" ; return $false }
    End-Task $Id 'done'
    return $true
}

# Host Read-Host and the renderer's output would fight over the console, so
# drop the renderer for the duration of any interactive prompt.
function Invoke-Interactive {
    param([scriptblock] $Prompt)
    Stop-Renderer
    try {
        & $Prompt
    } finally {
        if ($script:RenderPy) { Start-Renderer $script:RenderPy | Out-Null; Replay-Tasks }
    }
}

# ══════════════════════════════════════════════════════════════════════
# Platform helpers
# ══════════════════════════════════════════════════════════════════════
function Test-Admin {
    try {
        $id = [Security.Principal.WindowsIdentity]::GetCurrent()
        return (New-Object Security.Principal.WindowsPrincipal $id).
                 IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
    } catch {
        return $false      # non-Windows host, or principal unavailable
    }
}

function Get-PackageManager {
    foreach ($c in 'winget', 'choco', 'scoop') {
        if (Get-Command $c -ErrorAction SilentlyContinue) { return $c }
    }
    return $null
}

# winget/choco/scoop all write to new directories that this session cannot see.
function Update-PathFromDisk {
    $dirs = New-Object System.Collections.Generic.List[string]
    $dirs.Add('C:\Program Files\qpdf\bin')
    $dirs.Add('C:\Program Files (x86)\qpdf\bin')
    $dirs.Add("$env:LOCALAPPDATA\qpdf\bin")
    $dirs.Add('C:\Program Files\Git\bin')
    $dirs.Add('C:\Program Files\Git\usr\bin')
    foreach ($root in 'C:\Program Files\gs', 'C:\Program Files (x86)\gs') {
        if (Test-Path $root) {
            Get-ChildItem $root -Directory -ErrorAction SilentlyContinue |
                ForEach-Object { $dirs.Add((Join-Path $_.FullName 'bin')) }
        }
    }
    $added = $false
    foreach ($d in $dirs) {
        if ((Test-Path $d) -and ($env:Path -notlike "*$d*")) {
            $env:Path = $env:Path + ';' + $d
            $added = $true
        }
    }
    return $added
}

# Locate the Ghostscript console binary (gswin64c.exe / gswin32c.exe / gs.exe)
function Find-Ghostscript {
    $roots = @('C:\Program Files\gs', 'C:\Program Files (x86)\gs', "$env:LOCALAPPDATA\gs")
    foreach ($root in $roots) {
        if (-not (Test-Path $root)) { continue }
        foreach ($exe in 'gswin64c.exe', 'gswin32c.exe', 'gsc.exe', 'gs.exe') {
            $hit = Get-ChildItem $root -Recurse -Filter $exe -ErrorAction SilentlyContinue |
                   Select-Object -First 1
            if ($hit) { return $hit.FullName }
        }
    }
    $cmd = Get-Command gswin64c.exe, gs.exe -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($cmd) { return $cmd.Source }
    return $null
}

# merge_and_convert.sh / convert_pdf_a4l.sh call plain `gs`
function New-GhostscriptShim {
    param([string] $GsExe)
    if (-not $GsExe) { return $false }
    if (-not (Test-Path $BinDir)) { New-Item -ItemType Directory -Path $BinDir | Out-Null }

    $dir  = Split-Path -Parent $GsExe
    $name = Split-Path -Leaf  $GsExe

    $cmd = @"
@echo off
REM Generated by setup.ps1 -- forwards to the real Ghostscript binary.
"$GsExe" %*
"@
    Set-Content -Path (Join-Path $BinDir 'gs.cmd') -Value $cmd -Encoding ASCII

    # POSIX shim for Git Bash / WSL
    $posixDir = ($dir -replace '\\', '/') -replace '^([A-Za-z]):', '/$1'
    $sh = @"
#!/usr/bin/env bash
# Generated by setup.ps1 -- Ghostscript on Windows ships as $name.
exec "$posixDir/$name" "`$@"
"@
    $shPath = Join-Path $BinDir 'gs'
    Set-Content -Path $shPath -Value $sh -Encoding ASCII
    try { & bash -c "chmod +x '$shPath'" 2>$null } catch { }
    return $true
}

function Find-Python {
    $cands = @()
    if (Get-Command py -ErrorAction SilentlyContinue)      { $cands += ,@('py', '-3') }
    foreach ($n in 'python', 'python3') {
        if (Get-Command $n -ErrorAction SilentlyContinue)   { $cands += ,@($n) }
    }
    foreach ($cand in $cands) {
        $exe = $cand[0]
        $pre = @()
        if ($cand.Count -gt 1) { $pre = $cand[1..($cand.Count - 1)] }
        try {
            $global:LASTEXITCODE = 0
            $ver = & $exe @pre -c "import sys;print('%d.%d'%sys.version_info[:2])" 2>$null
            if (($LASTEXITCODE -eq 0) -and $ver) {
                $p = ([string]$ver).Trim().Split('.')
                if (([int]$p[0] -gt 3) -or (([int]$p[0] -eq 3) -and ([int]$p[1] -ge 10))) {
                    $real = & $exe @pre -c "import sys;print(sys.executable)" 2>$null
                    return [pscustomobject]@{
                        Exe = $exe
                        Pre = $pre
                        Real = ([string]$real).Trim()
                    }
                }
            }
        } catch { }
    }
    return $null
}

function Get-BashFlavour {
    $gitBash = @(
        'C:\Program Files\Git\bin\bash.exe',
        'C:\Program Files (x86)\Git\bin\bash.exe',
        "$env:LOCALAPPDATA\Programs\Git\bin\bash.exe"
    ) | Where-Object { Test-Path $_ } | Select-Object -First 1

    $wsl = $null
    if (Get-Command wsl.exe -ErrorAction SilentlyContinue) {
        $out = & wsl.exe --list --quiet 2>$null
        if ($LASTEXITCODE -eq 0) { $wsl = ($out | Where-Object { $_ -and $_.Trim() } | Select-Object -First 1) }
    }
    return [pscustomobject]@{ GitBash = $gitBash; WslDistro = $wsl }
}

# ══════════════════════════════════════════════════════════════════════
# Main
# ══════════════════════════════════════════════════════════════════════

# Bangla filenames in chem_pdfs/ -- make sure the console speaks UTF-8
try {
    chcp 65001 | Out-Null
    [Console]::OutputEncoding = [System.Text.Encoding]::UTF8
    $OutputEncoding = [System.Text.Encoding]::UTF8
} catch { }

$forward = @()
if ($NoSystem)     { $forward += '-NoSystem' }
if ($RecreateVenv) { $forward += '-RecreateVenv' }
if ($SkipVerify)   { $forward += '-SkipVerify' }

$script:Reported  = $false
$script:ExitCode  = 0

function Invoke-Main {

Say ''
Say 'PDF Layout Studio - setup (Windows)'
Say ("{0}  |  PS {1}" -f $PSVersionTable.PSVersion, $PSVersionTable.PSEdition)
Say $ScriptDir
Say ''

$py = Find-Python
if (-not $py) {
    Warn 'No Python 3.10+ interpreter found.'
    Warn 'The progress display and .venv both need one.'
}
if (-not (Start-Renderer $py.Real)) {
    Warn 'Live progress unavailable - using plain output.'
}

Add-Task 'pre'  'Preflight checks'
Add-Task 'sys'  'System packages'
Add-Task 'shim' 'Ghostscript shim (bin\gs)'
Add-Task 'venv' 'Virtual environment (.venv)'
Add-Task 'deps' 'Python dependencies'
Add-Task 'ver'  'Verification'
Add-Task 'bash' 'Shell script runner (WSL / Git Bash)'

# ── Preflight ──────────────────────────────────────────────────────────
$pm = Get-PackageManager
$isAdmin = Test-Admin
$adminTxt = if ($isAdmin) { 'elevated' } else { 'standard user' }

if ($py) {
    End-Task 'pre' 'done'
    Info ("python {0}  |  package manager: {1}  |  {2}" -f (& $py.Real -V 2>&1), $(if ($pm) { $pm } else { 'none' }), $adminTxt)
} else {
    End-Task 'pre' 'skipped' 'python not found'
}

# ── System packages ────────────────────────────────────────────────────
$qpdfOk = [bool](Get-Command qpdf.exe -ErrorAction SilentlyContinue)
$gsExe  = Find-Ghostscript
$gsOk   = [bool]$gsExe
$pyOk   = [bool]$py

$needSys = (-not $qpdfOk) -or (-not $gsOk) -or (-not $pyOk)

if ($NoSystem) {
    End-Task 'sys' 'skipped' '-NoSystem'
    Note 'warn' 'Skipping system packages (qpdf / Ghostscript / Python).'
} elseif (-not $needSys) {
    $qver = (qpdf --version 2>$null | Select-Object -First 1)
    End-Task 'sys' 'skipped' "qpdf $qver, gs present"
} elseif (-not $pm) {
    End-Task 'sys' 'failed' 'no package manager'
    Note 'err' 'No winget, choco or scoop found. Install qpdf and Ghostscript manually:'
    Note 'info' '  https://qpdf.sourceforge.io  |  https://www.ghostscript.com/releases/gsdnld.html'
} {
    # A system install needs Administrator. Ask first, then re-launch elevated.
    if (-not $isAdmin) {
        Note 'step' 'qpdf / Ghostscript / Python must be installed, which needs Administrator.'
        $answer = 'n'
        Invoke-Interactive {
            $script:answer = Read-Host '    Re-launch this script elevated (UAC prompt)? [y/N]'
        }
        if ("$answer".Trim().ToLower() -notin @('y', 'yes')) {
            End-Task 'sys' 'skipped' 'elevation declined'
            Note 'warn' 'Continuing without system packages.'
            $needSys = $false
        } else {
            Stop-Renderer
            $psExe = if ($PSHOME) { Join-Path $PSHOME 'powershell.exe' } else { 'powershell.exe' }
            if (-not (Test-Path $psExe)) { $psExe = 'powershell.exe' }
            $argv = @('-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', $ScriptFile) + $forward
            Info "Re-launching: $psExe $($argv -join ' ')"
            $p = Start-Process -FilePath $psExe -Verb RunAs -ArgumentList $argv -Wait -PassThru
            Say ''
            if ($p.ExitCode -eq 0) { Say 'Setup finished in the elevated window.' -ForegroundColor Green }
            else { Say "Elevated setup exited with code $($p.ExitCode)." -ForegroundColor Yellow }
            exit $p.ExitCode
        }
    }

    if ($needSys) {
        Note 'step' "Installing with $pm"
        switch ($pm) {
            'winget' {
                $ids = @()
                if (-not $qpdfOk) { $ids += 'QPDF.QPDF' }
                if (-not $gsOk)   { $ids += 'ArtifexSoftware.GhostScript' }
                foreach ($id in $ids) {
                    $found = Invoke-Logged 'sys' "winget install -e --id $id" {
                        & winget install -e --id $id `
                            --accept-package-agreements --accept-source-agreements `
                            --disable-interactivity
                    }
                    if (-not $found) {
                        Warn "winget could not install $id (its installer may be interactive)."
                        Note 'info' "  Retry from an elevated prompt:  winget install -e --id $id"
                    }
                }
            }
            'choco' {
                $pkgs = @()
                if (-not $qpdfOk) { $pkgs += 'qpdf' }
                if (-not $gsOk)   { $pkgs += 'ghostscript' }
                Invoke-Logged 'sys' "choco install $($pkgs -join ' ')" {
                    & choco install @pkgs -y
                } | Out-Null
            }
            'scoop' {
                $pkgs = @()
                if (-not $qpdfOk) { $pkgs += 'qpdf' }
                if (-not $gsOk)   { $pkgs += 'ghostscript' }
                Invoke-Logged 'sys' "scoop install $($pkgs -join ' ')" {
                    & scoop install @pkgs
                } | Out-Null
            }
        }

        if (Update-PathFromDisk) {
            Note 'info' 'Added newly installed tool directories to PATH for this session.'
            Note 'info' 'Reopen your terminal later so they persist in a new shell.'
        }

        $qpdfOk = [bool](Get-Command qpdf.exe -ErrorAction SilentlyContinue)
        $gsExe  = Find-Ghostscript
        $gsOk   = [bool]$gsExe

        $still = @()
        if (-not $qpdfOk) { $still += 'qpdf' }
        if (-not $gsOk)   { $still += 'Ghostscript' }
        if ($still.Count -gt 0) {
            End-Task 'sys' 'failed' "missing: $($still -join ', ')"
            Note 'warn' "Still missing: $($still -join ', ')"
            Note 'warn' '  process.py works without these; merge_and_convert.sh does not.'
        } else {
            $qver = (qpdf --version 2>$null | Select-Object -First 1)
            End-Task 'sys' 'done'
            Info "qpdf $qver"
        }
    }
}

# ── Ghostscript shim ───────────────────────────────────────────────────
$gsExe = Find-Ghostscript
if (-not $gsExe) {
    End-Task 'shim' 'skipped' 'Ghostscript not installed'
} elseif (Test-Path (Join-Path $BinDir 'gs.cmd')) {
    End-Task 'shim' 'skipped' 'already present'
} elseif (New-GhostscriptShim $gsExe) {
    End-Task 'shim' 'done'
    Info "gs shim -> $gsExe"
} else {
    End-Task 'shim' 'failed' 'could not write shim'
}

# ── Python + virtualenv ────────────────────────────────────────────────
$py = Find-Python
if (-not $py) {
    End-Task 'venv' 'failed' 'no python 3.10+'
    Die 'Python 3.10 or newer is required. Install it, then re-run setup.ps1.'
}

if ($RecreateVenv -and (Test-Path $Venv)) {
    Note 'step' "Recreating $Venv"
    Remove-Item -Recurse -Force $Venv
}

if (Test-Path $VenvPy) {
    $v = (& $VenvPy -V 2>&1) -join ' '
    End-Task 'venv' 'skipped' "$v (existing)"
} else {
    if (Invoke-Logged 'venv' "$($py.Real) -m venv .venv" {
        & $py.Real -m venv $Venv
    }) {
        Info "Created $Venv"
    } else {
        End-Task 'venv' 'failed' 'venv creation failed'
        Die 'Could not create the virtual environment.'
    }
}

if (-not (Test-Path $VenvPy)) {
    End-Task 'venv' 'failed' 'venv interpreter missing'
    Die "Virtual environment interpreter not found at $VenvPy"
}

# Upgrade the renderer now that .venv has rich
if ($script:RenderPy -ne $VenvPy) {
    $hasRich = $false
    try { $global:LASTEXITCODE = 0; & $VenvPy -c 'import rich' 2>$null | Out-Null; $hasRich = ($LASTEXITCODE -eq 0) } catch { }
    if ($hasRich) {
        Stop-Renderer
        if (Start-Renderer $VenvPy) { Replay-Tasks }
    }
}

# ── pip dependencies ───────────────────────────────────────────────────
if (-not (Test-Path $ReqFile)) {
    End-Task 'deps' 'failed' 'requirements.txt not found'
    Die "Missing $ReqFile"
}

if (Invoke-Logged 'deps' 'pip install --upgrade pip' {
    & $VenvPy -m pip install --progress-bar raw --upgrade pip
}) { } else {
    Warn 'pip self-upgrade failed - continuing with the bundled pip.'
}

if (-not (Invoke-Logged 'deps' 'pip install -r requirements.txt' {
        & $VenvPy -m pip install --progress-bar raw -r $ReqFile
    })) {
    Die 'pip install failed - see the output panel above.'
}

# ── Verification ───────────────────────────────────────────────────────
if ($SkipVerify) {
    End-Task 'ver' 'skipped' '-SkipVerify'
} else {
    $verifyPy = @'
import io, sys, contextlib

MODS = [("pymupdf", "fitz"), ("numpy", "numpy"), ("yt-dlp", "yt_dlp"),
        ("regex", "regex"), ("gdown", "gdown"), ("pathvalidate", "pathvalidate"),
        ("rich", "rich")]

# PyMuPDF prints a deprecation notice on stdout when process.py's `import fitz`
# runs. Capture it so it does not scramble the live display, then report it.
notices = []


def check(dist, mod):
    buf = io.StringIO()
    try:
        with contextlib.redirect_stdout(buf):
            m = __import__(mod)
    except Exception as e:
        print("  %-24s MISSING  (%s)" % ("%s  (%s)" % (dist, mod), e))
        return False
    for line in buf.getvalue().splitlines():
        if "deprecated" in line.lower():
            notices.append(line.strip())
    ver = getattr(m, "__version__", None) or getattr(m, "version", "")
    print("  %-24s %s" % ("%s  (%s)" % (dist, mod), ver or "installed"))
    return True


print("  %-24s %s" % ("python", sys.version.split()[0]))
missing = [d for d, m in MODS if not check(d, m)]
for n in notices:
    print("  note: %s" % n)
sys.exit(1 if missing else 0)
'@

    Start-Task 'ver' 'importing modules'
    $verifyOk = Invoke-Logged 'ver' 'import check' {
        $verifyPy | & $VenvPy - 2>&1
    }
    if ($verifyOk) {
        $smokeOk = Invoke-Logged 'ver' 'process.py config.json --info' {
            & $VenvPy (Join-Path $ScriptDir 'process.py') (Join-Path $ScriptDir 'config.json') --info 2>&1
        }
        if ($smokeOk) { End-Task 'ver' 'done' 'all modules import; process.py --info ok' }
        else { End-Task 'ver' 'failed' 'process.py --info failed' }
    }
}

# ── Shell script runner detection ──────────────────────────────────────
$bash = Get-BashFlavour
if ($bash.GitBash) {
    $bver = (& $bash.GitBash --version 2>$null | Select-Object -First 1)
    End-Task 'bash' 'done' 'Git Bash found'
    Info "Git Bash: $bver"
} elseif ($bash.WslDistro) {
    End-Task 'bash' 'done' "WSL: $($bash.WslDistro)"
} else {
    End-Task 'bash' 'skipped' 'no bash found'
}

# ── Wrap up ────────────────────────────────────────────────────────────
# Close the live table first: everything below is plain Write-Host output.
Stop-Renderer
Say ''

if (-not (Get-Command qpdf.exe -ErrorAction SilentlyContinue)) {
    Say ([char]0x26A0 + "  qpdf is not installed - merge_and_convert.sh cannot merge PDFs.") -ForegroundColor Yellow
    Say '   winget install -e --id QPDF.QPDF' -ForegroundColor Cyan
}

$bashPathNote = Join-Path $ScriptDir 'bin'
if ($bash.GitBash) {
    Say ([char]0x2714 + " Setup complete.") -ForegroundColor Green
    Say ''
    Say 'Activate the environment'
    Say "  $Venv\Scripts\Activate.ps1"
    Say ''
    Say 'Add the gs shim to PATH (needed by the .sh scripts):'
    Say "  `$env:Path = `"$bashPathNote;`$env:Path`""
    Say ''
    Say 'Run the bash tools'
    Say "  & '$($bash.GitBash)' -lc 'cd `"$ScriptDir`" && ./merge_and_convert.sh ./chem_pdfs'"
    Say ''
} elseif ($bash.WslDistro) {
    Say ([char]0x2714 + " Setup complete.") -ForegroundColor Green
    Say ''
    Say 'Activate the environment'
    Say "  $Venv\Scripts\Activate.ps1"
    Say ''
    Say "Run the bash tools inside WSL ($($bash.WslDistro)):"
    Say "  wsl -d $($bash.WslDistro) -- bash -lc 'cd /mnt/c/... && ./merge_and_convert.sh ./chem_pdfs'"
    Say '  (WSL needs its own qpdf/ghostscript:  sudo apt install qpdf ghostscript)'
    Say ''
} else {
    Say ([char]0x26A0 + " Setup complete, but no bash was found.") -ForegroundColor Yellow
    Say 'process.py / scrape_playlist.py / download_pdfs.py all work as-is.'
    Say 'merge_and_convert.sh and convert_pdf_a4l.sh need WSL or Git for Windows.'
    Say '  winget install -e --id Microsoft.WSL'
    Say '  winget install -e --id Git.Git'
    Say ''
}

Say 'Typical workflow'
Say '  1. scrape Drive links    .\.venv\Scripts\python.exe scrape_playlist.py "<playlist url>" -o links.json'
Say '  2. download the PDFs   .\.venv\Scripts\python.exe download_pdfs.py links.json -d chem_pdfs'
Say '  3. merge + A4 landscape   .\merge_and_convert.sh  (via bash)'
Say '  4. N-up layout         .\.venv\Scripts\python.exe process.py config.json .\chem_pdfs\'
Say '  5. visual preview      start pdf-layout-studio.html'
Say ''

}

# Dot-sourcing exposes the helpers for testing without running setup.
if ($MyInvocation.InvocationName -ne '.') {
    try {
        Invoke-Main
    }
    catch {
        if (-not $script:Reported) {
            Err $_.Exception.Message
            if ($_.ScriptStackTrace) { Write-Host $_.ScriptStackTrace -ForegroundColor DarkGray }
        }
        $script:ExitCode = 1
    }
    finally {
        # Draws the final task table before the window closes
        Stop-Renderer
    }

    exit $script:ExitCode
}