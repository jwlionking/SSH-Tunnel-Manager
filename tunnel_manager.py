import argparse
import configparser
import logging
import os
import signal
import subprocess
import sys
import threading
import time
from typing import Any, Dict, Optional

from PIL import Image, ImageDraw
import pystray

import process_utils as pu
import tunnel_ui
from config_store import (
    clear_tunnel_pid as cs_clear_tunnel_pid,
    decrypt_password as cs_decrypt_password,
    delete_tunnel_config as cs_delete_tunnel_config,
    encrypt_password as cs_encrypt_password,
    ensure_default_sections,
    load_saved_tunnels as cs_load_saved_tunnels,
    save_config as cs_save_config,
    save_tunnel_config as cs_save_tunnel_config,
    save_tunnel_pid as cs_save_tunnel_pid,
)
from logger_utils import LOG_FILE, close_file_logging, init_logging, setup_file_logging
from ssh_utils import (
    build_ssh_command,
    cleanup_path,
    find_ssh_executable,
    redact_command,
    wrap_password_command,
)

if getattr(sys, "frozen", False):
    APP_DIR = os.path.dirname(sys.executable)
else:
    APP_DIR = os.path.dirname(os.path.abspath(__file__))

CONFIG_FILE = os.path.join(APP_DIR, "tunnel_manager.ini")
ICON_FILE = os.path.join(APP_DIR, "tunnel_icon.ico")

DEBUG_MODE = "--debug" in sys.argv
init_logging(DEBUG_MODE)

if sys.platform == "win32":
    import winreg
else:
    winreg = None


class TunnelManager:
    def __init__(self, headless: bool = False):
        self.headless = headless
        self.process = None
        self.config = configparser.ConfigParser()
        try:
            self._launched_from_console = sys.stdout.isatty()
        except Exception:
            self._launched_from_console = False

        setup_file_logging()
        self.load_config()

        self.icon = None
        self.running = True
        self._tunnel_cache: Dict[int, Dict[str, Any]] = {}
        self._last_scan_time = 0.0
        self._cache_ttl = 5
        self._window_visible = True
        self._saved_tunnels: Dict[str, Dict[str, Any]] = {}
        self._active_tunnels: Dict[str, Dict[str, Any]] = {}
        self._askpass_cleanup: list[str] = []

        self.load_saved_tunnels()
        self.detect_external_tunnels()

        if not find_ssh_executable():
            logging.error("OpenSSH client ('ssh') was not found on PATH.")

        if not headless:
            tunnel_ui.create_widgets(self)

        if self.config.getboolean("Settings", "auto_start", fallback=False):
            threading.Thread(target=self._auto_start_tunnels, daemon=True).start()

    def load_config(self) -> None:
        try:
            if os.path.exists(CONFIG_FILE):
                self.config.read(CONFIG_FILE, encoding="utf-8")
                logging.info("Configuration loaded from %s", CONFIG_FILE)
            else:
                logging.info("No config file yet; using defaults")
            ensure_default_sections(self.config)
        except Exception as exc:
            logging.error("Error loading configuration: %s", exc)
            ensure_default_sections(self.config)

    def persist_config(self) -> None:
        cs_save_config(self.config, CONFIG_FILE)

    def collect_ui_settings(self) -> None:
        ensure_default_sections(self.config)
        if hasattr(self, "user_entry"):
            self.config.set("VPS", "user", self.user_entry.get().strip())
            self.config.set("VPS", "ip", self.ip_entry.get().strip())
            self.config.set("Tunnel", "ports", self.ports_entry.get().strip())
        if hasattr(self, "auto_start_var"):
            self.config.set("Settings", "auto_start", str(bool(self.auto_start_var.get())))
            self.config.set("Settings", "minimize_to_tray", str(bool(self.minimize_to_tray_var.get())))
            self.config.set("Settings", "start_with_windows", str(bool(self.start_with_windows_var.get())))
        if hasattr(self, "stop_on_exit_var"):
            self.config.set("Settings", "stop_tunnels_on_exit", str(bool(self.stop_on_exit_var.get())))
        if hasattr(self, "host_key_policy_var"):
            self.config.set("Settings", "host_key_policy", self.host_key_policy_var.get())

    def save_config(self, notify: bool = True) -> None:
        try:
            self.collect_ui_settings()
            self.persist_config()
            if hasattr(self, "start_with_windows_var"):
                if self.start_with_windows_var.get():
                    self.enable_startup()
                else:
                    self.disable_startup()
            if notify:
                self.notify("Success", "Configuration saved.")
            logging.info("Configuration saved")
        except Exception as exc:
            logging.error("Failed to save configuration: %s", exc)
            self.notify("Error", f"Failed to save configuration: {exc}", level="error")

    def save_settings_quiet(self) -> None:
        self.save_config(notify=False)

    def load_saved_tunnels(self) -> None:
        try:
            self._saved_tunnels = cs_load_saved_tunnels(self.config, CONFIG_FILE)
        except Exception as exc:
            logging.error("Error loading saved tunnels: %s", exc)
            self._saved_tunnels = {}

    def save_tunnel_config(self, tunnel_config: Dict[str, Any]) -> None:
        cs_save_tunnel_config(self.config, CONFIG_FILE, tunnel_config)
        self._saved_tunnels[tunnel_config["name"]] = tunnel_config.copy()
        self._refresh_tray_menu()

    def delete_tunnel_config(self, tunnel_name: str) -> None:
        cs_delete_tunnel_config(self.config, CONFIG_FILE, tunnel_name)
        self._saved_tunnels.pop(tunnel_name, None)
        self._refresh_tray_menu()

    def save_tunnel_pid(self, tunnel_name: str, pid: int) -> None:
        cs_save_tunnel_pid(self.config, CONFIG_FILE, tunnel_name, pid)

    def clear_tunnel_pid(self, tunnel_name: str) -> None:
        cs_clear_tunnel_pid(self.config, CONFIG_FILE, tunnel_name)

    def _encrypt_password(self, password: str) -> str:
        return cs_encrypt_password(password)

    def _decrypt_password(self, encrypted_password: str) -> str:
        return cs_decrypt_password(encrypted_password)

    def host_key_policy(self) -> str:
        return self.config.get("Settings", "host_key_policy", fallback="accept-new")

    def notify(self, title: str, message: str, level: str = "info") -> None:
        log = {"info": logging.info, "warning": logging.warning, "error": logging.error}.get(level, logging.info)
        log("%s: %s", title, message)
        if self.headless:
            return
        try:
            from tkinter import messagebox

            if level == "error":
                messagebox.showerror(title, message)
            elif level == "warning":
                messagebox.showwarning(title, message)
            else:
                messagebox.showinfo(title, message)
        except Exception:
            pass

    def confirm(self, title: str, message: str) -> bool:
        if self.headless:
            return True
        try:
            from tkinter import messagebox

            return bool(messagebox.askyesno(title, message))
        except Exception:
            return False

    def ui(self, fn, *args, **kwargs) -> None:
        if self.headless or not hasattr(self, "root"):
            return
        self.root.after(0, lambda: fn(*args, **kwargs))

    def detect_external_tunnels(self) -> None:
        try:
            processes = pu.scan_ssh_tunnels()
            matched = 0
            for name, config in self._saved_tunnels.items():
                user_host = f"{config['user']}@{config['host']}"
                last_pid = None
                try:
                    if config.get("last_pid"):
                        last_pid = int(config["last_pid"])
                except (TypeError, ValueError):
                    last_pid = None
                info = pu.find_matching_process(processes, user_host, config.get("ports", ""), last_pid)
                if info:
                    self.save_tunnel_pid(name, info["pid"])
                    config["last_pid"] = str(info["pid"])
                    matched += 1
                elif last_pid:
                    self.clear_tunnel_pid(name)
                    config["last_pid"] = None
            if matched:
                logging.info("Matched %s running tunnel(s) to saved configurations", matched)
        except Exception as exc:
            logging.error("Error detecting external tunnels: %s", exc)

    def _auto_start_tunnels(self) -> None:
        time.sleep(0.4)
        marked = [name for name, cfg in self._saved_tunnels.items() if cfg.get("auto_start")]
        targets = marked or list(self._saved_tunnels.keys())
        for name in targets:
            try:
                if self._is_saved_tunnel_running(name):
                    logging.info("Skipping auto-start for %s; already running", name)
                    continue
                self.start_saved_tunnel(name, notify=False)
            except Exception as exc:
                logging.error("Auto-start failed for %s: %s", name, exc)
        self.ui(self.refresh_tunnels)

    def _is_saved_tunnel_running(self, tunnel_name: str) -> bool:
        info = self._saved_tunnels.get(tunnel_name)
        if not info:
            return False
        if tunnel_name in self._active_tunnels:
            process = self._active_tunnels[tunnel_name].get("process")
            if process and process.poll() is None:
                return True
        processes = self._get_cached_ssh_processes()
        user_host = f"{info['user']}@{info['host']}"
        return pu.find_matching_process(processes, user_host, info.get("ports", "")) is not None

    def _get_cached_ssh_processes(self, force_refresh: bool = False) -> Dict[int, Dict[str, Any]]:
        now = time.time()
        if not force_refresh and (now - self._last_scan_time) < self._cache_ttl:
            return self._tunnel_cache
        try:
            self._tunnel_cache = dict(pu.scan_ssh_tunnels())
        except Exception as exc:
            logging.error("Error scanning SSH processes: %s", exc)
            self._tunnel_cache = {}
        self._last_scan_time = now
        return self._tunnel_cache

    def build_ssh_command(self, test: bool = False):
        user = self.user_entry.get().strip() if hasattr(self, "user_entry") else self.config.get("VPS", "user", fallback="")
        host = self.ip_entry.get().strip() if hasattr(self, "ip_entry") else self.config.get("VPS", "ip", fallback="")
        ports = self.ports_entry.get().strip() if hasattr(self, "ports_entry") else self.config.get("Tunnel", "ports", fallback="")
        return build_ssh_command(
            user,
            host,
            ports,
            test=test,
            host_key_policy=self.host_key_policy(),
            debug=DEBUG_MODE,
        )

    def add_tunnel_dialog(self) -> None:
        prefill = None
        try:
            if hasattr(self, "tunnel_tree"):
                selection = self.tunnel_tree.selection()
                if selection:
                    connection_text = self.get_connection_for_item(selection[0])
                    if connection_text and "@" in connection_text:
                        user, host = connection_text.split("@", 1)
                        prefill = {"user": user, "host": host}
        except Exception:
            pass
        self.tunnel_config_dialog(prefill=prefill)

    def edit_selected_tunnel(self) -> None:
        item = self._selected_tunnel_item()
        if not item:
            return
        name = self.tunnel_tree.item(item, "values")[0]
        if name in self._saved_tunnels:
            self.tunnel_config_dialog(self._saved_tunnels[name])
        else:
            self.notify("Cannot Edit", "This is not a saved configuration.", "warning")

    def start_selected_tunnel(self) -> None:
        item = self._selected_tunnel_item()
        if not item:
            return
        values = self.tunnel_tree.item(item, "values")
        name, status = values[0], values[3]
        if "Active" in status or "External" in status:
            self.notify("Already Running", f"'{name}' is already running.")
            return
        if name in self._saved_tunnels:
            self.start_saved_tunnel(name)
        else:
            self.notify("Cannot Start", "This tunnel configuration is not available.", "warning")

    def delete_selected_tunnel(self) -> None:
        item = self._selected_tunnel_item()
        if not item:
            return
        values = self.tunnel_tree.item(item, "values")
        name, status = values[0], values[3]
        if "Active" in status or "External" in status:
            self.notify("Cannot Delete", "Stop the tunnel before deleting its configuration.", "warning")
            return
        if name not in self._saved_tunnels:
            self.notify("Cannot Delete", "This is not a saved configuration.", "warning")
            return
        if not self.confirm("Delete Tunnel", f"Delete saved tunnel '{name}'?"):
            return
        try:
            self.delete_tunnel_config(name)
            self.refresh_tunnels()
            self.notify("Deleted", f"Tunnel '{name}' removed.")
        except Exception as exc:
            self.notify("Error", f"Failed to delete tunnel: {exc}", "error")

    def tunnel_config_dialog(self, existing_config=None, prefill=None):
        return tunnel_ui.tunnel_config_dialog(self, existing_config, prefill)

    def start_saved_tunnel(self, tunnel_name: str, notify: bool = True) -> None:
        if tunnel_name not in self._saved_tunnels:
            if notify:
                self.notify("Error", f"Tunnel '{tunnel_name}' not found.", "error")
            return
        if self._is_saved_tunnel_running(tunnel_name):
            if notify:
                self.notify("Already Running", f"Tunnel '{tunnel_name}' is already running.")
            return

        config = self._saved_tunnels[tunnel_name]
        cleanup = None
        try:
            ssh_port = int(config.get("ssh_port") or 22)
        except (TypeError, ValueError):
            ssh_port = 22
        try:
            cmd = build_ssh_command(
                config["user"],
                config["host"],
                config["ports"],
                auth_method=config.get("auth_method", "key"),
                identity_file=config.get("identity_file", ""),
                ssh_port=ssh_port,
                host_key_policy=self.host_key_policy(),
                debug=DEBUG_MODE,
            )
            env = None
            if config.get("auth_method") == "password":
                password = config.get("password") or ""
                cmd, env, cleanup = wrap_password_command(cmd, password)
                if cleanup:
                    self._askpass_cleanup.append(cleanup)

            logging.info("Starting tunnel '%s': %s", tunnel_name, redact_command(cmd))
            process = self._popen_with_logging(cmd, env=env)

            deadline = time.time() + 2.5
            while time.time() < deadline:
                if process.poll() is not None:
                    raise ValueError("SSH exited immediately. Check the Activity Log for details.")
                time.sleep(0.2)

            self._active_tunnels[tunnel_name] = {
                "process": process,
                "config": config,
                "start_time": time.time(),
            }
            self.save_tunnel_pid(tunnel_name, process.pid)
            logging.info("Started tunnel '%s' (PID %s)", tunnel_name, process.pid)
            if notify:
                self.notify("Started", f"Tunnel '{tunnel_name}' started.\nPID: {process.pid}")
            self.ui(self.refresh_tunnels)
            self._refresh_tray_menu()
        except Exception as exc:
            logging.error("Failed to start tunnel '%s': %s", tunnel_name, exc)
            if notify:
                self.notify("Error", f"Failed to start tunnel '{tunnel_name}': {exc}", "error")
        finally:
            if cleanup:
                threading.Timer(15.0, cleanup_path, args=(cleanup,)).start()

    def _creation_flags(self) -> int:
        if os.name != "nt":
            return 0
        flags = subprocess.CREATE_NEW_PROCESS_GROUP
        if hasattr(subprocess, "DETACHED_PROCESS"):
            flags |= subprocess.DETACHED_PROCESS
        return flags

    def _popen_with_logging(self, cmd, env=None, creation_flags=None):
        if creation_flags is None:
            creation_flags = self._creation_flags()
        try:
            proc = subprocess.Popen(
                cmd,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                env=env,
                creationflags=creation_flags,
            )
        except Exception as exc:
            logging.error("Failed to launch process: %s", exc)
            raise
        threading.Thread(target=self._log_stream, args=(proc.stderr, logging.INFO, "[SSH] "), daemon=True).start()
        threading.Thread(target=self._log_stream, args=(proc.stdout, logging.DEBUG, "[SSH-OUT] "), daemon=True).start()
        return proc

    def _log_stream(self, stream, level, prefix=""):
        try:
            for line in iter(stream.readline, ""):
                if line:
                    logging.log(level, "%s%s", prefix, line.rstrip())
        except Exception as exc:
            logging.debug("Stream logger ended: %s", exc)
        finally:
            try:
                stream.close()
            except Exception:
                pass

    def quick_start_tunnel(self) -> None:
        try:
            user = self.user_entry.get().strip()
            host = self.ip_entry.get().strip()
            ports = self.ports_entry.get().strip()
            if not all([user, host, ports]):
                self.notify("Missing Information", "Fill in username, host, and ports first.", "error")
                return
            tunnel_config = {
                "name": "QuickStart",
                "user": user,
                "host": host,
                "ports": ports,
                "description": "Quick start tunnel from Connection tab",
                "auth_method": "key",
                "auto_start": False,
            }
            self.save_tunnel_config(tunnel_config)
            self.start_saved_tunnel("QuickStart")
            if hasattr(self, "notebook"):
                for index in range(self.notebook.index("end")):
                    if "Tunnels" in self.notebook.tab(index, "text"):
                        self.notebook.select(index)
                        break
        except Exception as exc:
            self.notify("Error", f"Failed to create quick start tunnel: {exc}", "error")

    def find_external_tunnels(self) -> None:
        try:
            processes = pu.list_external_tunnels()
            if not processes:
                self.notify("No External Tunnels", "No SSH reverse-tunnel processes found.")
                return
            self.show_external_tunnels_dialog(processes)
        except Exception as exc:
            self.notify("Error", f"Failed to find external tunnels: {exc}", "error")

    def show_external_tunnels_dialog(self, processes):
        return tunnel_ui.show_external_tunnels_dialog(self, processes)

    def start_tunnel(self) -> None:
        self.quick_start_tunnel()

    def start_tunnel_background(self) -> None:
        self._auto_start_tunnels()

    def _start_tunnel_process(self) -> None:
        self.quick_start_tunnel()

    def stop_tunnel(self) -> None:
        self.stop_all_tunnels()

    def _stop_tunnel_process(self) -> None:
        self.stop_all_tunnels(confirm=False, notify=False)

    def is_tunnel_running(self) -> bool:
        if any(info.get("process") and info["process"].poll() is None for info in self._active_tunnels.values()):
            return True
        return bool(self._get_cached_ssh_processes())

    def test_ssh_connection(self) -> None:
        try:
            cmd = self.build_ssh_command(test=True)
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=12)
            if result.returncode == 0:
                self.notify("SSH Test Success", "SSH key authentication is working.")
            else:
                error_msg = result.stderr or result.stdout or "Unknown error"
                if "Permission denied" in error_msg:
                    self.notify(
                        "SSH Test Failed",
                        "Key authentication failed. Check the username, host, and that ssh-agent has your key.\n\n"
                        + error_msg[:400],
                        "error",
                    )
                else:
                    self.notify("SSH Test Failed", error_msg[:400], "error")
        except subprocess.TimeoutExpired:
            self.notify("SSH Test Failed", "Connection timed out. Check host and network.", "error")
        except Exception as exc:
            self.notify("Error", str(exc), "error")

    def view_logs(self) -> None:
        try:
            if sys.platform == "win32":
                subprocess.Popen(["notepad.exe", LOG_FILE])
            else:
                subprocess.Popen(["xdg-open", LOG_FILE])
        except Exception as exc:
            self.notify("Error", f"Could not open log file: {exc}", "error")

    def update_log_display(self):
        return tunnel_ui.update_log_display(self)

    def _load_tray_image(self) -> Image.Image:
        if os.path.exists(ICON_FILE):
            try:
                return Image.open(ICON_FILE).convert("RGBA")
            except Exception:
                pass
        image = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
        draw = ImageDraw.Draw(image)
        draw.rounded_rectangle([8, 8, 56, 56], radius=12, fill=(46, 134, 171, 255))
        draw.rectangle([18, 28, 46, 36], fill="white")
        return image

    def create_tray_icon(self):
        self.icon = pystray.Icon("SSH Tunnel Manager", self._load_tray_image(), menu=self._build_tray_menu())
        return self.icon

    def _build_tray_menu(self):
        items = [pystray.MenuItem("Show", self.show_window, default=True), pystray.Menu.SEPARATOR]
        if self._saved_tunnels:
            for name in sorted(self._saved_tunnels):
                items.append(pystray.MenuItem(f"Start {name}", self._tray_start_handler(name)))
            items.append(pystray.Menu.SEPARATOR)
            items.append(pystray.MenuItem("Stop All Tunnels", self.stop_tunnel_tray))
        else:
            items.append(pystray.MenuItem("Start Tunnel", self.start_tunnel_tray))
            items.append(pystray.MenuItem("Stop Tunnel", self.stop_tunnel_tray))
        items.extend(
            [
                pystray.Menu.SEPARATOR,
                pystray.MenuItem("Exit", self.quit_app),
            ]
        )
        return pystray.Menu(*items)

    def _tray_start_handler(self, name: str):
        def _handler(icon=None, item=None):
            self.start_saved_tunnel(name, notify=False)

        return _handler

    def _refresh_tray_menu(self) -> None:
        if self.icon:
            try:
                self.icon.menu = self._build_tray_menu()
            except Exception:
                pass

    def minimize_to_tray(self) -> None:
        if self.headless or not hasattr(self, "root"):
            return
        self.root.withdraw()
        if not self.icon:
            self.icon = self.create_tray_icon()
            threading.Thread(target=self.icon.run, daemon=True).start()

    def show_window(self, icon=None, item=None) -> None:
        if self.headless or not hasattr(self, "root"):
            return
        self.root.deiconify()
        self.root.lift()
        self.root.focus_force()

    def on_closing(self) -> None:
        if getattr(self, "_launched_from_console", False) and not (
            hasattr(self, "minimize_to_tray_var") and self.minimize_to_tray_var.get()
        ):
            self.quit_app()
            return
        if hasattr(self, "minimize_to_tray_var") and self.minimize_to_tray_var.get():
            self.minimize_to_tray()
        else:
            self.quit_app()

    def start_tunnel_tray(self, icon=None, item=None) -> None:
        threading.Thread(target=self._auto_start_tunnels, daemon=True).start()

    def stop_tunnel_tray(self, icon=None, item=None) -> None:
        self.stop_all_tunnels(confirm=False, notify=False)

    def quit_app(self, icon=None, item=None) -> None:
        try:
            logging.info("Initiating shutdown")
            self.running = False
            if self.icon:
                try:
                    self.icon.stop()
                except Exception:
                    pass
                self.icon = None

            stop_on_exit = True
            try:
                stop_on_exit = self.config.getboolean("Settings", "stop_tunnels_on_exit", fallback=True)
            except Exception:
                pass
            if stop_on_exit:
                for name, info in list(self._active_tunnels.items()):
                    process = info.get("process")
                    if process and process.poll() is None:
                        try:
                            process.terminate()
                            process.wait(timeout=4)
                        except Exception:
                            try:
                                process.kill()
                            except Exception:
                                pass
                    self.clear_tunnel_pid(name)
                self._active_tunnels.clear()

            for path in list(self._askpass_cleanup):
                cleanup_path(path)
            close_file_logging()

            if not self.headless and hasattr(self, "root"):
                try:
                    self.root.quit()
                    self.root.destroy()
                except Exception:
                    pass
        finally:
            try:
                sys.exit(0)
            except SystemExit:
                pass
            os._exit(0)

    def update_tunnel_list(self) -> None:
        if self.headless or not hasattr(self, "tunnel_tree"):
            return
        try:
            selected = self.tunnel_tree.selection()
            selected_names = []
            for item in selected:
                values = self.tunnel_tree.item(item, "values")
                if values:
                    selected_names.append(values[0])

            for item in self.tunnel_tree.get_children():
                self.tunnel_tree.delete(item)

            processes = self._get_cached_ssh_processes()
            connections: Dict[str, list] = {}
            for name, config in self._saved_tunnels.items():
                connections.setdefault(f"{config['user']}@{config['host']}", []).append((name, config))

            active_count = 0
            for user_host, items in sorted(connections.items()):
                parent_id = self.tunnel_tree.insert("", "end", text=user_host, open=True)
                for name, config in items:
                    running_pid = None
                    duration = "-"
                    status = "Stopped"

                    tracked = self._active_tunnels.get(name)
                    if tracked:
                        process = tracked.get("process")
                        if process and process.poll() is None:
                            running_pid = process.pid
                            duration = self.format_duration(time.time() - tracked["start_time"])
                            status = "Active"
                            active_count += 1
                        else:
                            self._active_tunnels.pop(name, None)
                            self.clear_tunnel_pid(name)

                    if not running_pid:
                        match = pu.find_matching_process(
                            processes,
                            user_host,
                            config.get("ports", ""),
                            int(config["last_pid"]) if str(config.get("last_pid") or "").isdigit() else None,
                        )
                        if match:
                            running_pid = match["pid"]
                            duration = self.format_duration(time.time() - match["create_time"])
                            status = "External"
                            active_count += 1

                    child = self.tunnel_tree.insert(
                        parent_id,
                        "end",
                        values=(
                            name,
                            config.get("ports", ""),
                            config.get("description", ""),
                            status,
                            running_pid if running_pid else "-",
                            duration,
                        ),
                    )
                    if name in selected_names:
                        self.tunnel_tree.selection_add(child)

            if hasattr(self, "tunnel_count_label"):
                self.tunnel_count_label.config(
                    text=f"Tunnels: {len(self._saved_tunnels)} ({active_count} running)"
                )
        except Exception as exc:
            logging.error("Error updating tunnel list: %s", exc)

    def extract_user_host(self, cmdline: str) -> str:
        return pu.extract_user_host(cmdline)

    def extract_port_mappings(self, cmdline: str) -> str:
        return pu.extract_port_mappings(cmdline)

    def format_duration(self, seconds) -> str:
        return pu.format_duration(int(seconds))

    def refresh_tunnels(self) -> None:
        self._get_cached_ssh_processes(force_refresh=True)
        self.update_tunnel_list()
        self.update_status_bar()

    def refresh_tunnels_quiet(self) -> None:
        self._get_cached_ssh_processes(force_refresh=True)
        self.update_tunnel_list()
        self.update_status_bar()

    def _selected_tunnel_item(self) -> Optional[str]:
        if not hasattr(self, "tunnel_tree"):
            return None
        selection = self.tunnel_tree.selection()
        if not selection:
            self.notify("No Selection", "Select a tunnel first.", "warning")
            return None
        item = selection[0]
        if not self.tunnel_tree.item(item, "values"):
            self.notify("Invalid Selection", "Select a specific tunnel under a connection.", "warning")
            return None
        return item

    def stop_selected_tunnel(self) -> None:
        item = self._selected_tunnel_item()
        if not item:
            return
        values = self.tunnel_tree.item(item, "values")
        name = values[0]
        pid_str = values[4]
        if pid_str == "-":
            self.notify("No Process", "This tunnel is not currently running.", "warning")
            return
        try:
            pid = int(pid_str)
        except ValueError:
            self.notify("Error", "Invalid process ID.", "error")
            return
        if pu.terminate_pid(pid):
            self.clear_tunnel_pid(name)
            self._active_tunnels.pop(name, None)
            logging.info("Stopped tunnel '%s' (PID %s)", name, pid)
            self.refresh_tunnels()
            self.notify("Stopped", f"Tunnel '{name}' stopped.")
        else:
            self.notify("Error", f"Failed to stop tunnel '{name}'.", "error")

    def get_connection_for_item(self, item_id: str) -> str:
        try:
            parent = self.tunnel_tree.parent(item_id)
            if parent:
                return self.tunnel_tree.item(parent, "text") or ""
            return self.tunnel_tree.item(item_id, "text") or ""
        except Exception:
            return ""

    def stop_all_tunnels(self, confirm: bool = True, notify: bool = True) -> None:
        try:
            if confirm and not self.confirm("Stop All Tunnels", "Stop every detected SSH reverse tunnel?"):
                return
            count = pu.stop_all_ssh_tunnels(timeout=4.0)
            for name in list(self._active_tunnels):
                self.clear_tunnel_pid(name)
            self._active_tunnels.clear()
            self._get_cached_ssh_processes(force_refresh=True)
            self.ui(self.refresh_tunnels)
            if notify:
                if count:
                    self.notify("Stopped", f"Stopped {count} SSH tunnel process(es).")
                else:
                    self.notify("No Tunnels", "No SSH tunnels were running.")
        except Exception as exc:
            logging.error("Error stopping all tunnels: %s", exc)
            if notify:
                self.notify("Error", f"Failed to stop tunnels: {exc}", "error")

    def view_tunnel_details(self):
        return tunnel_ui.view_tunnel_details(self)

    def restart_selected_tunnel(self) -> None:
        item = self._selected_tunnel_item()
        if not item:
            return
        values = self.tunnel_tree.item(item, "values")
        name = values[0]
        pid_str = values[4]
        if name not in self._saved_tunnels:
            self.notify("Not Managed", "This tunnel is not a saved configuration.", "warning")
            return
        if pid_str and pid_str != "-":
            try:
                pu.terminate_pid(int(pid_str))
            except ValueError:
                pass
            self.clear_tunnel_pid(name)
            self._active_tunnels.pop(name, None)
            time.sleep(0.4)
        self.start_saved_tunnel(name)

    def show_tunnel_context_menu(self, event):
        return tunnel_ui.show_tunnel_context_menu(self, event)

    def restart_all_under_connection(self) -> None:
        try:
            selection = self.tunnel_tree.selection()
            if not selection:
                self.notify("No Selection", "Select a connection or a tunnel under it.", "warning")
                return
            item = selection[0]
            values = self.tunnel_tree.item(item, "values")
            parent = item if not values else self.tunnel_tree.parent(item)
            if not parent:
                self.notify("Invalid Selection", "Select a connection group or a tunnel under it.", "warning")
                return
            connection = self.tunnel_tree.item(parent, "text")
            restarted = 0
            errors = []
            for child in self.tunnel_tree.get_children(parent):
                cvals = self.tunnel_tree.item(child, "values")
                if not cvals:
                    continue
                name = cvals[0]
                pid_str = cvals[4] if len(cvals) > 4 else "-"
                try:
                    if pid_str and pid_str != "-":
                        pu.terminate_pid(int(pid_str))
                except Exception as exc:
                    errors.append(f"{name}: stop error {exc}")
                try:
                    if name in self._saved_tunnels:
                        time.sleep(0.2)
                        self.start_saved_tunnel(name, notify=False)
                        restarted += 1
                    else:
                        errors.append(f"{name}: not a saved configuration")
                except Exception as exc:
                    errors.append(f"{name}: start error {exc}")
            self.refresh_tunnels()
            if errors:
                self.notify(
                    "Restart Completed with Issues",
                    f"Connection: {connection}\nRestarted: {restarted}\n" + "\n".join(errors[:8]),
                    "warning",
                )
            else:
                self.notify("Restarted", f"Restarted {restarted} tunnel(s) under {connection}.")
        except Exception as exc:
            self.notify("Error", f"Failed to restart all tunnels: {exc}", "error")

    def copy_tunnel_command(self):
        return tunnel_ui.copy_tunnel_command(self)

    def update_status_bar(self):
        return tunnel_ui.update_status_bar(self)

    def open_config_file(self) -> None:
        try:
            if not os.path.exists(CONFIG_FILE):
                self.persist_config()
            if sys.platform == "win32":
                os.startfile(CONFIG_FILE)  # type: ignore[attr-defined]
            else:
                subprocess.Popen(["xdg-open", CONFIG_FILE])
        except Exception as exc:
            self.notify("Error", f"Could not open config file: {exc}", "error")

    def clear_logs(self) -> None:
        try:
            close_file_logging()
            try:
                with open(LOG_FILE, "w", encoding="utf-8"):
                    pass
                if hasattr(self, "log_text"):
                    self.log_text.delete("1.0", "end")
                setup_file_logging()
                logging.info("Log file cleared")
                self.notify("Success", "Logs cleared.")
            except Exception:
                setup_file_logging()
                raise
        except Exception as exc:
            self.notify("Error", f"Failed to clear logs: {exc}", "error")

    def enable_startup(self) -> None:
        if winreg is None:
            logging.warning("Windows startup integration is only available on Windows.")
            return
        try:
            key = winreg.OpenKey(
                winreg.HKEY_CURRENT_USER,
                r"SOFTWARE\Microsoft\Windows\CurrentVersion\Run",
                0,
                winreg.KEY_SET_VALUE,
            )
            if getattr(sys, "frozen", False):
                app_path = f'"{sys.executable}" --headless'
            else:
                app_path = f'"{sys.executable}" "{os.path.abspath(__file__)}" --headless'
            winreg.SetValueEx(key, "SSH Tunnel Manager", 0, winreg.REG_SZ, app_path)
            winreg.CloseKey(key)
            logging.info("Added to Windows startup")
        except Exception as exc:
            logging.error("Failed to add to startup: %s", exc)
            self.notify("Startup Error", f"Could not enable Windows startup: {exc}", "error")

    def disable_startup(self) -> None:
        if winreg is None:
            return
        try:
            key = winreg.OpenKey(
                winreg.HKEY_CURRENT_USER,
                r"SOFTWARE\Microsoft\Windows\CurrentVersion\Run",
                0,
                winreg.KEY_SET_VALUE,
            )
            winreg.DeleteValue(key, "SSH Tunnel Manager")
            winreg.CloseKey(key)
            logging.info("Removed from Windows startup")
        except FileNotFoundError:
            pass
        except Exception as exc:
            logging.error("Failed to remove from startup: %s", exc)

    def is_startup_enabled(self) -> bool:
        if winreg is None:
            return False
        try:
            key = winreg.OpenKey(
                winreg.HKEY_CURRENT_USER,
                r"SOFTWARE\Microsoft\Windows\CurrentVersion\Run",
                0,
                winreg.KEY_READ,
            )
            winreg.QueryValueEx(key, "SSH Tunnel Manager")
            winreg.CloseKey(key)
            return True
        except FileNotFoundError:
            return False
        except Exception:
            return False

    def run(self) -> None:
        if self.headless:
            logging.info("Starting in headless mode")
            self.icon = self.create_tray_icon()
            try:
                self.icon.run_detached()
            except AttributeError:
                threading.Thread(target=self.icon.run, daemon=True).start()
                try:
                    while self.running:
                        time.sleep(0.5)
                except KeyboardInterrupt:
                    pass
                finally:
                    self.quit_app()
        else:
            self.root.mainloop()


def main() -> None:
    parser = argparse.ArgumentParser(description="SSH Reverse Tunnel Manager")
    parser.add_argument("--headless", action="store_true", help="Run in system tray only")
    parser.add_argument("--debug", action="store_true", help="Enable verbose console and SSH logging")
    args = parser.parse_args()

    app = TunnelManager(headless=args.headless)

    def signal_handler(signum, frame):
        logging.info("Received signal %s, shutting down", signum)
        app.quit_app()

    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)

    try:
        app.run()
    except KeyboardInterrupt:
        app.quit_app()
    except Exception as exc:
        logging.error("Unexpected error in main: %s", exc)
        app.quit_app()


if __name__ == "__main__":
    main()
