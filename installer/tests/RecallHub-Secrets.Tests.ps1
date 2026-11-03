<#
.SYNOPSIS
    Pester 5+ tests for installer/scripts/RecallHub-Secrets.psm1 and
    Initialize-JwtSecrets.ps1 (per-install login token signing keys).
.DESCRIPTION
    Windows ships Pester 3.4 only; install a current one once per user:
        Install-Module Pester -Scope CurrentUser -Force -SkipPublisherCheck
    Run from the repo root:
        Invoke-Pester -Path installer/tests -Output Detailed
#>
#Requires -Modules @{ ModuleName = 'Pester'; ModuleVersion = '5.0.0' }

BeforeAll {
    $script:ScriptsDir = Join-Path $PSScriptRoot '..\scripts'
    $script:RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path
    Import-Module (Join-Path $script:ScriptsDir 'RecallHub-Secrets.psm1') -Force
    $script:PublicDefault = 'recallhub-secret-key-change-in-production'
    $script:KeyA = 'a' * 64
    $script:KeyB = 'b' * 64

    function Write-TestEnv([string]$Path, [string[]]$Lines) {
        [System.IO.File]::WriteAllLines($Path, $Lines, (New-Object System.Text.UTF8Encoding($false)))
    }
}

Describe 'Get-JwtSecretNames' {
    It 'lists the per-tenant variables as plain strings' {
        $names = @('JWT_SECRET_KEY') + @(Get-JwtSecretNames)

        $names.Count | Should -Be 3
        $names[1] | Should -BeOfType [string]
        $names[1..2] | Should -Be @('RECALLHUB_JWT_SECRET_KEY', 'QUELLEX_JWT_SECRET_KEY')
    }
}

Describe 'New-JwtSecret' {
    It 'returns 64 lower-case hex characters' {
        New-JwtSecret | Should -Match '^[0-9a-f]{64}$'
    }

    It 'returns a different key every time' {
        (New-JwtSecret) | Should -Not -Be (New-JwtSecret)
    }
}

Describe 'Test-JwtSecret' {
    It 'rejects <Case>' -ForEach @(
        @{ Case = 'null'; Value = $null }
        @{ Case = 'empty'; Value = '' }
        @{ Case = 'blank'; Value = '   ' }
        @{ Case = 'the public default'; Value = 'recallhub-secret-key-change-in-production' }
        @{ Case = 'a short key'; Value = ('x' * 31) }
    ) {
        Test-JwtSecret $Value | Should -BeFalse
    }

    It 'accepts a generated key' {
        Test-JwtSecret (New-JwtSecret) | Should -BeTrue
    }
}

Describe 'Initialize-JwtSecrets' {
    BeforeEach {
        $envPath = Join-Path $TestDrive ("{0}.env" -f [guid]::NewGuid())
    }

    It 'creates a missing file with one generated key per tenant' {
        $results = @(Initialize-JwtSecrets -EnvPath $envPath)

        $results.Name | Should -Be @('RECALLHUB_JWT_SECRET_KEY', 'QUELLEX_JWT_SECRET_KEY')
        $results.Action | Should -Be @('Generated', 'Generated')
        $recallhub = Get-EnvFileValue -Path $envPath -Name 'RECALLHUB_JWT_SECRET_KEY'
        $quellex = Get-EnvFileValue -Path $envPath -Name 'QUELLEX_JWT_SECRET_KEY'
        $recallhub | Should -Match '^[0-9a-f]{64}$'
        $quellex | Should -Match '^[0-9a-f]{64}$'
        $recallhub | Should -Not -Be $quellex
        (Get-Content $envPath -TotalCount 1) | Should -Match '^# Login token signing keys'
    }

    It 'never returns or prints a key value' {
        $output = @(Initialize-JwtSecrets -EnvPath $envPath *>&1)
        $key = Get-EnvFileValue -Path $envPath -Name 'QUELLEX_JWT_SECRET_KEY'

        ($output | Out-String) | Should -Not -Match $key
        foreach ($item in $output) {
            @($item.PSObject.Properties.Name) | Should -Be @('Name', 'Action')
        }
    }

    It 'keeps valid keys and leaves the file untouched' {
        Write-TestEnv $envPath @('LLM_MODEL=gpt', "RECALLHUB_JWT_SECRET_KEY=$KeyA", "QUELLEX_JWT_SECRET_KEY=$KeyB")
        $before = [System.IO.File]::ReadAllBytes($envPath)
        (Get-Item $envPath).LastWriteTimeUtc = [datetime]'2020-01-01'

        $results = @(Initialize-JwtSecrets -EnvPath $envPath)

        $results.Action | Should -Be @('Kept', 'Kept')
        [System.IO.File]::ReadAllBytes($envPath) | Should -Be $before
        (Get-Item $envPath).LastWriteTimeUtc | Should -Be ([datetime]'2020-01-01')
    }

    It 'replaces <Case> with a generated key' -ForEach @(
        @{ Case = 'the public default'; Value = 'recallhub-secret-key-change-in-production' }
        @{ Case = 'an empty value'; Value = '' }
        @{ Case = 'a short key'; Value = 'too-short' }
    ) {
        Write-TestEnv $envPath @("QUELLEX_JWT_SECRET_KEY=$Value")

        $result = @(Initialize-JwtSecrets -EnvPath $envPath -Names 'QUELLEX_JWT_SECRET_KEY')

        $result.Action | Should -Be 'Generated'
        Test-JwtSecret (Get-EnvFileValue -Path $envPath -Name 'QUELLEX_JWT_SECRET_KEY') | Should -BeTrue
    }

    It 'keeps every other line in place, drops duplicate assignments and writes no BOM' {
        Write-TestEnv $envPath @(
            '# comment',
            'COMPOSE_PROFILES=quellex',
            'QUELLEX_JWT_SECRET_KEY=',
            'BIND_ADDRESS=127.0.0.1',
            'QUELLEX_JWT_SECRET_KEY=recallhub-secret-key-change-in-production'
        )

        $null = Initialize-JwtSecrets -EnvPath $envPath -Names 'QUELLEX_JWT_SECRET_KEY'

        $lines = [System.IO.File]::ReadAllLines($envPath)
        $lines.Count | Should -Be 4
        $lines[0] | Should -Be '# comment'
        $lines[1] | Should -Be 'COMPOSE_PROFILES=quellex'
        $lines[2] | Should -Match '^QUELLEX_JWT_SECRET_KEY=[0-9a-f]{64}$'
        $lines[3] | Should -Be 'BIND_ADDRESS=127.0.0.1'
        $bytes = [System.IO.File]::ReadAllBytes($envPath)
        ($bytes[0] -eq 0xEF -and $bytes[1] -eq 0xBB) | Should -BeFalse
    }

    It 'reads quoted values like Compose does' {
        Write-TestEnv $envPath @("RECALLHUB_JWT_SECRET_KEY=""$KeyA""", "QUELLEX_JWT_SECRET_KEY='$KeyB'")

        @(Initialize-JwtSecrets -EnvPath $envPath).Action | Should -Be @('Kept', 'Kept')
        Get-EnvFileValue -Path $envPath -Name 'RECALLHUB_JWT_SECRET_KEY' | Should -Be $KeyA
    }

    It 'puts back the install''s own key over a key that came with a package' {
        Write-TestEnv $envPath @("QUELLEX_JWT_SECRET_KEY=$KeyB")

        $result = @(Initialize-JwtSecrets -EnvPath $envPath -Names 'QUELLEX_JWT_SECRET_KEY' -Preserve @{ QUELLEX_JWT_SECRET_KEY = $KeyA })

        $result.Action | Should -Be 'Restored'
        Get-EnvFileValue -Path $envPath -Name 'QUELLEX_JWT_SECRET_KEY' | Should -Be $KeyA
    }

    It 'ignores an invalid preserved key' {
        Write-TestEnv $envPath @("QUELLEX_JWT_SECRET_KEY=$KeyB")

        $result = @(Initialize-JwtSecrets -EnvPath $envPath -Names 'QUELLEX_JWT_SECRET_KEY' -Preserve @{ QUELLEX_JWT_SECRET_KEY = $PublicDefault })

        $result.Action | Should -Be 'Kept'
        Get-EnvFileValue -Path $envPath -Name 'QUELLEX_JWT_SECRET_KEY' | Should -Be $KeyB
    }
}

Describe 'Get-JwtSecrets' {
    It 'returns only usable keys' {
        $envPath = Join-Path $TestDrive 'get.env'
        Write-TestEnv $envPath @("RECALLHUB_JWT_SECRET_KEY=$PublicDefault", "QUELLEX_JWT_SECRET_KEY=$KeyB")

        $found = Get-JwtSecrets -EnvPath $envPath

        @($found.Keys) | Should -Be @('QUELLEX_JWT_SECRET_KEY')
        $found['QUELLEX_JWT_SECRET_KEY'] | Should -Be $KeyB
    }

    It 'returns an empty table for a missing file' {
        (Get-JwtSecrets -EnvPath (Join-Path $TestDrive 'missing.env')).Count | Should -Be 0
    }
}

Describe 'Clear-JwtSecrets' {
    It 'blanks the keys and keeps everything else' {
        $envPath = Join-Path $TestDrive 'package.env'
        Write-TestEnv $envPath @('LLM_MODEL=gpt', "RECALLHUB_JWT_SECRET_KEY=$KeyA", 'QUELLEX_JWT_SECRET_KEY=')

        $cleared = @(Clear-JwtSecrets -EnvPath $envPath)

        $cleared.Count | Should -Be 1
        $cleared[0] | Should -Be 'RECALLHUB_JWT_SECRET_KEY'
        [System.IO.File]::ReadAllLines($envPath) | Should -Be @('LLM_MODEL=gpt', 'RECALLHUB_JWT_SECRET_KEY=', 'QUELLEX_JWT_SECRET_KEY=')
    }

    It 'does nothing for a missing file' {
        @(Clear-JwtSecrets -EnvPath (Join-Path $TestDrive 'nope.env')).Count | Should -Be 0
        Test-Path (Join-Path $TestDrive 'nope.env') | Should -BeFalse
    }
}

Describe 'Backup and restore keep the install''s keys' {
    It 'survives a package .env overwriting the install .env (full-restore flow)' {
        $install = Join-Path $TestDrive 'install\.env'
        $package = Join-Path $TestDrive 'package\.env'
        $null = New-Item -ItemType Directory -Force (Split-Path $install), (Split-Path $package)

        # Existing install with its own keys; the package came from another machine.
        $null = Initialize-JwtSecrets -EnvPath $install
        $own = Get-JwtSecrets -EnvPath $install
        Write-TestEnv $package @('COMPOSE_PROFILES=quellex', "RECALLHUB_JWT_SECRET_KEY=$KeyA", "QUELLEX_JWT_SECRET_KEY=$KeyB")
        $null = Clear-JwtSecrets -EnvPath $package            # full-backup.ps1

        $captured = Get-JwtSecrets -EnvPath $install          # full-restore.ps1, before Step 5
        Copy-Item $package $install -Force                     # Steps 5 and 6
        $results = @(Initialize-JwtSecrets -EnvPath $install -Preserve $captured)

        $results.Action | Should -Be @('Restored', 'Restored')
        (Get-JwtSecrets -EnvPath $install)['QUELLEX_JWT_SECRET_KEY'] | Should -Be $own['QUELLEX_JWT_SECRET_KEY']
        (Get-JwtSecrets -EnvPath $install)['RECALLHUB_JWT_SECRET_KEY'] | Should -Be $own['RECALLHUB_JWT_SECRET_KEY']
        Get-EnvFileValue -Path $install -Name 'COMPOSE_PROFILES' | Should -Be 'quellex'
    }

    It 'generates keys of its own for a fresh install from a package' {
        $install = Join-Path $TestDrive 'fresh\.env'
        $null = New-Item -ItemType Directory -Force (Split-Path $install)
        Write-TestEnv $install @('RECALLHUB_JWT_SECRET_KEY=', 'QUELLEX_JWT_SECRET_KEY=')

        @(Initialize-JwtSecrets -EnvPath $install -Preserve @{}).Action | Should -Be @('Generated', 'Generated')
    }
}

Describe 'Initialize-JwtSecrets.ps1' {
    BeforeAll {
        $script:Wrapper = Join-Path $script:ScriptsDir 'Initialize-JwtSecrets.ps1'
    }

    It 'writes JWT_SECRET_KEY to secrets.env for the hardened layout and keeps it on the next run' {
        $app = Join-Path $TestDrive 'app'
        $null = New-Item -ItemType Directory -Force $app

        $first = & powershell -NoProfile -ExecutionPolicy Bypass -File $Wrapper -InstallPath $app -Hardened
        $firstExit = $LASTEXITCODE
        $key = Get-EnvFileValue -Path (Join-Path $app 'secrets.env') -Name 'JWT_SECRET_KEY'
        $second = & powershell -NoProfile -ExecutionPolicy Bypass -File $Wrapper -InstallPath $app -Hardened

        $firstExit | Should -Be 0
        $LASTEXITCODE | Should -Be 0
        Test-JwtSecret $key | Should -BeTrue
        ($first -join "`n") | Should -Match 'JWT_SECRET_KEY: generated'
        ($second -join "`n") | Should -Match 'JWT_SECRET_KEY: kept'
        (($first + $second) -join "`n") | Should -Not -Match $key
        Get-EnvFileValue -Path (Join-Path $app 'secrets.env') -Name 'JWT_SECRET_KEY' | Should -Be $key
        Test-Path (Join-Path $app '.env') | Should -BeFalse
    }

    It 'writes both tenant keys to .env for a backup/restore install' {
        $install = Join-Path $TestDrive 'quellex'
        $null = New-Item -ItemType Directory -Force $install

        $null = & powershell -NoProfile -ExecutionPolicy Bypass -File $Wrapper -InstallPath $install

        $LASTEXITCODE | Should -Be 0
        (Get-JwtSecrets -EnvPath (Join-Path $install '.env')).Count | Should -Be 2
    }
}

Describe 'Shipped configuration carries no signing key' {
    It '<File> has no JWT key value' -ForEach @(
        @{ File = '.env.example' }
        @{ File = '.env.docker' }
        @{ File = 'installer\config\hardened.env' }
    ) {
        $path = Join-Path $RepoRoot $File
        foreach ($name in @('JWT_SECRET_KEY') + @(Get-JwtSecretNames)) {
            Get-EnvFileValue -Path $path -Name $name | Should -BeNullOrEmpty -Because "$name must be generated per install"
        }
    }

    It 'the hardened compose loads secrets.env into the backend' {
        $compose = Get-Content (Join-Path $RepoRoot 'installer\config\docker-compose.hardened.yml') -Raw
        $compose | Should -Match '(?m)^\s+- \./secrets\.env\s*$'
    }

    It 'the generated hardened config loads secrets.env and requires a key' {
        $out = Join-Path $TestDrive 'build-config'
        $null = & (Join-Path $ScriptsDir 'New-BuildConfig.ps1') -OutputDir $out *>&1

        (Get-Content (Join-Path $out 'docker-compose.hardened.yml') -Raw) | Should -Match '(?m)^\s+- \./secrets\.env\s*$'
        Get-EnvFileValue -Path (Join-Path $out 'hardened.env') -Name 'JWT_SECRET_REQUIRED' | Should -Be 'true'
        Get-EnvFileValue -Path (Join-Path $out 'hardened.env') -Name 'JWT_SECRET_KEY' | Should -BeNullOrEmpty
    }
}
