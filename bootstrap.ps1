$ErrorActionPreference = "Stop"

if ($env:TERMUX_VERSION -or (($env:PREFIX | Out-String).ToLower().Contains("com.termux"))) {
    Write-Host "DeathTG does not support Termux or Android terminal environments."
    exit 1
}

$python = $null
if (Get-Command py -ErrorAction SilentlyContinue) {
    $python = @("py", "-3")
} elseif (Get-Command python -ErrorAction SilentlyContinue) {
    $python = @("python")
} else {
    Write-Host "Python 3 is required."
    exit 1
}

& $python[0] @($python[1..($python.Length - 1)] | Where-Object { $_ }) "bootstrap.py"
