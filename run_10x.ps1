$ErrorActionPreference = "Stop"
$resultsDir = "F:\Projects\AI-Operator\10x_results"
New-Item -ItemType Directory -Path $resultsDir -Force | Out-Null

for ($i = 1; $i -le 10; $i++) {
    Write-Host "=== Run $i / 10 ==="
    $outputFile = Join-Path $resultsDir "run_${i}.txt"
    $startTime = Get-Date
    
    Set-Location "F:\Projects\AI-Operator\services\core"
    $env:PYTHONDONTWRITEBYTECODE = "1"
    
    $proc = Start-Process -FilePath "python" -ArgumentList "-m","pytest","tests/test_run_controller.py","-v","--tb=short" -NoNewWindow -Wait -PassThru -RedirectStandardOutput $outputFile -RedirectStandardError "$outputFile.err"
    
    $endTime = Get-Date
    $duration = $endTime - $startTime
    $exitCode = $proc.ExitCode
    
    Add-Content -Path $outputFile -Value "`nEXIT_CODE: $exitCode"
    Add-Content -Path $outputFile -Value "DURATION: $($duration.TotalSeconds)s"
    
    Write-Host "Run $i completed with exit code $exitCode in $($duration.TotalSeconds)s"
    
    if ($exitCode -ne 0) {
        Write-Host "FAILURE detected in run $i. Stopping."
        exit 1
    }
}
Write-Host "All 10 runs completed successfully."
