<#
    clamav-local.ps1 - סורק ClamAV מקומי לפיתוח, בלי התקנה ובלי הרשאות מנהל.

        .\scripts\clamav-local.ps1 setup     הורדה ופריסה (פעם אחת, ~225MB) + חתימות (~300MB)
        .\scripts\clamav-local.ps1 update    עדכון חתימות (freshclam)
        .\scripts\clamav-local.ps1 start     clamd על 127.0.0.1:3310
        .\scripts\clamav-local.ps1 stop
        .\scripts\clamav-local.ps1 status

    ואז ב-.env:  PORTAL_CLAMD_ADDR=tcp://127.0.0.1:3310

    למה מחוץ לתיקיית הפרויקט - מאותה סיבה כמו pg-local.ps1: נתיב
    עברי. הכול תחת %LOCALAPPDATA%\nahmani-clamav.

    בייצור (Linux) זה לא רץ כך: שם clamd הוא קונטיינר
    clamav/clamav - ראה deploy/docker-compose.yml.
#>

param(
    [Parameter(Position = 0)]
    [ValidateSet('setup', 'update', 'start', 'stop', 'status')]
    [string]$Command = 'status'
)

$ErrorActionPreference = 'Stop'

$Version = '1.5.4'
$Root    = Join-Path $env:LOCALAPPDATA 'nahmani-clamav'
$Bin     = Join-Path $Root "clamav-$Version.win.x64"
$Db      = Join-Path $Root 'db'
$Logs    = Join-Path $Root 'logs'
$Url     = "https://github.com/Cisco-Talos/clamav/releases/download/clamav-$Version/clamav-$Version.win.x64.zip"
$Port    = 3310

function Write-Configs {
    New-Item -ItemType Directory -Force -Path $Db, $Logs | Out-Null
    @"
DatabaseDirectory "$Db"
DatabaseMirror database.clamav.net
UpdateLogFile "$Logs\freshclam.log"
"@ | Set-Content -Encoding ascii (Join-Path $Root 'freshclam.conf')

    # StreamMaxLength מעל תקרת ההעלאה (12MB). מאזין על loopback בלבד.
    @"
DatabaseDirectory "$Db"
TCPSocket $Port
TCPAddr 127.0.0.1
LogFile "$Logs\clamd.log"
LogTime yes
StreamMaxLength 20M
MaxScanSize 100M
MaxFileSize 25M
"@ | Set-Content -Encoding ascii (Join-Path $Root 'clamd.conf')

    # חתימת בדיקה לפיתוח בלבד. EICAR מזוהה רק בתחילת קובץ, וההעלאה
    # דורשת כותרת PDF/JPG/PNG - כך שאי אפשר לבדוק איתו את הזרימה
    # המלאה. החתימה הזו מזהה מחרוזת סינתטית בכל מקום בקובץ.
    # בייצור היא לא קיימת.
    $marker = 'NB-SYNTHETIC-MALWARE-MARKER-FOR-TESTS-ONLY'
    $hex = -join ([Text.Encoding]::ASCII.GetBytes($marker) | ForEach-Object { $_.ToString('x2') })
    "NahmaniBendahan.Test.SyntheticMarker:0:*:$hex" |
        Set-Content -Encoding ascii (Join-Path $Db 'nahmani-test.ndb')
}

function Assert-Installed {
    if (-not (Test-Path (Join-Path $Bin 'clamd.exe'))) {
        throw "ClamAV לא נמצא ב-$Root. הריצו תחילה: .\scripts\clamav-local.ps1 setup"
    }
}

function Get-Clamd {
    Get-Process clamd -ErrorAction SilentlyContinue |
        Where-Object { $_.Path -like "$Bin*" }
}

switch ($Command) {

    'setup' {
        New-Item -ItemType Directory -Force -Path $Root | Out-Null
        $zip = Join-Path $Root 'clamav.zip'
        if (-not (Test-Path (Join-Path $Bin 'clamd.exe'))) {
            if (-not (Test-Path $zip)) {
                Write-Host "מוריד ClamAV $Version (~225MB)..."
                Invoke-WebRequest -Uri $Url -OutFile $zip
            }
            Write-Host "פורס..."
            Expand-Archive -Path $zip -DestinationPath $Root -Force
        }
        Write-Configs
        Write-Host "מוריד חתימות (~300MB, כמה דקות)..."
        & (Join-Path $Bin 'freshclam.exe') --config-file (Join-Path $Root 'freshclam.conf')
        Write-Host "מוכן. להפעלה: .\scripts\clamav-local.ps1 start"
    }

    'update' {
        Assert-Installed
        & (Join-Path $Bin 'freshclam.exe') --config-file (Join-Path $Root 'freshclam.conf')
    }

    'start' {
        Assert-Installed
        if (Get-Clamd) { Write-Host "clamd כבר רץ."; break }
        Start-Process -FilePath (Join-Path $Bin 'clamd.exe') `
            -ArgumentList '--config-file', "`"$(Join-Path $Root 'clamd.conf')`"" `
            -WindowStyle Hidden
        Write-Host "clamd עולה (טעינת החתימות לוקחת 20-60 שניות)."
        Write-Host "PORTAL_CLAMD_ADDR=tcp://127.0.0.1:$Port"
    }

    'stop' {
        $p = Get-Clamd
        if ($p) { $p | Stop-Process; Write-Host "clamd נעצר." } else { Write-Host "clamd לא רץ." }
    }

    'status' {
        if (-not (Test-Path (Join-Path $Bin 'clamd.exe'))) {
            Write-Host "לא מותקן. הריצו: .\scripts\clamav-local.ps1 setup"; break
        }
        if (Get-Clamd) { Write-Host "clamd רץ על 127.0.0.1:$Port" } else { Write-Host "clamd לא רץ." }
    }
}
