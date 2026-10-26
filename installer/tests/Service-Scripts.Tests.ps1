<#
.SYNOPSIS
    Pester 5+ tests for Start-Services.ps1 / Stop-Services.ps1 -DryRun.
.DESCRIPTION
    Builds a throwaway install folder (compose files, .env, config.json with
    installPath "auto" and the scripts plus their modules under scripts\, the
    hardened-installer layout), runs each script with -DryRun in a child
    powershell.exe (the scripts call exit) and checks the printed docker command:
    the -f list follows COMPOSE_FILE from .env when it is set, otherwise it is the
    compose file plus override and shares; the profiles from config.json and
    COMPOSE_PROFILES are merged either way. Logs go to a temporary LOCALAPPDATA.
    Run from the repo root:
        Invoke-Pester -Path installer/tests -Output Detailed
#>
#Requires -Modules @{ ModuleName = 'Pester'; ModuleVersion = '5.0.0' }

Describe '<_> -DryRun' -ForEach @('Start-Services.ps1', 'Stop-Services.ps1') {
    BeforeAll {
        $scriptName = $_
        $sourceDir = Join-Path $PSScriptRoot '..\scripts'

        function Invoke-ServiceScriptDryRun {
            param([string]$ScriptPath, [string]$LocalAppData)
            $saved = @{
                LOCALAPPDATA           = $env:LOCALAPPDATA
                COMPOSE_FILE           = $env:COMPOSE_FILE
                COMPOSE_PATH_SEPARATOR = $env:COMPOSE_PATH_SEPARATOR
            }
            try {
                $env:LOCALAPPDATA = $LocalAppData
                $env:COMPOSE_FILE = $null
                $env:COMPOSE_PATH_SEPARATOR = $null
                $lines = & powershell.exe -NoProfile -NonInteractive -ExecutionPolicy Bypass -File $ScriptPath -DryRun 2>&1 | ForEach-Object { "$_" }
                $exitCode = $LASTEXITCODE
            } finally {
                $env:LOCALAPPDATA = $saved.LOCALAPPDATA
                $env:COMPOSE_FILE = $saved.COMPOSE_FILE
                $env:COMPOSE_PATH_SEPARATOR = $saved.COMPOSE_PATH_SEPARATOR
            }
            return [pscustomobject]@{
                ExitCode = $exitCode
                Output   = ($lines -join "`n")
                Command  = [string]($lines | Where-Object { $_ -like 'DRY RUN: docker compose *' } | Select-Object -First 1)
            }
        }

        function Get-FileArguments {
            # "-f <a> -f <b>" as the scripts print it, for an ordered substring check.
            param([string]$Install, [string[]]$Names)
            return (($Names | ForEach-Object { '-f ' + (Join-Path $Install $_) }) -join ' ')
        }
    }

    BeforeEach {
        $install = Join-Path $TestDrive ('install-' + [guid]::NewGuid().ToString('N').Substring(0, 8))
        $scriptsDir = Join-Path $install 'scripts'
        $appData = Join-Path $install 'appdata'
        New-Item -ItemType Directory -Path $scriptsDir, $appData | Out-Null
        foreach ($f in 'Start-Services.ps1', 'Stop-Services.ps1', 'RecallHub-Logging.psm1', 'RecallHub-Docker.psm1') {
            Copy-Item (Join-Path $sourceDir $f) (Join-Path $scriptsDir $f)
        }
        foreach ($name in 'docker-compose.yml', 'docker-compose.override.yml', 'docker-compose.shares.yml', 'docker-compose.gpu.yml') {
            Set-Content -Path (Join-Path $install $name) -Value 'services: {}' -Encoding ascii
        }
        $config = @{ docker = @{ installPath = 'auto'; composeFile = 'docker-compose.yml'; composeProfile = 'quellex' } }
        Set-Content -Path (Join-Path $install 'config.json') -Value ($config | ConvertTo-Json -Depth 4) -Encoding ascii
        $script = Join-Path $scriptsDir $scriptName
    }

    It 'without COMPOSE_FILE passes the compose file, the override and the shares file' {
        Set-Content -Path (Join-Path $install '.env') -Value 'COMPOSE_PROFILES=embedder-gpu' -Encoding ascii

        $run = Invoke-ServiceScriptDryRun -ScriptPath $script -LocalAppData $appData

        $run.ExitCode | Should -Be 0 -Because $run.Output
        $run.Command | Should -Not -BeNullOrEmpty -Because $run.Output
        $run.Command | Should -BeLike ('*' + (Get-FileArguments $install 'docker-compose.yml', 'docker-compose.override.yml', 'docker-compose.shares.yml') + ' --profile quellex --profile embedder-gpu *')
        $run.Command | Should -Not -BeLike '*docker-compose.gpu.yml*'
    }

    It 'passes the files COMPOSE_FILE lists, in that order, and still merges the profiles' {
        Set-Content -Path (Join-Path $install '.env') -Value @(
            'COMPOSE_PROFILES=embedder-gpu',
            'COMPOSE_FILE=docker-compose.yml;docker-compose.shares.yml;docker-compose.override.yml;docker-compose.gpu.yml') -Encoding ascii

        $run = Invoke-ServiceScriptDryRun -ScriptPath $script -LocalAppData $appData

        $run.ExitCode | Should -Be 0 -Because $run.Output
        $run.Command | Should -BeLike ('*' + (Get-FileArguments $install 'docker-compose.yml', 'docker-compose.shares.yml', 'docker-compose.override.yml', 'docker-compose.gpu.yml') + ' --profile quellex --profile embedder-gpu *')
        $run.Output | Should -Match 'Compose files \(COMPOSE_FILE\)'
    }

    It 'skips a listed file that is missing, says so, and still exits 0' {
        Set-Content -Path (Join-Path $install '.env') -Value 'COMPOSE_FILE=docker-compose.yml;docker-compose.missing.yml;docker-compose.gpu.yml' -Encoding ascii

        $run = Invoke-ServiceScriptDryRun -ScriptPath $script -LocalAppData $appData

        $run.ExitCode | Should -Be 0 -Because $run.Output
        $run.Command | Should -BeLike ('*' + (Get-FileArguments $install 'docker-compose.yml', 'docker-compose.gpu.yml') + ' --profile quellex *')
        $run.Command | Should -Not -BeLike '*docker-compose.missing.yml*'
        $run.Output | Should -Match '\[WARN\].*COMPOSE_FILE lists docker-compose\.missing\.yml .* skipped'
    }

    It 'honours COMPOSE_PATH_SEPARATOR from .env' {
        Set-Content -Path (Join-Path $install '.env') -Value @(
            'COMPOSE_PATH_SEPARATOR=:',
            'COMPOSE_FILE=docker-compose.yml:docker-compose.gpu.yml') -Encoding ascii

        $run = Invoke-ServiceScriptDryRun -ScriptPath $script -LocalAppData $appData

        $run.ExitCode | Should -Be 0 -Because $run.Output
        $run.Command | Should -BeLike ('*' + (Get-FileArguments $install 'docker-compose.yml', 'docker-compose.gpu.yml') + ' --profile quellex *')
        $run.Command | Should -Not -BeLike '*docker-compose.override.yml*'
    }

    It 'keeps the single-file behaviour for a compose file other than docker-compose.yml' {
        Set-Content -Path (Join-Path $install 'docker-compose.custom.yml') -Value 'services: {}' -Encoding ascii
        $config = @{ docker = @{ installPath = 'auto'; composeFile = 'docker-compose.custom.yml'; composeProfile = 'quellex' } }
        Set-Content -Path (Join-Path $install 'config.json') -Value ($config | ConvertTo-Json -Depth 4) -Encoding ascii
        Set-Content -Path (Join-Path $install '.env') -Value 'COMPOSE_FILE=docker-compose.yml;docker-compose.gpu.yml' -Encoding ascii

        $run = Invoke-ServiceScriptDryRun -ScriptPath $script -LocalAppData $appData

        $run.ExitCode | Should -Be 0 -Because $run.Output
        $run.Command | Should -BeLike ('*compose ' + (Get-FileArguments $install 'docker-compose.custom.yml') + ' --profile quellex *')
        $run.Command | Should -Not -BeLike '*docker-compose.gpu.yml*'
        $run.Command | Should -Not -BeLike '*docker-compose.override.yml*'
    }

    It 'writes its log below the LOCALAPPDATA it was given, not the real one' {
        Set-Content -Path (Join-Path $install '.env') -Value 'COMPOSE_FILE=docker-compose.yml;docker-compose.gpu.yml' -Encoding ascii

        $run = Invoke-ServiceScriptDryRun -ScriptPath $script -LocalAppData $appData

        $run.ExitCode | Should -Be 0 -Because $run.Output
        $logs = @(Get-ChildItem (Join-Path $appData 'RecallHub\logs') -Filter '*.log')
        $logs.Count | Should -Be 1
        (Get-Content $logs[0].FullName -Raw) | Should -Match 'Compose files \(COMPOSE_FILE\)'
    }
}
