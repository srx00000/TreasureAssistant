param([string]$IndexUrl = 'https://pypi.org/simple')
$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
try {
    $candidateList = @(
        @{ Cmd = 'py'; Prefix = @('-3.12') },
        @{ Cmd = 'py'; Prefix = @('-3.11') },
        @{ Cmd = 'python'; Prefix = @() }
    )
    $found = $null
    foreach ($candidate in $candidateList) {
        if (-not (Get-Command $candidate.Cmd -ErrorAction SilentlyContinue)) { continue }
        $cmd = $candidate.Cmd
        $prefix = $candidate.Prefix
        try {
            $probe = & $cmd @prefix -c "import sys,struct; print('OK' if sys.version_info[:2] in ((3,11),(3,12)) and struct.calcsize('P')==8 else 'NO')" 2>$null
            if ($LASTEXITCODE -eq 0 -and $probe -eq 'OK') { $found = $candidate; break }
        } catch { }
    }
    if ($null -eq $found) {
        throw 'Install Python 3.12 x64 from https://www.python.org/downloads/windows/ (enable Add Python to PATH), then run SETUP.cmd again. Python 3.11 x64 is also supported.'
    }
    $cmd = $found.Cmd
    $prefix = $found.Prefix
    & $cmd @prefix -m venv .venv
    if ($LASTEXITCODE -ne 0) { throw 'Could not create the local Python environment.' }
    $python = Join-Path $PSScriptRoot '.venv\Scripts\python.exe'
    & $python -m pip install --disable-pip-version-check --index-url $IndexUrl -r requirements.txt
    if ($LASTEXITCODE -ne 0) { throw 'Package installation failed. Check your network, or try SETUP_CHINA.cmd.' }
    & $python -c "import tkinter,mss,opencc; from rapidocr_onnxruntime import RapidOCR; RapidOCR(intra_op_num_threads=2,inter_op_num_threads=1); print('OCR ready / models are local.')"
    if ($LASTEXITCODE -ne 0) { throw 'OCR initialization failed. Check setup.log. For DLL errors install Microsoft Visual C++ 2015-2022 Redistributable x64: https://aka.ms/vs/17/release/vc_redist.x64.exe' }
    Write-Host 'Ready. Run START_TW.cmd (traditional), START_CN.cmd (simplified), or DEMO.cmd.' -ForegroundColor Green
    exit 0
} catch {
    Write-Host $_.Exception.Message -ForegroundColor Red
    exit 1
}
