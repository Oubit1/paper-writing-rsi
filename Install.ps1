$ErrorActionPreference='Stop'
Set-Location -LiteralPath $PSScriptRoot
$pythonCommand=Get-Command py -ErrorAction SilentlyContinue
if($pythonCommand){$pythonExe=$pythonCommand.Source;$pythonArgs=@('-3')}
else{$pythonCommand=Get-Command python -ErrorAction SilentlyContinue;if(-not $pythonCommand){throw 'Install Python 3.10-3.13 x64 from python.org, then run this installer again.'};$pythonExe=$pythonCommand.Source;$pythonArgs=@()}
& $pythonExe @pythonArgs -c "import sys,struct; assert (3,10)<=sys.version_info[:2]<(3,14) and struct.calcsize('P')==8, 'Python 3.10-3.13 x64 is required'"
if($LASTEXITCODE -ne 0){throw 'Unsupported Python version or architecture.'}
& $pythonExe @pythonArgs -m venv .venv
if($LASTEXITCODE -ne 0){throw 'Could not create the Python virtual environment.'}
$localPython=Join-Path $PSScriptRoot '.venv\Scripts\python.exe'
& $localPython -m pip install -r requirements.txt -r requirements-translation.txt
if($LASTEXITCODE -ne 0){throw 'Dependency installation failed. Check your connection and retry.'}
& $localPython -c "import ctranslate2,sentencepiece,pypdf,pdfplumber,PIL; print('Dependencies ready.')"
if($LASTEXITCODE -ne 0){throw 'Dependency check failed.'}
& $localPython scripts/download_runtime.py
if($LASTEXITCODE -ne 0){throw 'Runtime download failed. Check your connection and retry.'}
& $localPython scripts/install_paper_writing_skill.py
if($LASTEXITCODE -ne 0){throw 'Writing skill installation failed. Check your connection and retry.'}
Write-Host 'Ready. Sign in to your own Codex installation, then run Start.ps1.'
