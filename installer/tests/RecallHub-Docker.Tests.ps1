<#
.SYNOPSIS
    Pester 5+ tests for installer/scripts/RecallHub-Docker.psm1.
.DESCRIPTION
    Windows ships Pester 3.4 only; install a current one once per user:
        Install-Module Pester -Scope CurrentUser -Force -SkipPublisherCheck
    Run from the repo root:
        Invoke-Pester -Path installer/tests -Output Detailed
#>
#Requires -Modules @{ ModuleName = 'Pester'; ModuleVersion = '5.0.0' }

BeforeAll {
    Import-Module (Join-Path $PSScriptRoot '..\scripts\RecallHub-Docker.psm1') -Force
}

Describe 'Confirm-DockerEngine' {
    BeforeEach {
        $global:RhTestLog = [System.Collections.Generic.List[string]]::new()
        $global:RhEngineProbes = 0
        $log = { param([string]$Message, [string]$Level) $global:RhTestLog.Add("[$Level] $Message") }

        Mock -ModuleName RecallHub-Docker Start-Sleep { }
        Mock -ModuleName RecallHub-Docker Start-Process { }
        Mock -ModuleName RecallHub-Docker Test-Path { $true }
        Mock -ModuleName RecallHub-Docker Test-DockerDesktopCli { $true }
        Mock -ModuleName RecallHub-Docker Invoke-DockerDesktopStartCli { $true }
        Mock -ModuleName RecallHub-Docker Test-DockerDesktopProcess { $false }
        # Engine answers on the Nth probe; tests override $global:RhEngineReadyAt.
        $global:RhEngineReadyAt = 2
        Mock -ModuleName RecallHub-Docker Test-DockerEngine {
            $global:RhEngineProbes++
            return ($global:RhEngineProbes -ge $global:RhEngineReadyAt)
        }
    }

    AfterAll {
        Remove-Variable -Name RhTestLog, RhEngineProbes, RhEngineReadyAt -Scope Global -ErrorAction SilentlyContinue
    }

    It 'reports ready and launches nothing when the engine already answers' {
        $global:RhEngineReadyAt = 1

        $result = Confirm-DockerEngine -TimeoutSeconds 9 -Log $log

        $result.Ready | Should -BeTrue
        $result.Action | Should -Be 'ready'
        Should -Invoke -ModuleName RecallHub-Docker Invoke-DockerDesktopStartCli -Times 0 -Exactly
        Should -Invoke -ModuleName RecallHub-Docker Start-Process -Times 0 -Exactly
    }

    It 'waits for a Docker Desktop that is still booting instead of relaunching it' {
        Mock -ModuleName RecallHub-Docker Test-DockerDesktopProcess { $true }
        $global:RhEngineReadyAt = 3

        $result = Confirm-DockerEngine -TimeoutSeconds 30 -PollSeconds 3 -Log $log

        $result.Ready | Should -BeTrue
        $result.Action | Should -Be 'waited'
        $result.Seconds | Should -Be 6
        Should -Invoke -ModuleName RecallHub-Docker Invoke-DockerDesktopStartCli -Times 0 -Exactly
        Should -Invoke -ModuleName RecallHub-Docker Start-Process -Times 0 -Exactly
        ($global:RhTestLog -join "`n") | Should -Match 'not relaunching'
    }

    It 'gives up with a timeout, still without relaunching, when a running Docker Desktop never answers' {
        Mock -ModuleName RecallHub-Docker Test-DockerDesktopProcess { $true }
        $global:RhEngineReadyAt = 1000

        $result = Confirm-DockerEngine -TimeoutSeconds 9 -PollSeconds 3 -Log $log

        $result.Ready | Should -BeFalse
        $result.Action | Should -Be 'timeout'
        Should -Invoke -ModuleName RecallHub-Docker Start-Sleep -Times 3 -Exactly
        Should -Invoke -ModuleName RecallHub-Docker Invoke-DockerDesktopStartCli -Times 0 -Exactly
        Should -Invoke -ModuleName RecallHub-Docker Start-Process -Times 0 -Exactly
    }

    It 'starts Docker Desktop through the Docker Desktop CLI when no process exists' {
        $result = Confirm-DockerEngine -TimeoutSeconds 30 -Log $log

        $result.Ready | Should -BeTrue
        $result.Action | Should -Be 'started-cli'
        Should -Invoke -ModuleName RecallHub-Docker Invoke-DockerDesktopStartCli -Times 1 -Exactly
        Should -Invoke -ModuleName RecallHub-Docker Start-Process -Times 0 -Exactly
    }

    It 'falls back to the executable, minimized, when the Docker Desktop CLI is missing' {
        Mock -ModuleName RecallHub-Docker Test-DockerDesktopCli { $false }

        $result = Confirm-DockerEngine -DesktopPath 'C:\dd\Docker Desktop.exe' -TimeoutSeconds 30 -Log $log

        $result.Ready | Should -BeTrue
        $result.Action | Should -Be 'started-exe'
        Should -Invoke -ModuleName RecallHub-Docker Invoke-DockerDesktopStartCli -Times 0 -Exactly
        Should -Invoke -ModuleName RecallHub-Docker Start-Process -Times 1 -Exactly -ParameterFilter {
            $FilePath -eq 'C:\dd\Docker Desktop.exe' -and "$WindowStyle" -eq 'Minimized'
        }
    }

    It 'falls back to the executable when docker desktop start fails' {
        Mock -ModuleName RecallHub-Docker Invoke-DockerDesktopStartCli { $false }

        $result = Confirm-DockerEngine -TimeoutSeconds 30 -Log $log

        $result.Action | Should -Be 'started-exe'
        Should -Invoke -ModuleName RecallHub-Docker Start-Process -Times 1 -Exactly
        ($global:RhTestLog -join "`n") | Should -Match 'falling back'
    }

    It 'reports not-installed when neither the CLI nor the executable is available' {
        Mock -ModuleName RecallHub-Docker Test-DockerDesktopCli { $false }
        Mock -ModuleName RecallHub-Docker Test-Path { $false }
        $global:RhEngineReadyAt = 1000

        $result = Confirm-DockerEngine -DesktopPath 'C:\missing\Docker Desktop.exe' -TimeoutSeconds 9 -Log $log

        $result.Ready | Should -BeFalse
        $result.Action | Should -Be 'not-installed'
        Should -Invoke -ModuleName RecallHub-Docker Start-Process -Times 0 -Exactly
        Should -Invoke -ModuleName RecallHub-Docker Start-Sleep -Times 0 -Exactly
    }

    It 'launches nothing with -NoLaunch' {
        $global:RhEngineReadyAt = 1000

        $result = Confirm-DockerEngine -NoLaunch -TimeoutSeconds 9 -Log $log

        $result.Ready | Should -BeFalse
        $result.Action | Should -Be 'disabled'
        Should -Invoke -ModuleName RecallHub-Docker Invoke-DockerDesktopStartCli -Times 0 -Exactly
        Should -Invoke -ModuleName RecallHub-Docker Start-Process -Times 0 -Exactly
    }
}

Describe 'Wait-DockerEngine' {
    BeforeEach {
        $global:RhEngineProbes = 0
        Mock -ModuleName RecallHub-Docker Start-Sleep { }
    }

    AfterAll {
        Remove-Variable -Name RhEngineProbes -Scope Global -ErrorAction SilentlyContinue
    }

    It 'returns the seconds waited once the engine answers' {
        Mock -ModuleName RecallHub-Docker Test-DockerEngine { $global:RhEngineProbes++; return ($global:RhEngineProbes -ge 2) }

        Wait-DockerEngine -TimeoutSeconds 30 -PollSeconds 3 | Should -Be 6
        Should -Invoke -ModuleName RecallHub-Docker Start-Sleep -Times 2 -Exactly -ParameterFilter { $Seconds -eq 3 }
    }

    It 'returns -1 when the timeout elapses' {
        Mock -ModuleName RecallHub-Docker Test-DockerEngine { $false }

        Wait-DockerEngine -TimeoutSeconds 9 -PollSeconds 3 | Should -Be -1
        Should -Invoke -ModuleName RecallHub-Docker Start-Sleep -Times 3 -Exactly
    }
}

Describe 'Get-ComposeFileSet' {
    # An explicit -f list makes Compose ignore COMPOSE_FILE from .env, so the
    # service scripts read it themselves. The tray app mirrors this rule
    # (ServiceManager.ResolveComposeFiles, installer/tray-app.Tests).
    BeforeEach {
        $install = Join-Path $TestDrive ('install-' + [guid]::NewGuid().ToString('N').Substring(0, 8))
        New-Item -ItemType Directory -Path $install | Out-Null
        foreach ($name in 'docker-compose.yml', 'docker-compose.override.yml', 'docker-compose.shares.yml', 'docker-compose.gpu.yml') {
            Set-Content -Path (Join-Path $install $name) -Value 'services: {}' -Encoding ascii
        }
        $global:RhComposeLog = [System.Collections.Generic.List[string]]::new()
        $log = { param([string]$Message, [string]$Level) $global:RhComposeLog.Add("[$Level] $Message") }
        # The process environment overrides .env (as for Compose itself); keep the
        # developer's shell out of the tests.
        $global:RhSavedComposeFile = $env:COMPOSE_FILE
        $global:RhSavedSeparator = $env:COMPOSE_PATH_SEPARATOR
        $env:COMPOSE_FILE = $null
        $env:COMPOSE_PATH_SEPARATOR = $null
    }

    AfterEach {
        $env:COMPOSE_FILE = $global:RhSavedComposeFile
        $env:COMPOSE_PATH_SEPARATOR = $global:RhSavedSeparator
    }

    AfterAll {
        Remove-Variable -Name RhComposeLog, RhSavedComposeFile, RhSavedSeparator -Scope Global -ErrorAction SilentlyContinue
    }

    It 'uses the compose file plus override and shares when .env has no COMPOSE_FILE' {
        Set-Content -Path (Join-Path $install '.env') -Value 'COMPOSE_PROFILES=quellex' -Encoding ascii

        $result = Get-ComposeFileSet -InstallPath $install -Log $log

        $result.Source | Should -Be 'default'
        $result.Files | Should -Be @(
            (Join-Path $install 'docker-compose.yml'),
            (Join-Path $install 'docker-compose.override.yml'),
            (Join-Path $install 'docker-compose.shares.yml'))
        $global:RhComposeLog.Count | Should -Be 0
    }

    It 'leaves out an override or shares file that does not exist' {
        Remove-Item (Join-Path $install 'docker-compose.shares.yml')

        $result = Get-ComposeFileSet -InstallPath $install -Log $log

        $result.Files | Should -Be @((Join-Path $install 'docker-compose.yml'), (Join-Path $install 'docker-compose.override.yml'))
    }

    It 'passes every file COMPOSE_FILE lists, in that order' {
        Set-Content -Path (Join-Path $install '.env') -Value @(
            'COMPOSE_PROFILES=quellex',
            'COMPOSE_FILE=docker-compose.yml;docker-compose.shares.yml;docker-compose.override.yml;docker-compose.gpu.yml') -Encoding ascii

        $result = Get-ComposeFileSet -InstallPath $install -Log $log

        $result.Source | Should -Be 'COMPOSE_FILE'
        $result.Files | Should -Be @(
            (Join-Path $install 'docker-compose.yml'),
            (Join-Path $install 'docker-compose.shares.yml'),
            (Join-Path $install 'docker-compose.override.yml'),
            (Join-Path $install 'docker-compose.gpu.yml'))
        $global:RhComposeLog.Count | Should -Be 0
    }

    It 'logs and skips a listed file that does not exist' {
        Set-Content -Path (Join-Path $install '.env') -Value 'COMPOSE_FILE=docker-compose.yml;docker-compose.gpu.yml;docker-compose.missing.yml' -Encoding ascii

        $result = Get-ComposeFileSet -InstallPath $install -Log $log

        $result.Files | Should -Be @((Join-Path $install 'docker-compose.yml'), (Join-Path $install 'docker-compose.gpu.yml'))
        $global:RhComposeLog.Count | Should -Be 1
        $global:RhComposeLog[0] | Should -Match '^\[WARN\] COMPOSE_FILE lists docker-compose\.missing\.yml .* skipped$'
    }

    It 'falls back to the default set when no listed file exists' {
        Set-Content -Path (Join-Path $install '.env') -Value 'COMPOSE_FILE=nope.yml;nada.yml' -Encoding ascii

        $result = Get-ComposeFileSet -InstallPath $install -Log $log

        $result.Source | Should -Be 'default'
        $result.Files.Count | Should -Be 3
        ($global:RhComposeLog -join "`n") | Should -Match 'names no existing file - using the default compose file set'
    }

    It 'honours COMPOSE_PATH_SEPARATOR, an export prefix and quotes' {
        Set-Content -Path (Join-Path $install '.env') -Value @(
            'COMPOSE_PATH_SEPARATOR=:',
            'export COMPOSE_FILE="docker-compose.yml:docker-compose.gpu.yml"') -Encoding ascii

        $result = Get-ComposeFileSet -InstallPath $install -Log $log

        $result.Files | Should -Be @((Join-Path $install 'docker-compose.yml'), (Join-Path $install 'docker-compose.gpu.yml'))
    }

    It 'takes the last definition and ignores comment lines' {
        Set-Content -Path (Join-Path $install '.env') -Value @(
            '# COMPOSE_FILE=docker-compose.yml;docker-compose.override.yml',
            'COMPOSE_FILE=docker-compose.yml',
            'COMPOSE_FILE=docker-compose.yml;docker-compose.gpu.yml') -Encoding ascii

        $result = Get-ComposeFileSet -InstallPath $install -Log $log

        $result.Files | Should -Be @((Join-Path $install 'docker-compose.yml'), (Join-Path $install 'docker-compose.gpu.yml'))
    }

    It 'lets the process environment override .env' {
        Set-Content -Path (Join-Path $install '.env') -Value 'COMPOSE_FILE=docker-compose.yml;docker-compose.override.yml' -Encoding ascii
        $env:COMPOSE_FILE = 'docker-compose.yml;docker-compose.gpu.yml'

        $result = Get-ComposeFileSet -InstallPath $install -Log $log

        $result.Files | Should -Be @((Join-Path $install 'docker-compose.yml'), (Join-Path $install 'docker-compose.gpu.yml'))
    }

    It 'ignores COMPOSE_FILE and the extras for a compose file other than docker-compose.yml' {
        Set-Content -Path (Join-Path $install 'docker-compose.custom.yml') -Value 'services: {}' -Encoding ascii
        Set-Content -Path (Join-Path $install '.env') -Value 'COMPOSE_FILE=docker-compose.yml;docker-compose.gpu.yml' -Encoding ascii

        $result = Get-ComposeFileSet -InstallPath $install -ComposeFile 'docker-compose.custom.yml' -Log $log

        $result.Source | Should -Be 'default'
        $result.Files | Should -Be @((Join-Path $install 'docker-compose.custom.yml'))
        $global:RhComposeLog.Count | Should -Be 0
    }
}

Describe 'Get-DotEnvValue' {
    It 'returns $null without the file or the key' {
        Get-DotEnvValue -Path (Join-Path $TestDrive 'absent.env') -Key 'COMPOSE_FILE' | Should -BeNullOrEmpty
        Set-Content -Path (Join-Path $TestDrive 'a.env') -Value 'COMPOSE_PROFILES=quellex' -Encoding ascii
        Get-DotEnvValue -Path (Join-Path $TestDrive 'a.env') -Key 'COMPOSE_FILE' | Should -BeNullOrEmpty
        Get-DotEnvValue -Path (Join-Path $TestDrive 'a.env') -Key 'COMPOSE_PROFILES' | Should -Be 'quellex'
    }
}
