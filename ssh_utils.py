"""SSH command construction, port parsing, and password-askpass helpers."""
from __future__ import annotations

import os
import shutil
import sys
import tempfile
from typing import Dict, Iterable, List, Optional, Sequence, Set, Tuple


TUNNEL_TYPES = ("reverse", "local", "dynamic")
TUNNEL_TYPE_LABELS = {
    "reverse": "Reverse (-R)",
    "local": "Local (-L)",
    "dynamic": "Dynamic SOCKS (-D)",
}
_FLAG_TO_KIND = {"-R": "reverse", "-L": "local", "-D": "dynamic"}


def extract_forward_spec(cmdline: str) -> Tuple[str, str]:
    """Return (tunnel_type, mappings) from an ssh command line."""
    try:
        parts = cmdline.split()
        mappings: List[str] = []
        kind = "reverse"
        for index, token in enumerate(parts):
            if token in _FLAG_TO_KIND and index + 1 < len(parts) and not parts[index + 1].startswith("-"):
                kind = _FLAG_TO_KIND[token]
                mappings.append(parts[index + 1])
        return kind, (", ".join(mappings) if mappings else "-")
    except Exception:
        return "reverse", "-"


def known_hosts_null() -> str:
    return "NUL" if os.name == "nt" else "/dev/null"


def find_ssh_executable() -> Optional[str]:
    return shutil.which("ssh")


def normalize_tunnel_type(value: str = "") -> str:
    raw = (value or "reverse").strip().lower()
    aliases = {
        "r": "reverse",
        "-r": "reverse",
        "reverse": "reverse",
        "l": "local",
        "-l": "local",
        "local": "local",
        "forward": "local",
        "d": "dynamic",
        "-d": "dynamic",
        "dynamic": "dynamic",
        "socks": "dynamic",
        "socks5": "dynamic",
    }
    return aliases.get(raw, "reverse")


def parse_port_pairs(ports: str) -> List[Tuple[int, int]]:
    """Parse mappings like '8080:80', '8080:127.0.0.1:80', or a single SOCKS port."""
    pairs: List[Tuple[int, int]] = []
    if not ports or ports.strip() in {"-", ""}:
        return pairs
    for raw in ports.split(","):
        token = raw.strip()
        if not token:
            continue
        parts = [p.strip() for p in token.split(":") if p.strip()]
        numbers: List[int] = []
        for part in parts:
            try:
                numbers.append(int(part))
            except ValueError:
                continue
        if len(numbers) >= 2:
            pairs.append((numbers[0], numbers[-1]))
        elif len(numbers) == 1:
            pairs.append((numbers[0], numbers[0]))
    return pairs


def normalize_port_set(ports: str) -> Set[Tuple[int, int]]:
    return set(parse_port_pairs(ports))


def ports_match(saved_ports: str, process_ports: str) -> bool:
    saved = normalize_port_set(saved_ports)
    running = normalize_port_set(process_ports)
    if not saved or not running:
        return False
    return saved == running or saved.issubset(running)


def build_forward_flags(ports: str, tunnel_type: str = "reverse") -> List[str]:
    kind = normalize_tunnel_type(tunnel_type)
    pairs = parse_port_pairs(ports)
    if not pairs:
        return []
    if kind == "dynamic":
        return ["-D", str(pairs[0][0])]
    flag = "-R" if kind == "reverse" else "-L"
    flags: List[str] = []
    for left, right in pairs:
        flags.extend([flag, f"{left}:127.0.0.1:{right}"])
    return flags


def build_r_flags(ports: str) -> List[str]:
    return build_forward_flags(ports, "reverse")


def build_ssh_command(
    user: str,
    host: str,
    ports: str = "",
    *,
    test: bool = False,
    auth_method: str = "key",
    identity_file: str = "",
    ssh_port: int = 22,
    host_key_policy: str = "accept-new",
    debug: bool = False,
    tunnel_type: str = "reverse",
) -> List[str]:
    if not user or not host:
        raise ValueError("Username and host are required.")
    kind = normalize_tunnel_type(tunnel_type)
    if not test:
        if kind == "dynamic":
            if not parse_port_pairs(ports):
                raise ValueError("A SOCKS listen port is required (e.g. 1080).")
        elif not parse_port_pairs(ports):
            raise ValueError("At least one valid port mapping is required (e.g. 8080:8080).")

    cmd: List[str] = ["ssh"]
    if debug:
        cmd.append("-vvv")

    policy = (host_key_policy or "accept-new").strip()
    if policy not in {"accept-new", "yes", "no"}:
        policy = "accept-new"
    cmd.extend(["-o", f"StrictHostKeyChecking={policy}"])
    if policy == "no":
        cmd.extend(["-o", f"UserKnownHostsFile={known_hosts_null()}"])

    cmd.extend(
        [
            "-o",
            "ConnectTimeout=10",
            "-o",
            "ServerAliveInterval=30",
            "-o",
            "ServerAliveCountMax=4",
            "-o",
            "ExitOnForwardFailure=yes",
        ]
    )

    if ssh_port and int(ssh_port) != 22:
        cmd.extend(["-p", str(int(ssh_port))])

    identity_file = (identity_file or "").strip()
    if identity_file:
        cmd.extend(["-i", identity_file, "-o", "IdentitiesOnly=yes"])

    if auth_method == "password":
        cmd.extend(
            [
                "-o",
                "PreferredAuthentications=password",
                "-o",
                "PubkeyAuthentication=no",
                "-o",
                "NumberOfPasswordPrompts=1",
            ]
        )
    else:
        cmd.extend(
            [
                "-o",
                "PreferredAuthentications=publickey",
                "-o",
                "PasswordAuthentication=no",
                "-o",
                "BatchMode=yes",
            ]
        )

    if test:
        cmd.extend([f"{user}@{host}", "echo", "SSH connection successful"])
        return cmd

    cmd.extend(build_forward_flags(ports, kind))
    cmd.extend(["-N", f"{user}@{host}"])
    return cmd


def wrap_password_command(cmd: Sequence[str], password: str) -> Tuple[List[str], Dict[str, str], Optional[str]]:
    """Return (command, env, cleanup_path) for password auth without putting the password on argv.

    Prefers `sshpass -e` (password in env). Falls back to an SSH_ASKPASS helper script.
    """
    if not password:
        raise ValueError("Password is required for password authentication.")

    env = os.environ.copy()
    sshpass = shutil.which("sshpass")
    if sshpass:
        env["SSHPASS"] = password
        return [sshpass, "-e", *cmd], env, None

    helper = _write_askpass_helper()
    env["STM_ASKPASS_PASSWORD"] = password
    env["SSH_ASKPASS"] = helper
    env["SSH_ASKPASS_REQUIRE"] = "force"
    env.setdefault("DISPLAY", "1")
    return list(cmd), env, helper


def _write_askpass_helper() -> str:
    fd, path = tempfile.mkstemp(prefix="stm_askpass_", suffix=".py")
    script = (
        "import os, sys\n"
        "sys.stdout.write(os.environ.get('STM_ASKPASS_PASSWORD', ''))\n"
    )
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        handle.write(script)
    if os.name != "nt":
        os.chmod(path, 0o700)
    # Windows OpenSSH expects an executable; invoke via the same Python.
    if os.name == "nt":
        launcher = path + ".cmd"
        with open(launcher, "w", encoding="utf-8") as handle:
            handle.write(f'@echo off\r\n"{sys.executable}" "{path}"\r\n')
        return launcher
    return f"{sys.executable} {path}"


def cleanup_path(path: Optional[str]) -> None:
    if not path:
        return
    candidates = {path}
    if path.endswith(".cmd"):
        candidates.add(path[:-4])
    for candidate in candidates:
        try:
            os.unlink(candidate)
        except OSError:
            pass


def redact_command(cmd: Iterable[str]) -> str:
    return " ".join(str(part) for part in cmd)


def validate_ports(ports: str, tunnel_type: str = "reverse") -> None:
    kind = normalize_tunnel_type(tunnel_type)
    if kind == "dynamic":
        token = (ports or "").strip()
        if not token or "," in token:
            raise ValueError("Dynamic SOCKS needs a single local port, e.g. 1080.")
        try:
            port = int(token.split(":")[-1])
        except ValueError as exc:
            raise ValueError("Dynamic SOCKS port must be a number, e.g. 1080.") from exc
        if port < 1 or port > 65535:
            raise ValueError("SOCKS port must be between 1 and 65535.")
        return
    if not parse_port_pairs(ports):
        raise ValueError("Port mappings must look like 8080:80 or 8080:8080,9000:9000.")
    hint = "local:remote" if kind == "local" else "remote:local"
    for raw in ports.split(","):
        token = raw.strip()
        if token and ":" not in token:
            raise ValueError(f"Invalid port mapping '{token}'. Use {hint}.")
