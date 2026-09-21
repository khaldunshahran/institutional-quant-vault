$destDirs = @([Environment]::GetFolderPath('Desktop'), "$env:USERPROFILE\Desktop") | Select-Object -Unique
$wsh = New-Object -ComObject WScript.Shell
$targetBat = "d:\5 minute btc\5min-btc-polymarket\run_dashboard.bat"
$workingDir = "d:\5 minute btc\5min-btc-polymarket"
$icon = "d:\5 minute btc\5min-btc-polymarket\.venv\Scripts\python.exe,0"

foreach ($dir in $destDirs) {
    if (Test-Path $dir) {
        $lnkPath = Join-Path $dir "BTC 5M Polymarket.lnk"
        $sc = $wsh.CreateShortcut($lnkPath)
        $sc.TargetPath = $targetBat
        $sc.WorkingDirectory = $workingDir
        $sc.Description = "Start BTC 5M Polymarket Dashboard"
        $sc.IconLocation = $icon
        $sc.Save()
        Write-Host "Created shortcut at: $lnkPath"
    }
}
