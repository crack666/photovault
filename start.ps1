#requires -Version 5.1
<#
PhotoVault starten -- der Weg fuer neue Nutzer unter Windows.

    start.bat            starten; beim ersten Mal der Einrichtungsassistent
    start.bat stop       Verbund anhalten (Index und Einstellungen bleiben)
    start.bat status
    start.bat restart
    start.bat setup      Assistent erzwingen, auch wo eine lokale Installation liegt

Was dieses Skript tut, und warum es das tut:

1. Docker pruefen und im Klartext sagen, was fehlt.
2. .env anlegen (freie Ports, gemessener Grafikspeicher).
3. docker-compose.override.yml erzeugen: alle festen Laufwerke *lesend*
   unter /host/<laufwerk>, die gewaehlten Fotoordner (data/sources.txt)
   zusaetzlich *beschreibbar* am selben Pfad. Nur dort darf PhotoVault
   schreiben -- Papierkorb, Verschieben, exif_repair --, und nirgends sonst.
   Die Grenze zieht Docker, nicht der Code.
4. Verbund hochfahren, Browser auf die Einrichtung.
5. Zweite Phase: sobald im Browser die Ordner gewaehlt sind, die Override
   mit den beschreibbaren Mounts neu schreiben und den Container neu
   erstellen. Der Nutzer tippt dafuer nichts; die Seite wartet.

Pfade, Ordner und Modelle stehen nicht in diesem Skript -- .env, data/ und
die Override-Datei sind die Ablage, alle drei nicht im Git.
#>
[CmdletBinding()]
param(
    [Parameter(Position = 0)]
    [string]$Action = "start",
    # Nur fuer Tests: Override-Text fuer diese Quellenliste erzeugen (in
    # -OutFile, UTF-8 ohne BOM wie im echten Lauf), nichts starten.
    [string]$EmitOverride,
    [string]$OutFile,
    [string[]]$Drives
)

$ErrorActionPreference = "Continue"
$Repo = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $Repo

$EnvFile = Join-Path $Repo ".env"
$DataDir = Join-Path $Repo "data"
$SourcesFile = Join-Path $DataDir "sources.txt"
$OverrideFile = Join-Path $Repo "docker-compose.override.yml"
#: Netzwerkfreigaben als CIFS-Volumes -- schreibt der Server (ingest/nas.py),
#: weil er Zugangsdaten und Quellen kennt. Hier nur in COMPOSE_FILE eintragen.
$NasFile = Join-Path $DataDir "nas-volumes.yml"

function Say($m) { Write-Host "  $m" }
function Ok($m) { Write-Host "  * $m" }
function Warn($m) { Write-Host "  ! $m" }
function Bad($m) { Write-Host "  x $m" }

# --------------------------------------------------------------------------
# .env
# --------------------------------------------------------------------------

function Read-EnvFile {
    $map = [ordered]@{}
    if (-not (Test-Path -LiteralPath $EnvFile)) { return $map }
    foreach ($line in Get-Content -LiteralPath $EnvFile) {
        $t = $line.Trim()
        if (-not $t -or $t.StartsWith("#")) { continue }
        $eq = $t.IndexOf("=")
        if ($eq -lt 1) { continue }
        $map[$t.Substring(0, $eq).Trim()] = $t.Substring($eq + 1).Trim()
    }
    return $map
}

function Set-EnvValue([string]$Key, [string]$Value) {
    # Eine Zeile ersetzen oder anhaengen -- Kommentare und andere Zeilen bleiben.
    $lines = @()
    if (Test-Path -LiteralPath $EnvFile) { $lines = @(Get-Content -LiteralPath $EnvFile) }
    $done = $false
    $out = foreach ($line in $lines) {
        if ($line -match "^\s*$([regex]::Escape($Key))\s*=") { $done = $true; "$Key=$Value" } else { $line }
    }
    if (-not $done) { $out = @($out) + "$Key=$Value" }
    Set-Content -LiteralPath $EnvFile -Value $out -Encoding ascii
}

function Test-PortFree([int]$Port) {
    try {
        $l = [System.Net.Sockets.TcpListener]::new([System.Net.IPAddress]::Loopback, $Port)
        $l.Start(); $l.Stop(); return $true
    } catch { return $false }
}

function Get-FreePort([int]$From) {
    for ($p = $From; $p -lt $From + 50; $p++) { if (Test-PortFree $p) { return $p } }
    return $From
}

function Get-VramMb {
    # nvidia-smi liegt bei jedem NVIDIA-Treiber; ohne Karte gibt es den Befehl nicht.
    $cmd = Get-Command nvidia-smi.exe -ErrorAction SilentlyContinue
    if (-not $cmd) { return 0 }
    try {
        $raw = & $cmd.Source --query-gpu=memory.total --format=csv,noheader,nounits 2>$null | Select-Object -First 1
        $n = 0
        if ([int]::TryParse(("$raw").Trim(), [ref]$n)) { return $n }
    } catch {}
    return 0
}

#: Ab diesem Treiber gibt es CUDA 13 -- die cuda-Variante des Images ist
#: dagegen gebaut (torch cu130, onnxruntime-gpu). Aelter: CPU-Variante.
$MinDriver = 580

function Get-DriverMajor {
    $cmd = Get-Command nvidia-smi.exe -ErrorAction SilentlyContinue
    if (-not $cmd) { return 0 }
    try {
        $raw = & $cmd.Source --query-gpu=driver_version --format=csv,noheader 2>$null | Select-Object -First 1
        $n = 0
        if ([int]::TryParse((("$raw").Trim() -split "\.")[0], [ref]$n)) { return $n }
    } catch {}
    return 0
}

# --------------------------------------------------------------------------
# Laufwerke und Override
# --------------------------------------------------------------------------

function Get-FixedDrives {
    # Nur feste Platten (DriveType 3). Netzlaufwerke (4) mountet Docker Desktop
    # nicht zuverlaessig, Wechselmedien (2) sind beim naechsten Start weg.
    try {
        return @(Get-CimInstance Win32_LogicalDisk -Filter "DriveType=3" -ErrorAction Stop |
            ForEach-Object { $_.DeviceID.Substring(0, 1).ToUpper() } | Sort-Object)
    } catch {
        return @(Get-PSDrive -PSProvider FileSystem | Where-Object { $_.Root -match '^[A-Z]:\\$' } |
            ForEach-Object { $_.Name.ToUpper() } | Sort-Object)
    }
}

function Get-ChosenHostPaths([string]$File) {
    <#
    Aktive Quellen aus data/sources.txt als Windows-Pfade.
    /host/d/Fotos/Alben  ->  D:/Fotos/Alben
    Ausschluesse (fuehrendes -) und stillgelegte Zeilen (#) zaehlen nicht:
    ein Ausschluss braucht keinen Schreibzugriff, und was still ist, ist still.
    #>
    $out = @()
    if (-not (Test-Path -LiteralPath $File)) { return $out }
    foreach ($line in Get-Content -LiteralPath $File -Encoding UTF8) {
        $t = ($line -split "#", 2)[0].Trim()
        if (-not $t -or $t.StartsWith("-")) { continue }
        if ($t -match '^/host/([A-Za-z])(/(.*))?$') {
            $letter = $Matches[1].ToUpper()
            $rest = if ($Matches[3]) { $Matches[3].TrimEnd("/") } else { "" }
            if (-not $rest) { continue }   # ein ganzes Laufwerk beschreibbar: nein
            $out += [pscustomobject]@{ Host = "$($letter):/$rest"; Target = "/host/$($letter.ToLower())/$rest" }
        }
    }
    return $out
}

function New-OverrideText([string[]]$DriveLetters, $Chosen, [bool]$Gpu = $false, [bool]$ApiGpu = $Gpu) {
    $sb = [System.Text.StringBuilder]::new()
    [void]$sb.AppendLine("# Von start.ps1 erzeugt -- bei jedem Start neu. Nicht von Hand aendern.")
    [void]$sb.AppendLine("# Laufwerke lesend unter /host/<laufwerk>; die gewaehlten Fotoordner aus")
    [void]$sb.AppendLine("# data/sources.txt zusaetzlich beschreibbar am selben Pfad.")
    [void]$sb.AppendLine("services:")
    if ($Gpu) {
        # Nur mit gemessener NVIDIA-Karte: ohne sie liesse die Reservierung
        # den ganzen Verbund nicht starten ("could not select device driver").
        # Beide bekommen sie: Ollama fuer die Beschreibungen, die API fuer
        # Gesichter und CLIP (Image-Variante `cuda`, ~10x beim Einlesen).
        [void]$sb.AppendLine("  ollama:")
        [void]$sb.AppendLine("    deploy:")
        [void]$sb.AppendLine("      resources:")
        [void]$sb.AppendLine("        reservations:")
        [void]$sb.AppendLine("          devices:")
        [void]$sb.AppendLine("            - driver: nvidia")
        [void]$sb.AppendLine("              count: all")
        [void]$sb.AppendLine("              capabilities: [gpu]")
    }
    [void]$sb.AppendLine("  api:")
    if ($ApiGpu) {
        [void]$sb.AppendLine("    deploy:")
        [void]$sb.AppendLine("      resources:")
        [void]$sb.AppendLine("        reservations:")
        [void]$sb.AppendLine("          devices:")
        [void]$sb.AppendLine("            - driver: nvidia")
        [void]$sb.AppendLine("              count: all")
        [void]$sb.AppendLine("              capabilities: [gpu]")
    }
    [void]$sb.AppendLine("    volumes:")
    foreach ($d in $DriveLetters) {
        [void]$sb.AppendLine("      - type: bind")
        [void]$sb.AppendLine("        source: `"$($d.ToUpper()):/`"")
        [void]$sb.AppendLine("        target: /host/$($d.ToLower())")
        [void]$sb.AppendLine("        read_only: true")
    }
    foreach ($c in @($Chosen)) {
        if (-not $c) { continue }
        [void]$sb.AppendLine("      - type: bind")
        [void]$sb.AppendLine("        source: `"$($c.Host)`"")
        [void]$sb.AppendLine("        target: $($c.Target)")
    }
    return $sb.ToString()
}

function Write-Override([bool]$Gpu = $false, [bool]$ApiGpu = $Gpu) {
    $drives = Get-FixedDrives
    $chosen = @(Get-ChosenHostPaths $SourcesFile | Where-Object { Test-Path -LiteralPath $_.Host })
    $text = New-OverrideText $drives $chosen $Gpu $ApiGpu
    # UTF-8 ohne BOM: Ordnernamen mit Umlauten muessen so ankommen, wie sie
    # heissen, und ein BOM am Dateianfang mag nicht jeder YAML-Leser.
    [System.IO.File]::WriteAllText($OverrideFile, $text, [System.Text.UTF8Encoding]::new($false))
    return $chosen
}

# --------------------------------------------------------------------------
# Docker
# --------------------------------------------------------------------------

function Test-Docker {
    $cmd = Get-Command docker.exe -ErrorAction SilentlyContinue
    if (-not $cmd) {
        Bad "Docker ist nicht installiert."
        Say ""
        Say "Docker Desktop laden und installieren, Rechner neu starten, Docker Desktop"
        Say "oeffnen und warten, bis das Wal-Symbol unten links ruhig steht:"
        Say "https://www.docker.com/products/docker-desktop/"
        return $false
    }
    & docker version *> $null
    if ($LASTEXITCODE -eq 0) { return $true }
    Bad "Docker laeuft nicht."
    Say ""
    Say "Docker Desktop starten und warten, bis das Wal-Symbol ruhig steht. Dann"
    Say "start.bat erneut. Startet Docker Desktop selbst nicht, sind es fast immer:"
    Say "  - Virtualisierung im BIOS aus (bei AMD 'SVM Mode', bei Intel 'VT-x')"
    Say "  - WSL 2 fehlt: in einer Eingabeaufforderung 'wsl --update' ausfuehren"
    try {
        $v = (Get-CimInstance Win32_Processor -ErrorAction Stop | Select-Object -First 1).VirtualizationFirmwareEnabled
        if ($v -eq $false) { Warn "Gemessen: Virtualisierung ist im BIOS AUS. Das ist die Ursache." }
        elseif ($v -eq $true) { Ok "Gemessen: Virtualisierung ist im BIOS an." }
    } catch {}
    return $false
}

function Compose { & docker compose @args }

function Test-DockerGpu {
    <#
    Sieht Docker die Karte? Unter Docker Desktop kommt sie ueber die
    WSL2-GPU-Durchreichung des NVIDIA-Treibers -- ohne Eintrag in einer
    .wslconfig, ohne Toolkit. Wenn das fehlschlaegt (Hyper-V-Backend statt
    WSL 2, alter Treiber, altes WSL), scheiterte sonst erst `compose up` mit
    "could not select device driver nvidia" -- fuer den Nutzer ein Raetsel.
    Deshalb vorher messen, mit dem Basis-Image, das ohnehin gebraucht wird.
    #>
    $out = & docker run --rm --gpus all python:3.11-slim nvidia-smi -L 2>&1
    if ($LASTEXITCODE -eq 0 -and "$out" -match "GPU 0") { return $true }
    Warn "Docker sieht die Grafikkarte nicht -- alles rechnet der Prozessor. Meist hilft:"
    Say "  - Docker Desktop -> Settings -> General: 'Use the WSL 2 based engine' an"
    Say "  - NVIDIA-Treiber aktualisieren (ab 580), dann 'wsl --update' und Neustart"
    Say ("  Docker sagte: " + (("$out" -split "`n")[-1]).Trim())
    return $false
}

function Wait-Api([string]$Url, [int]$Seconds, [string]$Note) {
    $t0 = Get-Date
    $shown = $false
    while (((Get-Date) - $t0).TotalSeconds -lt $Seconds) {
        try {
            $r = Invoke-WebRequest -UseBasicParsing -Uri "$Url/api/health" -TimeoutSec 3 -ErrorAction Stop
            if ($r.StatusCode -eq 200) { Write-Host ""; return $true }
        } catch {}
        if (-not $shown -and ((Get-Date) - $t0).TotalSeconds -gt 25 -and $Note) { Write-Host ""; Say $Note; $shown = $true }
        Write-Host -NoNewline "."
        Start-Sleep -Seconds 2
    }
    Write-Host ""
    return $false
}

function Get-SetupStep([string]$Url) {
    try {
        $r = Invoke-WebRequest -UseBasicParsing -Uri "$Url/api/setup/state" -TimeoutSec 5 -ErrorAction Stop
        $j = $r.Content | ConvertFrom-Json
        return "$($j.setup.step)"
    } catch { return "" }
}

# --------------------------------------------------------------------------
# Lokale Installation? (Entwicklungsmaschine)
# --------------------------------------------------------------------------

function Test-LocalInstall {
    $rt = Join-Path $env:USERPROFILE ".config\photovault\runtime"
    if ((Test-Path -LiteralPath $rt) -and (Select-String -Path $rt -Pattern '^RUNTIME=local' -Quiet)) { return $true }
    $wsl = Get-Command wsl.exe -ErrorAction SilentlyContinue
    if ($wsl) {
        & wsl.exe -e bash -lc "test -x ~/.venvs/photovault/bin/python" *> $null
        if ($LASTEXITCODE -eq 0) { return $true }
    }
    return $false
}

# --------------------------------------------------------------------------
# Ablauf
# --------------------------------------------------------------------------

if ($EmitOverride) {
    # Testmodus: Override fuer diese Quellenliste erzeugen, sonst nichts.
    # `-Drives C,D` kommt von einer fremden Shell als ein String an.
    $letters = if ($Drives) { @($Drives -split ",") } else { Get-FixedDrives }
    $chosen = @(Get-ChosenHostPaths $EmitOverride)
    $text = New-OverrideText $letters $chosen ([bool]$env:PHOTOVAULT_TEST_GPU)
    if ($OutFile) { [System.IO.File]::WriteAllText($OutFile, $text, [System.Text.UTF8Encoding]::new($false)) }
    else { Write-Output $text }
    exit 0
}

$force = $false
if ($Action -in @("setup", "--docker")) { $force = $true; $Action = "start" }

if (-not $force -and (Test-Path (Join-Path $Repo "start-local.bat")) -and (Test-LocalInstall)) {
    # Nicht den Docker-Wizard neben die lokale Installation stellen: zwei
    # Indizes, die auf verschiedene Pfade zeigen.
    & (Join-Path $Repo "start-local.bat") $Action
    exit $LASTEXITCODE
}

Write-Host ""
Write-Host "  PhotoVault"
Write-Host "  =========="
Write-Host ""

if ($Action -eq "stop") { Compose down; exit $LASTEXITCODE }
if ($Action -eq "status") { Compose ps; exit $LASTEXITCODE }
if ($Action -notin @("start", "restart")) { Bad "Unbekannt: $Action (start, stop, status, restart, setup)"; exit 2 }

if (-not (Test-Docker)) { exit 1 }

# --- .env: Ports beim ersten Mal, Grafikspeicher bei jedem Start ----------
$envMap = Read-EnvFile
if (-not (Test-Path -LiteralPath $EnvFile)) {
    $api = Get-FreePort 8000
    $qd = Get-FreePort 6333
    $ol = Get-FreePort 11434
    Set-Content -LiteralPath $EnvFile -Value @(
        "# Von start.bat angelegt. Ports frei gewaehlt; Fotoordner stehen in data/sources.txt.",
        "API_PORT=$api",
        "QDRANT_PORT=$qd",
        "OLLAMA_PORT=$ol",
        "# Ollama aus dem Buendel. Eigenes Ollama? Zeile leeren (COMPOSE_PROFILES=) und",
        "# im Wizard die Adresse eintragen, z.B. http://host.docker.internal:11434.",
        "COMPOSE_PROFILES=ollama",
        "# 0 = NVIDIA-Karte nicht benutzen, auch wenn eine da ist (nur Prozessor).",
        "PHOTOVAULT_GPU=1",
        "GPU_VRAM_MB=0"
    ) -Encoding ascii
    if ($ol -ne 11434) { Say "Port 11434 ist belegt -- laeuft hier schon ein Ollama? Das Buendel nimmt $ol; im Wizard laesst sich auch das eigene eintragen." }
    if ($api -ne 8000 -or $qd -ne 6333) { Warn "Port 8000 oder 6333 war belegt -- PhotoVault nimmt $api / $qd." }
    $envMap = Read-EnvFile
}
$vram = Get-VramMb
Set-EnvValue "GPU_VRAM_MB" $vram
# Die Karte benutzen, wenn eine da ist und der Treiber CUDA 13 kann -- es sei
# denn, die .env sagt nein. Ollama bekommt sie in jedem Fall, das eigene
# Image nur mit passendem Treiber.
$driver = Get-DriverMajor
$wantGpu = ($vram -gt 0) -and ($envMap["PHOTOVAULT_GPU"] -ne "0")
# Die Karte muss auch in Docker ankommen -- gemessen, nicht angenommen.
if ($wantGpu -and -not (Test-DockerGpu)) { $wantGpu = $false; $vram = 0 }
$useGpu = $wantGpu -and ($driver -ge $MinDriver)
if ($useGpu) {
    Set-EnvValue "PHOTOVAULT_IMAGE_TAG" "cuda"
    Set-EnvValue "TORCH_INDEX" "https://download.pytorch.org/whl/cu130"
    Set-EnvValue "ORT_PACKAGE" "onnxruntime-gpu"
    Ok "Grafikkarte: $([math]::Round($vram / 1024)) GB Speicher, Treiber $driver -- Beschreibungen, Gesichter und CLIP rechnen darauf."
} else {
    Set-EnvValue "PHOTOVAULT_IMAGE_TAG" "latest"
    Set-EnvValue "TORCH_INDEX" "https://download.pytorch.org/whl/cpu"
    Set-EnvValue "ORT_PACKAGE" "onnxruntime"
    if ($vram -gt 0 -and $envMap["PHOTOVAULT_GPU"] -eq "0") { Say "Grafikkarte gefunden, aber PHOTOVAULT_GPU=0 -- Gesichter und CLIP rechnet der Prozessor." }
    elseif ($vram -gt 0) { Warn "Grafikkarte gefunden, aber Treiber $driver ist aelter als $MinDriver (CUDA 13) -- Gesichter und CLIP rechnet der Prozessor. NVIDIA-Treiber aktualisieren, dann start.bat erneut." }
    else { Say "Keine NVIDIA-Grafikkarte gefunden -- alles rechnet der Prozessor." }
}
$port = if ($envMap["API_PORT"]) { [int]$envMap["API_PORT"] } else { 8000 }
$url = "http://localhost:$port"

function Set-ComposeFiles {
    # Compose fuehrt beliebig viele Dateien zusammen; Trenner unter Windows ist ';'.
    $files = @("docker-compose.yml", "docker-compose.override.yml")
    if (Test-Path -LiteralPath $NasFile) { $files += "data/nas-volumes.yml" }
    Set-EnvValue "COMPOSE_FILE" ($files -join ";")
    Set-EnvValue "COMPOSE_PATH_SEPARATOR" ";"
}

# --- Laufwerke und gewaehlte Ordner -----------------------------------------
New-Item -ItemType Directory -Force -Path $DataDir | Out-Null
Set-ComposeFiles
# Ollama darf die Karte auch mit aelterem Treiber nutzen; die API nur mit CUDA 13.
$chosen = Write-Override $wantGpu $useGpu
$drives = Get-FixedDrives
Ok ("Laufwerke lesend: " + (($drives | ForEach-Object { "$($_):" }) -join " "))
if ($chosen.Count) { Ok ("Beschreibbar: " + (($chosen | ForEach-Object { $_.Host }) -join ", ")) }

# --- Hochfahren -------------------------------------------------------------
if ($Action -eq "restart") { Compose down }
# Erst holen, sonst bauen -- und zwar *vor* `up`: mit `image:` und `build:`
# zugleich versucht `compose up` ein fehlendes Image zu ziehen und bricht
# ab, wenn die Registry "denied" sagt (gemessen beim ersten Fremden, als
# das Paket auf GHCR noch nicht oeffentlich war). Ist das Image nach dem Bau
# lokal da, zieht `up` nichts mehr.
Say "Hole das fertige Image (falls vorhanden) ..."
Compose pull api *> $null
if ($LASTEXITCODE -ne 0) {
    Say "Kein fertiges Image erreichbar -- baue selbst. Beim ersten Mal 5-15 Minuten, je nach Leitung."
    Compose build api
    if ($LASTEXITCODE -ne 0) { Bad "Bau fehlgeschlagen. Die Meldung oben sagt meist, warum."; exit 1 }
}
Say "Starte ..."
Compose up -d --no-build
if ($LASTEXITCODE -ne 0) {
    Bad "Start fehlgeschlagen. Die Meldung oben sagt meist, warum."
    exit 1
}
Write-Host -NoNewline "  Warte, bis PhotoVault bereit ist "
if (-not (Wait-Api $url 900 "Beim ersten Mal laedt PhotoVault rund 1,5 GB Modelle -- das dauert.")) {
    Bad "PhotoVault antwortet nicht auf $url. Protokoll: docker compose logs api"
    exit 1
}
Ok "Laeuft: $url"
Start-Process $url | Out-Null

# --- Zweite Phase: nach der Ordnerwahl beschreibbar einbinden --------------
$step = Get-SetupStep $url
if ($step -in @("sources-done", "done")) {
    Say "Eingerichtet. Dieses Fenster kann zu."
    exit 0
}
Write-Host ""
Say "Im Browser geht es weiter: Ordner waehlen. Dieses Fenster wartet darauf"
Say "und bindet die gewaehlten Ordner dann beschreibbar ein -- bitte offen lassen."
$t0 = Get-Date
while (((Get-Date) - $t0).TotalMinutes -lt 120) {
    Start-Sleep -Seconds 3
    $step = Get-SetupStep $url
    if ($step -in @("sources-done", "done")) { break }
    if ($step -eq "nas-added") {
        # Eine Freigabe wurde eingetragen: die Volume-Datei des Servers
        # aufnehmen und den Container neu erstellen. Die Seite setzt den
        # Schritt danach zurueck; solange er steht, nicht noch einmal starten.
        Ok "Freigabe einbinden ..."
        Set-ComposeFiles
        Compose up -d
        Write-Host -NoNewline "  Warte, bis PhotoVault wieder da ist "
        if (-not (Wait-Api $url 300 "")) { Bad "PhotoVault kommt nicht hoch. Protokoll: docker compose logs api"; exit 1 }
        $t1 = Get-Date
        while ((Get-SetupStep $url) -eq "nas-added" -and ((Get-Date) - $t1).TotalMinutes -lt 5) { Start-Sleep -Seconds 2 }
    }
}
if ($step -notin @("sources-done", "done")) {
    Warn "Zwei Stunden ohne Ordnerwahl -- beim naechsten start.bat werden gewaehlte Ordner beschreibbar."
    exit 0
}
$chosen = Write-Override $wantGpu $useGpu
Set-ComposeFiles
if ($chosen.Count) { Ok ("Beschreibbar einbinden: " + (($chosen | ForEach-Object { $_.Host }) -join ", ")) }
elseif (Test-Path -LiteralPath $NasFile) { Ok "Beschreibbar einbinden: gewaehlte Ordner auf Freigaben (data/nas-volumes.yml)" }
else {
    Warn "Keine Ordner in data/sources.txt gefunden, die auf ein Laufwerk oder eine Freigabe zeigen -- nichts einzubinden."
    exit 0
}
Compose up -d
Write-Host -NoNewline "  Warte, bis PhotoVault wieder da ist "
if (-not (Wait-Api $url 300 "")) {
    Bad "PhotoVault kommt nach dem Neustart nicht hoch. Protokoll: docker compose logs api"
    exit 1
}
Ok "Fertig -- weiter im Browser. Dieses Fenster kann zu."
exit 0
