<#
    pg-local.ps1 - PostgreSQL מקומי לפיתוח, בלי התקנה.

        .\scripts\pg-local.ps1 setup    הורדה, פריסה ואתחול (פעם אחת, ~330MB)
        .\scripts\pg-local.ps1 start
        .\scripts\pg-local.ps1 stop
        .\scripts\pg-local.ps1 status
        .\scripts\pg-local.ps1 psql

    למה מחוץ לתיקיית הפרויקט:
    נתיב הפרויקט מכיל עברית, ו-PostgreSQL נכשל עליו - initdb
    מחזיר "invalid byte sequence for encoding UTF8" והשרת אינו
    מוצא את קבצי share. הבינאריים והנתונים יושבים לכן תחת
    %LOCALAPPDATA%\nahmani-pg, שהוא נתיב ASCII.

    זו התקנה לפיתוח בלבד: אימות trust, בלי סיסמה, מאזין על
    127.0.0.1 בלבד. אין להשתמש בה לנתונים אמיתיים.
#>

param(
    [Parameter(Position = 0)]
    [ValidateSet('setup', 'start', 'stop', 'status', 'psql')]
    [string]$Command = 'status'
)

$ErrorActionPreference = 'Stop'

$Root    = Join-Path $env:LOCALAPPDATA 'nahmani-pg'
$Bin     = Join-Path $Root 'pgsql\bin'
$Data    = Join-Path $Root 'data'
$LogFile = Join-Path $Root 'server.log'
$Port    = 55432
$DbName  = 'portal'
$PgUrl   = 'https://get.enterprisedb.com/postgresql/postgresql-17.6-1-windows-x64-binaries.zip'

function Assert-Installed {
    if (-not (Test-Path (Join-Path $Bin 'pg_ctl.exe'))) {
        throw "PostgreSQL לא נמצא ב-$Root. הריצו תחילה: .\scripts\pg-local.ps1 setup"
    }
}

switch ($Command) {

    'setup' {
        New-Item -ItemType Directory -Force -Path $Root | Out-Null
        $zip = Join-Path $Root 'pg.zip'

        if (-not (Test-Path (Join-Path $Bin 'pg_ctl.exe'))) {
            if (-not (Test-Path $zip)) {
                Write-Host "מוריד PostgreSQL (~330MB)..."
                Invoke-WebRequest -Uri $PgUrl -OutFile $zip
            }
            Write-Host "פורס... (23 אלף קבצים, לוקח כמה דקות)"
            Expand-Archive -Path $zip -DestinationPath $Root -Force
        }

        if (Test-Path $Data) {
            Write-Host "תיקיית נתונים כבר קיימת - מדלג על initdb."
        } else {
            Write-Host "מאתחל..."
            & (Join-Path $Bin 'initdb.exe') -D $Data -U postgres -A trust -E UTF8 --locale=C | Out-Null
        }
        Write-Host "מוכן. להפעלה: .\scripts\pg-local.ps1 start"
    }

    'start' {
        Assert-Installed
        & (Join-Path $Bin 'pg_ctl.exe') -D $Data `
            -o "-p $Port -c listen_addresses=127.0.0.1" -l $LogFile start
        Start-Sleep -Seconds 2

        # יצירת המסד בהרצה ראשונה בלבד
        $exists = & (Join-Path $Bin 'psql.exe') -h 127.0.0.1 -p $Port -U postgres -tAc `
            "SELECT 1 FROM pg_database WHERE datname='$DbName'"
        if (-not $exists) {
            & (Join-Path $Bin 'psql.exe') -h 127.0.0.1 -p $Port -U postgres -c `
                "CREATE DATABASE $DbName ENCODING 'UTF8' TEMPLATE template0" | Out-Null
            Write-Host "נוצר מסד '$DbName'."
        }
        Write-Host "רץ על 127.0.0.1:$Port  ->  postgresql://postgres@127.0.0.1:$Port/$DbName"
        Write-Host "להחלת הסכמה: python -m server.db.apply"
    }

    'stop' {
        Assert-Installed
        & (Join-Path $Bin 'pg_ctl.exe') -D $Data -m fast stop
    }

    'status' {
        if (-not (Test-Path (Join-Path $Bin 'pg_ctl.exe'))) {
            Write-Host "לא מותקן. הריצו: .\scripts\pg-local.ps1 setup"; break
        }
        & (Join-Path $Bin 'pg_ctl.exe') -D $Data status
    }

    'psql' {
        Assert-Installed
        $env:PGCLIENTENCODING = 'UTF8'
        & (Join-Path $Bin 'psql.exe') -h 127.0.0.1 -p $Port -U postgres -d $DbName
    }
}
