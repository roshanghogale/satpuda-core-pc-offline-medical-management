"""Windows Firewall: let the PCs of the shop's LAN reach the Main PC's listener.

Two inbound rules, only for the local subnet (remoteip=localsubnet) and on every network
profile -- a switch with no router is an "Unidentified network", which Windows files under
Public. Adding a rule needs administrator rights, so it is done through one Windows prompt
(UAC) when the shop presses "Allow on this PC's firewall" (and by the installer). Nothing else
is opened: the engine's own API stays on 127.0.0.1.
"""
from __future__ import annotations

import os
import subprocess

RULE_TCP = "Satpuda Core LAN (Main PC)"
RULE_UDP = "Satpuda Core LAN (find Main PC)"


def _rule_args(name: str, proto: str, port: int) -> str:
    return (f'advfirewall firewall add rule name="{name}" dir=in action=allow protocol={proto} '
            f'localport={int(port)} remoteip=localsubnet profile=any')


def commands(tcp_port: int, udp_port: int) -> list[str]:
    """netsh argument lines (also what the installer runs)."""
    return [
        f'advfirewall firewall delete rule name="{RULE_TCP}"',
        f'advfirewall firewall delete rule name="{RULE_UDP}"',
        _rule_args(RULE_TCP, "TCP", tcp_port),
        _rule_args(RULE_UDP, "UDP", udp_port),
    ]


def rules_present() -> bool | None:
    """True / False on Windows; None when it cannot be told."""
    if os.name != "nt":
        return None
    try:
        out = subprocess.run(
            ["netsh", "advfirewall", "firewall", "show", "rule", f"name={RULE_TCP}"],
            capture_output=True, text=True, timeout=10,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        return out.returncode == 0 and RULE_TCP in (out.stdout or "")
    except Exception:
        return None


def ask_to_allow(tcp_port: int, udp_port: int) -> dict:
    """One Windows (UAC) prompt that adds both rules. Returns at once; the prompt is Windows'."""
    if os.name != "nt":
        return {"ok": False, "error": "Only on Windows."}
    script = " & ".join(f"netsh {c}" for c in commands(tcp_port, udp_port))
    try:
        import ctypes

        rc = ctypes.windll.shell32.ShellExecuteW(None, "runas", "cmd.exe", f'/c {script}', None, 0)
    except Exception as exc:
        return {"ok": False, "error": f"Could not ask Windows: {exc}"}
    if int(rc) <= 32:
        return {"ok": False, "error": "Windows did not allow the change (the prompt was closed or refused)."}
    return {"ok": True, "message": "Windows asked for permission; once allowed, counters can reach this PC."}

