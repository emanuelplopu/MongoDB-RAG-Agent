# RecallHub host agent

A small .NET 10 Windows service that samples the real host every 5 seconds into MongoDB
(`recallhub_telemetry.host_samples`): CPU, RAM, drives, disk and network I/O, CPU package power
(Windows RAPL counters), GPU via `nvidia-smi`, per-container use via the Docker Engine pipe,
Ollama models, backend `/health` latency and — with the opt-in PawnIO driver — CPU, board and
NVMe temperatures. The machine-stats widget and System → Telemetry History read it.

Full documentation (schema, sources, caveats, analysis): `docs/12-TELEMETRY_SYSTEM.md`,
section "Host Telemetry History".

```powershell
dotnet run -- --once                     # one sample as JSON, nothing written
dotnet run -- --dry-run                  # every sample as JSON, nothing written
dotnet run -- --sensors                  # (elevated) every LibreHardwareMonitor sensor + derived temperatures, nothing written
dotnet test ..\host-agent.Tests          # unit tests (+ MongoDB round trip with RECALLHUB_TEST_MONGO_URI)
dotnet publish -c Release                # self-contained single file in bin\Release\...\publish
..\scripts\Install-HostAgent.ps1 [-EnableHardwareSensors -InstallPawnIO] [-Mode Task]
```

| File | Role |
|------|------|
| `Program.cs`, `AgentWorker.cs` | host, configuration precedence, fixed-cadence loop, `--once` / `--dry-run` |
| `SampleCollector.cs` | runs the probes concurrently and assembles one sample (schema 1) |
| `Probes/SystemProbe.cs` | CPU, memory, drives, disk/network I/O, uptime (performance counters, Win32) |
| `Probes/RaplPowerProbe.cs` | CPU package / core / iGPU / DRAM power from `\Energy Meter(RAPL_*)` |
| `Probes/NvidiaSmiProbe.cs` | GPU via `nvidia-smi` (field-set detection, throttle reasons) |
| `Probes/DockerProbe.cs` | per-container CPU cores, memory, I/O, restarts, health (Engine API over a named pipe) |
| `Probes/HttpProbes.cs` | Ollama `/api/ps`, backend `/health` latency |
| `Probes/HardwareSensors.cs` | LibreHardwareMonitor + PawnIO temperatures and fans (opt-in); drive temperature selection |
| `Probes/SensorDiagnostics.cs` | `--sensors`: lists every sensor (name, type, value) and marks which drive temperatures are used |
| `Storage/MongoStorage.cs` | time-series collection creation, buffered writes, `activity_current` reader |
