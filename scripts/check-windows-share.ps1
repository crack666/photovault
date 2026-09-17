# Prueft, ob ein Windows-SMB-Share erreichbar ist -- ohne Passwort,
# ohne Abbruch. Die UNC steht in ~/.config/photovault/runtime (SMB_UNC),
# nicht in diesem Skript.
#
# Exit 0 immer: ein toter Share soll PhotoVault nicht am Start hindern.
# Der Index liegt in Qdrant; nur Thumbs und Ingest brauchen die Dateien.
param(
    [Parameter(Mandatory = $true)]
    [string]$Unc
)

$ErrorActionPreference = "Continue"
$Unc = $Unc.Trim().Trim('"')
if (-not $Unc) { exit 0 }

function Write-Ok($msg) { Write-Host "  * $msg" }
function Write-Bad($msg) { Write-Host "  x $msg" }
function Write-Hint($msg) { Write-Host "     $msg" }

if (Test-Path -LiteralPath $Unc) {
    Write-Ok "Windows-Share erreichbar: $Unc"
    exit 0
}

# Credential Manager / bestehende net-use-Sitzung, kein Prompt.
$prevEap = $ErrorActionPreference
$ErrorActionPreference = "SilentlyContinue"
cmd.exe /c "net use `"$Unc`" >nul 2>&1"
$ErrorActionPreference = $prevEap

if (Test-Path -LiteralPath $Unc) {
    Write-Ok "Windows-Share verbunden: $Unc"
    exit 0
}

Write-Bad "Windows-Share nicht erreichbar: $Unc"
Write-Hint "Explorer einmal oeffnen, oder: net use `"$Unc`""
Write-Hint "WSL hat eine eigene CIFS-Sitzung -- der Mount dort wird separat geprueft."
exit 0
