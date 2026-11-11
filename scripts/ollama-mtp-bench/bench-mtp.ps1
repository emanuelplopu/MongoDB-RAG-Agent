param(
    [Parameter(Mandatory)][string[]]$Models,
    [Parameter(Mandatory)][string[]]$Prompts,
    [int]$Reps = 2,
    [int]$NumPredict = 512,
    [int]$NumCtx = 16384,
    [string]$Base = 'http://127.0.0.1:11434',
    [string]$OutFile = (Join-Path $PSScriptRoot ("results-{0:yyyyMMdd-HHmmss}.jsonl" -f (Get-Date)))
)
# Decode/prefill benchmark through Ollama's /api/generate.
# Order: for each prompt -> for each model -> reps. Rep 1 of a model on a prompt is a cold
# prefill (model switch drops the prompt cache); later reps mostly measure decode.
# Greedy sampling (temperature 0, fixed seed) so outputs with and without a drafter can be
# compared byte for byte via out_sha.
$ErrorActionPreference = 'Stop'

$script:thinkCaps = @{}
function Supports-Think($model) {
    if (-not $script:thinkCaps.ContainsKey($model)) {
        $show = Invoke-RestMethod -Uri "$Base/api/show" -Method Post -Body (@{ model = $model } | ConvertTo-Json -Compress) -ContentType 'application/json' -TimeoutSec 60
        $script:thinkCaps[$model] = [bool]($show.capabilities -contains 'thinking')
        "{0}: {1} {2} draft={3} capabilities={4}" -f $model, $show.details.parameter_size, $show.details.quantization_level, [bool]$show.model_info.'general.draft', ($show.capabilities -join ',')
    }
    return $script:thinkCaps[$model]
}
function Invoke-Gen($model, $prompt, $keep) {
    $req = @{
        model = $model; prompt = $prompt; stream = $false; keep_alive = $keep
        options = @{ num_ctx = $NumCtx; num_predict = $NumPredict; temperature = 0; seed = 42 }
    }
    if (Supports-Think $model) { $req.think = $false }
    $body = $req | ConvertTo-Json -Depth 5 -Compress
    $bytes = [Text.Encoding]::UTF8.GetBytes($body)
    Invoke-RestMethod -Uri "$Base/api/generate" -Method Post -Body $bytes -ContentType 'application/json; charset=utf-8' -TimeoutSec 900
}
function Unload($model) {
    try {
        $b = @{ model = $model; keep_alive = 0 } | ConvertTo-Json -Compress
        Invoke-RestMethod -Uri "$Base/api/generate" -Method Post -Body $b -ContentType 'application/json' -TimeoutSec 120 | Out-Null
    } catch { Write-Warning "unload $model failed: $($_.Exception.Message)" }
}
function Sha($s) {
    $h = [Security.Cryptography.SHA256]::Create()
    ([BitConverter]::ToString($h.ComputeHash([Text.Encoding]::UTF8.GetBytes($s))) -replace '-', '').Substring(0, 12)
}
function GpuNow() {
    try { ((nvidia-smi --query-gpu=temperature.gpu,power.draw,memory.used --format=csv,noheader,nounits) -split ',\s*') } catch { @('?', '?', '?') }
}

foreach ($p in $Prompts) {
    $ptext = [IO.File]::ReadAllText($p, [Text.Encoding]::UTF8)
    foreach ($m in $Models) {
        for ($r = 1; $r -le $Reps; $r++) {
            $g0 = GpuNow
            $t0 = Get-Date
            $res = Invoke-Gen $m $ptext '10m'
            $wall = ((Get-Date) - $t0).TotalSeconds
            $ps = (Invoke-RestMethod -Uri "$Base/api/ps" -TimeoutSec 30).models | Where-Object { $_.name -eq $m -or $_.model -eq $m } | Select-Object -First 1
            $g1 = GpuNow
            $head = ($res.response -replace '\s+', ' ')
            $head = $head.Substring(0, [Math]::Min(110, $head.Length))
            $row = [ordered]@{
                ts = (Get-Date).ToString('s'); model = $m; prompt = (Split-Path $p -Leaf); rep = $r
                prompt_tokens = $res.prompt_eval_count
                prefill_s = [math]::Round($res.prompt_eval_duration / 1e9, 2)
                prefill_tps = [math]::Round($res.prompt_eval_count / [Math]::Max($res.prompt_eval_duration / 1e9, 1e-9), 0)
                gen_tokens = $res.eval_count
                decode_s = [math]::Round($res.eval_duration / 1e9, 2)
                decode_tps = [math]::Round($res.eval_count / [Math]::Max($res.eval_duration / 1e9, 1e-9), 1)
                load_s = [math]::Round($res.load_duration / 1e9, 2)
                total_s = [math]::Round($res.total_duration / 1e9, 2)
                wall_s = [math]::Round($wall, 2)
                done_reason = $res.done_reason
                out_sha = (Sha $res.response)
                out_head = $head
                vram_model = $ps.size_vram; size_model = $ps.size
                gpu_temp_before = $g0[0]; gpu_temp_after = $g1[0]; gpu_power_w = $g1[1]; gpu_mem_used_mib = $g1[2]
            }
            ([pscustomobject]$row | ConvertTo-Json -Compress) | Add-Content -Path $OutFile -Encoding UTF8
            "{0,-18} {1,-24} rep{2}  prompt {3,6} tok  prefill {4,5} tok/s  decode {5,5} tok/s ({6} tok in {7}s)  load {8}s  vram {9:N1} GB  {10}" -f $m, $row.prompt, $r, $row.prompt_tokens, $row.prefill_tps, $row.decode_tps, $row.gen_tokens, $row.decode_s, $row.load_s, ($row.vram_model / 1e9), $row.out_sha
        }
        Unload $m
    }
}
"results: $OutFile"
