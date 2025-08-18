import logging
import psutil
import time
from typing import Dict, List, Any


def extract_user_host(cmdline: str) -> str:
    """Extract user@host from SSH command line."""
    try:
        parts = cmdline.split()
        # Find the last token that looks like user@host
        for part in reversed(parts):
            if '@' in part and not part.startswith('-'):
                return part
        return "Unknown"
    except Exception:
        return "Unknown"


def extract_port_mappings(cmdline: str) -> str:
    """Extract -R port mappings from SSH command line into a concise string."""
    try:
        parts = cmdline.split()
        mappings: List[str] = []
        for i, p in enumerate(parts):
            if p == '-R' and i + 1 < len(parts):
                mappings.append(parts[i + 1])
        return ', '.join(mappings) if mappings else '-'
    except Exception:
        return '-'


def format_duration(seconds: int) -> str:
    """Format seconds into human-readable duration."""
    try:
        seconds = int(seconds)
        if seconds < 60:
            return f"{seconds}s"
        minutes, s = divmod(seconds, 60)
        if minutes < 60:
            return f"{minutes}m {s}s"
        hours, m = divmod(minutes, 60)
        if hours < 24:
            return f"{hours}h {m}m"
        days, h = divmod(hours, 24)
        return f"{days}d {h}h"
    except Exception:
        return "-"


def scan_ssh_tunnels() -> Dict[int, Dict[str, Any]]:
    """Scan the system for ssh -R reverse tunnel processes.
    Returns a dict keyed by PID with metadata.
    """
    results: Dict[int, Dict[str, Any]] = {}
    try:
        for proc in psutil.process_iter(['pid', 'name', 'cmdline', 'create_time']):
            try:
                name = (proc.info.get('name') or proc.name() or '').lower()
                cmd = proc.info.get('cmdline') or proc.cmdline() or []
                if name in ('ssh', 'ssh.exe') and cmd:
                    cmdline = ' '.join(cmd)
                    if '-R' in cmdline and '@' in cmdline:
                        results[proc.pid] = {
                            'pid': proc.pid,
                            'cmdline': cmdline,
                            'create_time': proc.info.get('create_time') or proc.create_time(),
                            'user_host': extract_user_host(cmdline),
                            'ports': extract_port_mappings(cmdline),
                        }
            except (psutil.NoSuchProcess, psutil.AccessDenied, IndexError):
                continue
    except Exception as e:
        logging.error(f"Error scanning SSH processes: {e}")
    return results


def list_external_tunnels() -> List[Dict[str, Any]]:
    """Return a list of external ssh -R tunnel processes with friendly fields."""
    out: List[Dict[str, Any]] = []
    now = time.time()
    for info in scan_ssh_tunnels().values():
        duration = format_duration(int(now - info.get('create_time', now)))
        out.append({
            'pid': info['pid'],
            'user_host': info.get('user_host', ''),
            'ports': info.get('ports', ''),
            'duration': duration,
            'cmdline': info.get('cmdline', ''),
        })
    return out


def stop_all_ssh_tunnels(timeout: float = 3.0) -> int:
    """Attempt to terminate all detected ssh -R tunnel processes. Returns count stopped."""
    count = 0
    procs = list(scan_ssh_tunnels().values())
    for info in procs:
        pid = info['pid']
        try:
            p = psutil.Process(pid)
            p.terminate()
            try:
                p.wait(timeout=timeout)
            except psutil.TimeoutExpired:
                p.kill()
            count += 1
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
        except Exception as e:
            logging.debug(f"Failed stopping pid {pid}: {e}")
    return count
