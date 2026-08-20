import base64
import configparser
import logging
import os
import sys
import tempfile
from typing import Any, Dict, Optional

from ssh_utils import normalize_tunnel_type


def _dirname(path: str) -> str:
    return os.path.dirname(os.path.abspath(path))


def save_config(config: configparser.ConfigParser, config_file: str) -> None:
    """Atomically persist the INI file."""
    directory = _dirname(config_file)
    os.makedirs(directory, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(prefix=".stm-", suffix=".ini", dir=directory)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            config.write(handle)
        os.replace(tmp_path, config_file)
    except Exception:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


def encrypt_password(password: str) -> str:
    """Protect a stored password. Uses Windows DPAPI when available; otherwise base64."""
    if not password:
        return ""
    if sys.platform == "win32":
        try:
            return "dpapi:" + _dpapi_protect(password)
        except Exception as exc:
            logging.warning("DPAPI encrypt failed, falling back to obfuscation: %s", exc)
    try:
        encoded = base64.b64encode(password.encode("utf-8")).decode("ascii")
        return f"enc:{encoded}"
    except Exception as exc:
        logging.error("Error encoding password: %s", exc)
        return password


def decrypt_password(encrypted_password: str) -> str:
    if not encrypted_password:
        return ""
    try:
        if encrypted_password.startswith("dpapi:"):
            return _dpapi_unprotect(encrypted_password[6:])
        if encrypted_password.startswith("enc:"):
            return base64.b64decode(encrypted_password[4:].encode("ascii")).decode("utf-8")
        return encrypted_password
    except Exception as exc:
        logging.error("Error decoding password: %s", exc)
        return ""


def _dpapi_protect(plaintext: str) -> str:
    data = _crypt_protect_data(plaintext.encode("utf-8"))
    return base64.b64encode(data).decode("ascii")


def _dpapi_unprotect(payload: str) -> str:
    raw = base64.b64decode(payload.encode("ascii"))
    return _crypt_unprotect_data(raw).decode("utf-8")


def _crypt_protect_data(raw: bytes) -> bytes:
    import ctypes
    from ctypes import wintypes

    class DATA_BLOB(ctypes.Structure):
        _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]

    crypt32 = ctypes.windll.crypt32
    kernel32 = ctypes.windll.kernel32
    buffer = ctypes.create_string_buffer(raw)
    in_blob = DATA_BLOB(len(raw), buffer)
    out_blob = DATA_BLOB()
    if not crypt32.CryptProtectData(ctypes.byref(in_blob), None, None, None, None, 0, ctypes.byref(out_blob)):
        raise OSError("CryptProtectData failed")
    try:
        return ctypes.string_at(out_blob.pbData, out_blob.cbData)
    finally:
        kernel32.LocalFree(out_blob.pbData)


def _crypt_unprotect_data(raw: bytes) -> bytes:
    import ctypes
    from ctypes import wintypes

    class DATA_BLOB(ctypes.Structure):
        _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]

    crypt32 = ctypes.windll.crypt32
    kernel32 = ctypes.windll.kernel32
    buffer = ctypes.create_string_buffer(raw)
    in_blob = DATA_BLOB(len(raw), buffer)
    out_blob = DATA_BLOB()
    if not crypt32.CryptUnprotectData(ctypes.byref(in_blob), None, None, None, None, 0, ctypes.byref(out_blob)):
        raise OSError("CryptUnprotectData failed")
    try:
        return ctypes.string_at(out_blob.pbData, out_blob.cbData)
    finally:
        kernel32.LocalFree(out_blob.pbData)


def ensure_default_sections(config: configparser.ConfigParser) -> None:
    if not config.has_section("VPS"):
        config.add_section("VPS")
        config.set("VPS", "user", "")
        config.set("VPS", "ip", "")
    if not config.has_section("Tunnel"):
        config.add_section("Tunnel")
        config.set("Tunnel", "ports", "")
    if not config.has_section("Settings"):
        config.add_section("Settings")
        config.set("Settings", "auto_start", "False")
        config.set("Settings", "minimize_to_tray", "True")
        config.set("Settings", "start_with_windows", "False")
        config.set("Settings", "stop_tunnels_on_exit", "True")
        config.set("Settings", "host_key_policy", "accept-new")


def load_saved_tunnels(config: configparser.ConfigParser, config_file: str) -> Dict[str, Dict[str, Any]]:
    tunnels: Dict[str, Dict[str, Any]] = {}
    try:
        if os.path.exists(config_file):
            config.read(config_file, encoding="utf-8")
        else:
            logging.info("Config file not found, starting fresh: %s", config_file)
        ensure_default_sections(config)

        for section_name in config.sections():
            if not section_name.startswith("Tunnel_"):
                continue
            section = config[section_name]
            tunnel_name = section.get("name", section_name[7:])
            if not tunnel_name:
                continue
            password = section.get("password", "")
            tunnels[tunnel_name] = {
                "name": tunnel_name,
                "user": section.get("user", ""),
                "host": section.get("host", ""),
                "ports": section.get("ports", ""),
                "tunnel_type": normalize_tunnel_type(section.get("tunnel_type", "reverse")),
                "description": section.get("description", ""),
                "auth_method": section.get("auth_method", "key"),
                "password": decrypt_password(password) if password else "",
                "identity_file": section.get("identity_file", ""),
                "ssh_port": section.get("ssh_port", "22"),
                "auto_start": section.getboolean("auto_start", fallback=False),
                "last_pid": section.get("last_pid", fallback=None) or section.get("pid", fallback=None),
                "last_started": section.get("last_started", fallback=None),
            }
        logging.info("Loaded %s saved tunnel configuration(s)", len(tunnels))
        return tunnels
    except Exception as exc:
        logging.error("Error loading saved tunnels: %s", exc)
        return {}


def save_tunnel_config(
    config: configparser.ConfigParser,
    config_file: str,
    tunnel_config: Dict[str, Any],
) -> None:
    tunnel_name = tunnel_config["name"]
    section_name = f"Tunnel_{tunnel_name}"
    previous_pid = None
    previous_started = None
    if config.has_section(section_name):
        previous_pid = config.get(section_name, "last_pid", fallback=None)
        previous_started = config.get(section_name, "last_started", fallback=None)
        config.remove_section(section_name)
    config.add_section(section_name)
    section = config[section_name]
    section["name"] = tunnel_config["name"]
    section["user"] = tunnel_config["user"]
    section["host"] = tunnel_config["host"]
    section["ports"] = tunnel_config["ports"]
    section["tunnel_type"] = normalize_tunnel_type(tunnel_config.get("tunnel_type", "reverse"))
    section["description"] = tunnel_config.get("description", "")
    section["auth_method"] = tunnel_config.get("auth_method", "key")
    section["identity_file"] = tunnel_config.get("identity_file", "")
    section["ssh_port"] = str(tunnel_config.get("ssh_port", "22") or "22")
    section["auto_start"] = "true" if tunnel_config.get("auto_start") else "false"
    if tunnel_config.get("auth_method") == "password" and tunnel_config.get("password"):
        section["password"] = encrypt_password(tunnel_config["password"])
    if previous_pid:
        section["last_pid"] = previous_pid
    if previous_started:
        section["last_started"] = previous_started
    save_config(config, config_file)
    logging.info("Saved tunnel configuration: %s", tunnel_name)


def delete_tunnel_config(config: configparser.ConfigParser, config_file: str, tunnel_name: str) -> None:
    section_name = f"Tunnel_{tunnel_name}"
    if section_name in config:
        config.remove_section(section_name)
        save_config(config, config_file)
    logging.info("Deleted tunnel configuration: %s", tunnel_name)


def save_tunnel_pid(config: configparser.ConfigParser, config_file: str, tunnel_name: str, pid: int) -> None:
    section_name = f"Tunnel_{tunnel_name}"
    if section_name not in config:
        logging.warning("Cannot save PID; missing section %s", section_name)
        return
    import time

    config[section_name]["last_pid"] = str(pid)
    config[section_name]["last_started"] = str(int(time.time()))
    save_config(config, config_file)


def clear_tunnel_pid(config: configparser.ConfigParser, config_file: str, tunnel_name: str) -> None:
    section_name = f"Tunnel_{tunnel_name}"
    if section_name in config:
        config[section_name].pop("last_pid", None)
        config[section_name].pop("pid", None)
        config[section_name].pop("last_started", None)
        save_config(config, config_file)
