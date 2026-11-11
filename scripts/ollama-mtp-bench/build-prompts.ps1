param(
    [string]$Corpus = 'C:\RecallHub_Synthetic\kanzlei_v1\written',
    [string]$OutDir = (Join-Path $PSScriptRoot 'prompts'),
    [int]$TargetChars = 34000,
    [int]$Count = 3
)
# Builds RAG-sized German prompts (about 9-10k Gemma tokens each) from the synthetic
# law-firm corpus: the largest written documents of the first $Count practice areas,
# followed by a structured-answer instruction. Output is UTF-8 without BOM.
$ErrorActionPreference = 'Stop'
New-Item -ItemType Directory -Force -Path $OutDir | Out-Null
$areas = Get-ChildItem $Corpus -Directory | Sort-Object Name | Select-Object -First $Count

$question = @"

---

Du bist die juristische Assistenz einer oesterreichischen Rechtsanwaltskanzlei. Beantworte auf Basis der obigen Dokumente ausfuehrlich und strukturiert auf Deutsch:
1. Fasse den Sachverhalt jedes Dokuments in je einem Absatz zusammen.
2. Liste alle Parteien, Geschaeftszahlen, Fristen, Termine und Geldbetraege tabellarisch auf.
3. Nenne die offenen rechtlichen Fragen und die naechsten empfohlenen Schritte fuer die Kanzlei.
"@

$i = 0
foreach ($a in $areas) {
    $i++
    $docs = Get-ChildItem $a.FullName -Filter 'D*.json' | Sort-Object Length -Descending
    $sb = New-Object System.Text.StringBuilder
    $used = @()
    foreach ($d in $docs) {
        if ($sb.Length -ge $TargetChars) { break }
        $j = Get-Content $d.FullName -Raw -Encoding UTF8 | ConvertFrom-Json
        $draft = $j.draft
        if (-not $draft -or -not $draft.body_markdown) { continue }
        [void]$sb.AppendLine("=== Dokument $($j.doc_id): $($draft.title) ===")
        if ($draft.header_lines) { [void]$sb.AppendLine(($draft.header_lines -join "`n")) }
        [void]$sb.AppendLine()
        [void]$sb.AppendLine($draft.body_markdown)
        if ($draft.closing_lines) { [void]$sb.AppendLine(($draft.closing_lines -join "`n")) }
        [void]$sb.AppendLine()
        $used += $d.Name
    }
    $text = $sb.ToString()
    if ($text.Length -gt $TargetChars) { $text = $text.Substring(0, $TargetChars) }
    $out = Join-Path $OutDir ("prompt-{0}-{1}.txt" -f $i, $a.Name)
    [IO.File]::WriteAllText($out, ($text + $question), (New-Object System.Text.UTF8Encoding $false))
    "{0}: {1:N0} chars, docs {2}" -f (Split-Path $out -Leaf), ($text.Length + $question.Length), ($used -join ' ')
}
