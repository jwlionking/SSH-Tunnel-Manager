import logging
import time
from typing import Any, Dict, List, Optional

import psutil

from ssh_utils import extract_forward_spec, normalize_tunnel_type, ports_match


def extract_user_host(cmdline: str) -> str:
    try:
        for part in reversed(cmdline.split()):
            if "@" in part and not part.startswith("-") and "://" not in part:
                return part
        return "Unknown"
    except Exception:
        return "Unknown"


def extract_port_mappings(cmdline: str) -> str:
    return extract_forward_spec(cmdline)[1]


def format_duration(seconds: int) -> str:
    try:
        seconds = max(0, int(seconds))
        if seconds < 60:
            return f"{seconds}s"
        minutes, secs = divmod(seconds, 60)
        if minutes < 60:
            return f"{minutes}m {secs}s"
        hours, minutes = divmod(minutes, 60)
        if hours < 24:
            return f"{hours}h {minutes}m"
        days, hours = divmod(hours, 24)
        return f"{days}d {hours}h"
    except Exception:
        return "-"


def scan_ssh_tunnels() -> Dict[int, Dict[str, Any]]:
    """Scan for ssh reverse/local/dynamic tunnel processes. Keyed by PID."""
    results: Dict[int, Dict[str, Any]] = {}
    try:
        for proc in psutil.process_iter(["pid", "name", "cmdline", "create_time"]):
            try:
                name = (proc.info.get("name") or "").lower()
                cmd = proc.info.get("cmdline") or []
                if name not in {"ssh", "ssh.exe"} or not cmd:
                    continue
                cmdline = " ".join(cmd)
                if not any(flag in cmd or flag in cmdline for flag in ("-R", "-L", "-D")):
                    continue
                kind, ports = extract_forward_spec(cmdline)
                results[proc.pid] = {
                    "pid": proc.pid,
                    "cmdline": cmdline,
                    "create_time": proc.info.get("create_time") or time.time(),
                    "user_host": extract_user_host(cmdline),
                    "ports": ports,
                    "tunnel_type": kind,
                }
            except (psutil.NoSuchProcess, psutil.AccessDenied, IndexError):
                continue
    except Exception as exc:
        logging.error("Error scanning SSH processes: %s", exc)
    return results


def find_matching_process(
    processes: Dict[int, Dict[str, Any]],
    user_host: str,
    ports: str,
    last_pid: Optional[int] = None,
    tunnel_type: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    want_type = normalize_tunnel_type(tunnel_type) if tunnel_type else None

    def matches(info: Dict[str, Any]) -> bool:
        if user_host not in info.get("user_host", ""):
            return False
        if want_type and normalize_tunnel_type(info.get("tunnel_type", "reverse")) != want_type:
            return False
        return ports_match(ports, info.get("ports", ""))

    if last_pid:
        info = processes.get(int(last_pid))
        if info and matches(info):
            return info
    for info in processes.values():
        if matches(info):
            return info
    return None


def list_external_tunnels() -> List[Dict[str, Any]]:
    now = time.time()
    out: List[Dict[str, Any]] = []
    for info in scan_ssh_tunnels().values():
        out.append(
            {
                "pid": info["pid"],
                "user_host": info.get("user_host", ""),
                "ports": info.get("ports", ""),
                "tunnel_type": info.get("tunnel_type", "reverse"),
                "duration": format_duration(int(now - info.get("create_time", now))),
                "create_time": info.get("create_time", now),
                "cmdline": info.get("cmdline", ""),
            }
        )
    return out


def terminate_pid(pid: int, timeout: float = 5.0) -> bool:
    try:
        proc = psutil.Process(pid)
    except psutil.NoSuchProcess:
        return False
    try:
        proc.terminate()
        try:
            proc.wait(timeout=timeout)
        except psutil.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=3)
        return True
    except (psutil.NoSuchProcess, psutil.AccessDenied) as exc:
        logging.debug("Could not stop pid %s: %s", pid, exc)
        return False


def stop_all_ssh_tunnels(timeout: float = 3.0) -> int:
    count = 0
    for info in list(scan_ssh_tunnels().values()):
        if terminate_pid(info["pid"], timeout=timeout):
            count += 1
    return count
