$b = "$env:APPDATA\Code\User\History"
$modules = @{
    "-26b1079d" = "no_supply_demand_detector.py"
    "-4174b350" = "market_env_detector.py"
    "-4bab1f2e" = "pnf_target_calculator.py"
    "-580da0f3" = "sow_bar_detector.py"
    "-74042da7" = "climatic_action_detector.py"
    "51f781c0" = "composite_man_detector.py"
    "615764a5" = "significant_bar_detector.py"
    "683c6b89" = "phase_context_detector.py"
    "6e9c4ab1" = "phase_d_entry_detector.py"
    "72e725e7" = "creek_jac_detector.py"
    "7dc183" = "event_sequence_state_machine.py"
    "fcae312" = "daily_event_detector.py"
}
$dest = "e:\freqtrade\wyckoff"
foreach ($kv in $modules.GetEnumerator()) {
    $dir = "$b\$($kv.Key)"
    $latest = Get-ChildItem $dir -Filter "*.py" | Sort-Object LastWriteTime -Descending | Select-Object -First 1
    if ($latest) {
        $target = "$dest\$($kv.Value)"
        Copy-Item $latest.FullName $target -Force
        Write-Host "OK: $($kv.Value) <- $($latest.Name) ($($latest.LastWriteTime))"
    } else {
        Write-Host "MISS: $($kv.Value)"
    }
}
Write-Host "Done!"
