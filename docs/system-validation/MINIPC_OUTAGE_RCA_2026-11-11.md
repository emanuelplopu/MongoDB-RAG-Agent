# Root cause analysis: client mini PC outage, 2026-11-11 05:01-09:25 CET

Host: DESKTOP-8ESH62J, Minisforum MS-02 Ultra (Core Ultra 9 285HX, RTX PRO 2000, AMI BIOS 1.03 of 2025-10-22), Windows 11 Pro 26200. Runs the live Quellex stack (Docker Desktop, `recallhub` compose project) and the RecallHub host agent 1.2.0.

Analysed on 2026-11-11 from the Windows System/Application/Setup/WindowsUpdateClient/Winlogon/Hyper-V event logs, `CBS.log`, Windows Error Reporting buckets, the firmware boot metrics (Kernel-Boot events 30/238), Docker Desktop host logs and the `recallhub_telemetry.host_samples` collection. All times are local (CET, UTC+1).

## Summary

The machine was not up between 05:01:32 and 09:25:55 on 2026-11-11 (4 h 24 min). It did not crash: there is no bugcheck, no dump, no WHEA hardware error and no unexpected-shutdown record. Windows Update installed the November 2026 cumulative update (KB5132061, build 26200.9974, plus servicing stack KB5132688) and restarted the machine unattended at 05:01. The operating system shut down cleanly and asked the firmware for a reboot, but the board never came back: the next thing in any log is a cold power-on at 09:25:55, i.e. when someone switched the machine on in the office. The update then finished normally (second reboot 09:26:41, back at 09:27:26), the user session logged on at 09:28:20, Docker Desktop started at 09:28:46 and the stack was healthy at 09:29.

Nothing happened on 2026-11-10: the uptime counter stood at 26 days at noon, the host agent wrote its 720 samples every hour all day, and the only Application-log entries were two VBScript deprecation notices.

## Timeline

| Time (CET) | Source | Event |
|---|---|---|
| 11-10 all day | host_samples, System log | Normal operation, 720 samples/hour, no errors, no reboot |
| 11-11 04:12-04:25 | WindowsUpdateClient, Setup | KB5132688 staged and installed, KB5132061 staged; "reboot necessary" at 04:25:22 |
| 04:18:14 | WER | `WindowsWcpOtherFailure3` in the component store (`OpenComponentFile`, 0x800f0983) during staging; the update nevertheless installed successfully later |
| 04:59:06 | User32 1074 | `MoUsoCoreWorker.exe` initiated the restart (reason "Operating System: Service pack"). Active hours are 16:00-03:00, so Windows was allowed to restart |
| 04:59:12-04:59:43 | Docker, Hyper-V | User session logged off, Docker Desktop backend exited (0x40010004, the normal WSL teardown), WSL vNIC removed |
| 04:59:12-04:59:33 | Application log | Host agent lost MongoDB (container already stopped), expected |
| 05:00:44-05:01:24 | CBS.log | `poqexec` applied `pending.xml`, result S_OK; "Shutdown processing complete" |
| 05:01:25 | System log | Services stopped, Event Log service stopped |
| 05:01:30-05:01:32 | Kernel-Power 109/577, Kernel-General 13 | "Power Action Reboot", "prepared for a system initiated reboot from Active", "operating system is shutting down" |
| 05:01:32-09:25:55 | nothing | No log, no telemetry, no firmware activity |
| 09:25:55 | Kernel-Boot 238 | Firmware clock at hand-off to the loader: 09:25:55; firmware POST took 25.5 s (ResetEnd to LoadOSImage), the same as every other boot |
| 09:25:58 | Kernel-Boot 20/27 | Boot type 0x0 (cold), "last shutdown's success status was true, last boot's success status was true" |
| 09:26:41-09:27:26 | TrustedInstaller | Second reboot of the update, back after 35 s |
| 09:28:20 | TerminalServices 21 | Session logon, source LOCAL |
| 09:28:46-09:29:07 | Docker | Docker Desktop started, MongoDB/embedder/gateway started, backend and worker recreated by the start script |
| 09:29:43 | host agent | Service started, telemetry resumed 09:29 |
| 09:29:50 | WindowsUpdateClient 19 | KB5132061 installation successful |
| 09:32:48 | TPM-WMI 1034/1808 | Secure Boot dbx update applied (post-update routine, same as after the October updates) |

## Evidence and what it rules out

1. **No crash.** In the last 60 days there is no Kernel-Power 41, no EventLog 6008, no BugCheck 1001, no WHEA-Logger event, and `C:\Windows\Minidump`, `MEMORY.DMP` and `LiveKernelReports` are empty. The last unexpected shutdowns of this machine are from May/June 2026.
2. **The shutdown was orderly up to the kernel's reboot call.** Every stage that can be logged is there and in order: session logoff, Docker/WSL teardown, `poqexec` S_OK, services stopped, Kernel-Power "Reboot", event 13. No service or driver reported a timeout. Diagnostics-Performance records (boot/shutdown durations) are not kept on this machine, so the few seconds after event 13 (driver shutdown callbacks and the ACPI reset) are unobservable.
3. **Windows did not attempt to boot during the gap.** The boot-status flags read at 09:25:58 say the last boot succeeded and the last shutdown was clean. A boot that had started and died would have cleared the "last boot succeeded" flag and triggered automatic repair. So the Windows boot manager was never loaded between 05:01:32 and 09:25:55.
4. **The 09:25 boot was a fresh power-on, not the end of a 4-hour POST.** The firmware boot metrics show 25.5 s from reset to loading the OS image (22.8-23.6 s on the other boots). Had the firmware sat in a dialog or a stalled POST since 05:01 and then been released, those counters would show hours. The RTC at hand-off read 09:25:55, so power/reset came at about 09:25:30.
5. **Every earlier reboot worked.** All reboots of the last 180 days, including the Windows Update reboots of 2026-07-23, 10-12 and 10-13 and the manual one on 10-15, came back in 0.4-0.6 min. This is the first time the board failed to return from an OS-initiated reset.
6. **Thermal/load state was benign.** Last host-agent samples before the shutdown: CPU package 23 W, GPU 45 °C in P8, memory 40 %, disks idle. Ingestion had finished on 2026-11-08; the stack was idle.
7. **Secure Boot servicing is not implicated.** No DB/KEK write was logged between the 10-15 boot and the 05:01 reboot; the registry shows the 2023 CA state as already "Updated" and the boot manager was last replaced on 10-13 (signed by Microsoft Windows Production PCA 2026) and has booted fine since. The dbx update at 09:32 happened after the outage, as it did after the October updates.
8. **The WER noise is unrelated.** `LiveKernelEvent` 1a8/1b8 reports arrive in bursts of four every four hours, 24 per day for weeks, on good days and bad; they are telemetry probes, not failures. The `WindowsWcpOtherFailure3` at 04:18 is a component-store hiccup during staging; the update completed.
9. **Power cut is implausible as the trigger.** Losing mains within the one or two seconds between the kernel's reset call and the firmware's POST would be a coincidence; the NAS, router and Pi-hole were reachable at 09:4x, and the previous reboots on the same mains were fine. A power event cannot be fully excluded from this machine's logs alone, but nothing supports it.

## Root cause

The platform (firmware/board) did not complete the reset that Windows requested at 05:01:30 on 2026-11-11. From the operating system's side the reboot was finished and clean; from the firmware's side the next recorded activity is a cold start at 09:25:55. Whether the board hung before POST (fans on, screen dark) or dropped to a powered-off state cannot be told from logs; only the person who switched it on knows what state it was in (lights/fans on or off, and whether a long press was needed).

Contributing factors:

- **Windows Update was allowed to restart the machine unattended.** Active hours are 16:00-03:00, so automatic restarts fall between 03:00 and 16:00, i.e. at night and during the office morning. The console session stays logged on, but Windows 11 Pro still restarts outside active hours unless the "no auto-restart with logged on users" policy is set.
- **No remote power control.** The machine is loopback-bound behind the Caddy gateway and reachable over AnyDesk/Tailscale only while Windows runs. A hung or powered-off board needs a person in the room.
- **BIOS 1.03 (2025-10-22).** No newer firmware could be confirmed from public sources during the analysis; the vendor's support page for the MS-02 Ultra is the place to check. A firmware fix is the only thing that would prevent a repeat, since the fault happened below the OS.

## Impact

Quellex (API, gateway, worker, MongoDB, embedder, host agent) was down from about 04:59 to 09:29 CET, a little over 4.5 hours, during the night and the first hour and a half of the office day. No data loss: MongoDB was stopped cleanly by Docker before the reboot, and all containers came back healthy. The host-telemetry history has a gap from 04:59 to 09:29.

## Recommendations

1. **Stop unattended update restarts.** Set the policy `HKLM\SOFTWARE\Policies\Microsoft\Windows\WindowsUpdate\AU\NoAutoRebootWithLoggedOnUsers = 1` (the console session is always logged on), or `AUOptions = 3` (download and notify), and restart for updates deliberately when someone is in the office. Alternatively move active hours so restarts cannot fall into the night, though the 18-hour maximum still leaves an unattended window.
2. **Watch the next reboot in person.** Trigger a restart from Windows while standing at the machine. If the board fails to return again, it is a reproducible firmware/hardware fault for the vendor; if it returns, this was a one-off and the update policy alone covers the risk.
3. **Check the vendor's support page for a BIOS newer than 1.03** and read its changelog for reboot, PCIe or memory-training fixes before flashing. Also check the BIOS setting "Restore on AC Power Loss" (set to Power On) so that a remote power-cycle brings the machine back.
4. **Add a remote hard-reset path:** a smart plug (or the office PDU) on the mini PC together with "Restore on AC Power Loss = Power On" turns a dark board into a two-minute fix from anywhere; Wake-on-LAN does not help a hung board.
5. **Confirm the stack comes back without a person.** The stack returned today because a session logged on at 09:28 (either the user or Windows' automatic restart sign-on). Docker Desktop and the start script run at logon, not at boot; verify on the next planned reboot that the sign-in and the stack start happen without anyone touching the machine.

## Actions taken on 2026-11-11

- Windows Update restart policy written (`scripts/Set-UpdateRestartPolicy.ps1`, elevated): `AUOptions = 4`, `NoAutoRebootWithLoggedOnUsers = 1`, `AlwaysAutoRebootAtScheduledTime = 0`. Updates still download and install on their own; the restart waits for a person. `-Status` shows the values, `-Remove` reverts.

## Could the KVM or a BIOS setting have caused it?

The operating system cannot see the firmware stage, so this cannot be settled from logs. What the OS does show: the keyboard and mouse arrive through one USB composite HID device reported as "Glinet Composite Device" (vendor 0x38EB, serial `CAFEBABE`, one keyboard and two mouse interfaces) on a plain USB 3 port. That is the GL.iNet Comet IP-KVM's USB emulation (keyboard, mouse, and virtual media when enabled); the monitor (ROG PG248Q) is on a DisplayPort output of the NVIDIA card, the Comet captures the HDMI output. The KVM path is already known to misbehave at boot: the firmware/driver could not read the monitor's EDID after the 2026-10-15 reboot (640x480 failsafe, worked around by forcing the EDID), and the panel's DDC still does not work through it. A KVM that answers USB enumeration or DisplayPort link training badly during POST is a plausible way to stall an AMI firmware, as is the discrete GPU's own option ROM. A BIOS setting does not hang a board on its own, but "Restore on AC Power Loss" decides whether a power-cycle brings it back, and "Fast Boot"/XHCI hand-off settings change how USB is enumerated at POST.

GL.iNet's own forum has reports of the Comet interfering with the POST stage of other machines: Virtual Media enabled makes the target's BIOS ignore keypresses and lengthens POST ([RM1PE thread](https://forum.gl-inet.com/t/rm1pe-virtual-media-freezes-the-target-bios-keyboard-drive-never-boots-same-as-66265-logs-attached/71600)), the Comet firmware 1.10.1 changed the USB composite descriptors and broke HID enumeration on some hosts until a downgrade to 1.9.2 ([bug report](https://forum.gl-inet.com/t/bug-report-firmware-1-10-1-usb-device-manager-breaks-hid-emulation-on-hardware-kvm-switches-asix-ax68002/70969)), and one BIOS only accepted the emulated HID at Full/Low USB speed ([Beckhoff thread](https://forum.gl-inet.com/t/glkvm-comet-hid-not-working-in-bios-on-beckhoff-c6915-0010-resolved/62019)). None of these is a confirmed "no POST" on an MS-02 Ultra, but they make the Comet a real suspect rather than a guess. Two things to check on the Comet first: whether Virtual Media was mounted or enabled, and which Comet firmware is installed (1.10.x versus 1.9.2). The Comet also answers the open question below, since its screen capture would have shown whether the GPU put out a signal during the outage; if it has the ATX board, it is the remote power-cycle path recommended above.

The way to find out is a controlled test, standing at the machine, with the stack stopped: restart Windows three times with the KVM's USB and video cables unplugged (monitor direct to the GPU, a plain USB keyboard), then three times with the KVM attached. If it only fails with the KVM attached, the KVM is the trigger; if it fails either way, it is the board/GPU firmware and goes to Minisforum with the BIOS version.

## Open questions for the person who restarted it

- Were the power LED and fans on when you found the machine (hung board), or was it dark (powered off)?
- Did it start on a short press of the power button, or did it need a long press first?
- Did the screen show anything (BIOS logo, a Secure Boot or memory message)?
