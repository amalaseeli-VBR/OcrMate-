[CmdletBinding()]
param(
    [string]$ProjectDir = $PSScriptRoot,
    [string]$VenvName = "virtualenv311",
    [string]$RequirementsFile = "requirements-py311.txt",
    [switch]$InstallPython,
    [switch]$InstallGit,
    [switch]$InstallTesseract,
    [switch]$InstallVCRedist,
    [switch]$InstallODBC17,
    [switch]$RunApp,
    [string]$AppFile = "docmate_app_v1.18.001_r01_customer.py"
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

function Write-Step {
    param([string]$Message)
    Write-Host ""
    Write-Host "==> $Message" -ForegroundColor Cyan
}

function Test-CommandExists {
    param([string]$Name)
    return [bool](Get-Command $Name -ErrorAction SilentlyContinue)
}

function Install-WingetPackage {
    param(
        [string]$Id,
        [string]$Name
    )
    if (-not (Test-CommandExists winget)) {
        throw "winget is not available. Install App Installer from Microsoft Store, then rerun this script."
    }

    Write-Step "Installing $Name with winget"
    winget install --id $Id --exact --source winget --accept-source-agreements --accept-package-agreements
}

function Get-Python311Command {
    if (Test-CommandExists py) {
        try {
            & py -3.11 --version | Out-Null
            return @("py", "-3.11")
        } catch {
        }
    }

    if (Test-CommandExists python) {
        $version = & python -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')"
        if ($version -eq "3.11") {
            return @("python")
        }
    }

    return $null
}

function Invoke-Python311 {
    param(
        [string[]]$PythonCommand,
        [string[]]$Arguments
    )

    if ($PythonCommand.Count -gt 1) {
        & $PythonCommand[0] @($PythonCommand[1..($PythonCommand.Count - 1)]) @Arguments
    } else {
        & $PythonCommand[0] @Arguments
    }
}

Write-Step "DocMate Python 3.11 setup"
Write-Host "ProjectDir: $ProjectDir"
Write-Host "VenvName:   $VenvName"

if ($InstallPython) {
    Install-WingetPackage -Id "Python.Python.3.11" -Name "Python 3.11"
}

if ($InstallGit) {
    Install-WingetPackage -Id "Git.Git" -Name "Git for Windows"
}

if ($InstallVCRedist) {
    Install-WingetPackage -Id "Microsoft.VCRedist.2015+.x64" -Name "Microsoft Visual C++ Redistributable 2015-2022 x64"
}

if ($InstallODBC17) {
    Install-WingetPackage -Id "Microsoft.ODBCDriver.17forSQLServer" -Name "Microsoft ODBC Driver 17 for SQL Server"
}

if ($InstallTesseract) {
    Install-WingetPackage -Id "UB-Mannheim.TesseractOCR" -Name "Tesseract OCR"
}

$python311 = Get-Python311Command
if ($null -eq $python311) {
    throw "Python 3.11 was not found. Rerun with -InstallPython, then open a new PowerShell window if PATH is not refreshed."
}

if (-not (Test-Path -LiteralPath $ProjectDir)) {
    throw "Project folder not found: $ProjectDir"
}

Set-Location -LiteralPath $ProjectDir

$requirementsPath = Join-Path $ProjectDir $RequirementsFile
if (-not (Test-Path -LiteralPath $requirementsPath)) {
    throw "Requirements file not found: $requirementsPath"
}

$venvPath = Join-Path $ProjectDir $VenvName
if (-not (Test-Path -LiteralPath $venvPath)) {
    Write-Step "Creating Python 3.11 virtual environment"
    Invoke-Python311 -PythonCommand $python311 -Arguments @("-m", "venv", $venvPath)
} else {
    Write-Step "Using existing virtual environment"
}

$venvPython = Join-Path $venvPath "Scripts\python.exe"
if (-not (Test-Path -LiteralPath $venvPython)) {
    throw "Virtual environment Python not found: $venvPython"
}

Write-Step "Upgrading pip, setuptools, and wheel"
& $venvPython -m pip install --upgrade pip setuptools wheel

Write-Step "Installing DocMate Python packages"
& $venvPython -m pip install -r $requirementsPath

Write-Step "Verifying important imports"
& $venvPython -c "import sys, reportlab, streamlit, pandas, fitz, cv2; print('Python', sys.version); print('reportlab ok'); print('streamlit ok'); print('pymupdf ok'); print('opencv ok')"

Write-Step "Checking optional desktop tools"
$tesseract = Get-Command tesseract -ErrorAction SilentlyContinue
if ($tesseract) {
    Write-Host "Tesseract: $($tesseract.Source)" -ForegroundColor Green
} else {
    Write-Host "Tesseract not found on PATH. Install with: .\setup_windows_py311.ps1 -InstallTesseract" -ForegroundColor Yellow
}

if ($RunApp) {
    $appPath = Join-Path $ProjectDir $AppFile
    if (-not (Test-Path -LiteralPath $appPath)) {
        throw "App file not found: $appPath"
    }

    Write-Step "Starting Streamlit"
    & $venvPython -m streamlit run $appPath
} else {
    Write-Step "Setup complete"
    Write-Host "Activate environment:" -ForegroundColor Green
    Write-Host ".\$VenvName\Scripts\Activate.ps1"
    Write-Host ""
    Write-Host "Run app:" -ForegroundColor Green
    Write-Host ".\$VenvName\Scripts\python.exe -m streamlit run $AppFile"
}
