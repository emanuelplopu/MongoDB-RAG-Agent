param(
    [Parameter(Mandatory)][string[]]$Files
)
# Summarizes bench-mtp.ps1 JSONL rows: per model, median decode tok/s (all reps),
# median cold prefill tok/s (rep 1 only), load time, VRAM, and whether the greedy
# outputs of the drafter and no-drafter runs agree per prompt and rep.
$ErrorActionPreference = 'Stop'
$rows = @()
foreach ($f in $Files) { $rows += Get-Content $f -Encoding UTF8 | Where-Object { $_.Trim() } | ForEach-Object { $_ | ConvertFrom-Json } }
function Median($xs) { $s = @($xs | Sort-Object); if ($s.Count -eq 0) { return $null }; $m = [int][Math]::Floor($s.Count / 2); if ($s.Count % 2) { $s[$m] } else { [math]::Round(($s[$m - 1] + $s[$m]) / 2, 1) } }

"{0,-20} {1,5} {2,9} {3,9} {4,9} {5,9} {6,7} {7,8} {8,9} {9,7}" -f 'model', 'runs', 'decode', 'dec_min', 'dec_max', 'prefill1', 'load1', 'ps_vram', 'smi_MiB', 'power'
foreach ($g in ($rows | Group-Object model)) {
    $dec = $g.Group | ForEach-Object { $_.decode_tps }
    $pre = $g.Group | Where-Object { $_.rep -eq 1 } | ForEach-Object { $_.prefill_tps }
    $load = $g.Group | Where-Object { $_.rep -eq 1 } | ForEach-Object { $_.load_s }
    $vram = ($g.Group | ForEach-Object { $_.vram_model } | Measure-Object -Maximum).Maximum / 1e9
    $smi = ($g.Group | ForEach-Object { [double]$_.gpu_mem_used_mib } | Measure-Object -Maximum).Maximum
    $pw = Median ($g.Group | ForEach-Object { [double]$_.gpu_power_w })
    "{0,-20} {1,5} {2,9} {3,9} {4,9} {5,9} {6,7} {7,8:N1} {8,9:N0} {9,7:N0}" -f $g.Name, $g.Count, (Median $dec), ($dec | Measure-Object -Minimum).Minimum, ($dec | Measure-Object -Maximum).Maximum, (Median $pre), (Median $load), $vram, $smi, $pw
}
""
"per run (file order):"
$rows | ForEach-Object { "{0,-20} {1,-28} rep{2} prompt {3,5} prefill {4,6} decode {5,5} tok/s gen {6} done={7} {8} temp {9}->{10} C  {11} W" -f $_.model, $_.prompt, $_.rep, $_.prompt_tokens, $_.prefill_tps, $_.decode_tps, $_.gen_tokens, $_.done_reason, $_.out_sha, $_.gpu_temp_before, $_.gpu_temp_after, $_.gpu_power_w }
""
"output agreement (same prompt and rep):"
foreach ($p in ($rows | Group-Object prompt)) {
    foreach ($r in 1, 2) {
        $byModel = @{}
        foreach ($x in ($p.Group | Where-Object { $_.rep -eq $r })) { $byModel[$x.model] = $x.out_sha }
        $pairs = @()
        if ($byModel.ContainsKey('gemma4:12b') -and $byModel.ContainsKey('gemma4:12b-nodraft')) { $pairs += ('draft vs nodraft: ' + $(if ($byModel['gemma4:12b'] -eq $byModel['gemma4:12b-nodraft']) { 'identical' } else { 'DIFFERENT' })) }
        if ($byModel.ContainsKey('gemma4:12b-june') -and $byModel.ContainsKey('gemma4:12b-nodraft')) { $pairs += ('june vs new: ' + $(if ($byModel['gemma4:12b-june'] -eq $byModel['gemma4:12b-nodraft']) { 'identical' } else { 'different' })) }
        if ($pairs.Count) { "{0,-28} rep{1}  {2}" -f $p.Name, $r, ($pairs -join '; ') }
    }
}
""
"answer openings (rep 1):"
$rows | Where-Object { $_.rep -eq 1 } | ForEach-Object { "{0,-20} {1,-28} {2}" -f $_.model, $_.prompt, $_.out_head }
