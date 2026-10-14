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
