import tkinter as tk
from tkinter import messagebox
import tunnel_ui
import subprocess
import os
import psutil
import configparser
import time
import threading
import sys
from PIL import Image, ImageDraw
import pystray
import winreg
import logging
from datetime import datetime
import argparse
import signal
from logger_utils import init_logging, setup_file_logging, close_file_logging, LOG_FILE
from config_store import (
    load_saved_tunnels as cs_load_saved_tunnels,
    save_tunnel_config as cs_save_tunnel_config,
    delete_tunnel_config as cs_delete_tunnel_config,
    save_tunnel_pid as cs_save_tunnel_pid,
    clear_tunnel_pid as cs_clear_tunnel_pid,
    encrypt_password as cs_encrypt_password,
    decrypt_password as cs_decrypt_password,
)
import process_utils as pu

# Config file to save settings - use absolute path to ensure persistence
# Detect if running as executable or script and use appropriate directory
if getattr(sys, 'frozen', False):
    # Running as executable (PyInstaller)
    APP_DIR = os.path.dirname(sys.executable)
else:
    # Running as script
    APP_DIR = os.path.dirname(os.path.abspath(__file__))

CONFIG_FILE = os.path.join(APP_DIR, 'tunnel_manager.ini')

# Initialize base logging (console only if debug flag present)
DEBUG_MODE = ('--debug' in sys.argv)
init_logging(DEBUG_MODE)

# Silence GUI popups: route messagebox show* to logging only
try:
    def _mb_showinfo(title, message):
        logging.info(f"{title}: {message}")
    def _mb_showwarning(title, message):
        logging.warning(f"{title}: {message}")
    def _mb_showerror(title, message):
        logging.error(f"{title}: {message}")
    messagebox.showinfo = _mb_showinfo
    messagebox.showwarning = _mb_showwarning
    messagebox.showerror = _mb_showerror
except Exception:
    pass

class TunnelManager:
    def __init__(self, headless=False):
        self.headless = headless
        self.process = None
        self.config = configparser.ConfigParser()
        # Detect if launched from a console/terminal to decide close behavior
        try:
            self._launched_from_console = sys.stdout.isatty()
        except Exception:
            self._launched_from_console = False
        
        # Setup file logging after initialization
        setup_file_logging()
        
        self.load_config()
        
        # System tray icon
        self.icon = None
        self.running = True
        
        # Resource optimization - process caching
        self._tunnel_cache = {}
        self._last_scan_time = 0
        self._cache_ttl = 60  # Cache SSH processes for 60 seconds (increased for performance)
        self._window_visible = True
        self._update_timers = {}
        
        # Tunnel management data structures
        self._saved_tunnels = {}  # Store user-defined tunnel configurations
        self._active_tunnels = {}  # Track tunnels started by this app
        self.load_saved_tunnels()
        
        # GUI elements (only if not headless)
        if not headless:
            # Delegate UI construction to tunnel_ui module
            tunnel_ui.create_widgets(self)
        
        # Auto-start tunnel if enabled
        if self.config.getboolean('Settings', 'auto_start', fallback=False):
            self.start_tunnel_background()
    
    def create_tray_icon(self):
        """Create system tray icon"""
        # Create a simple icon
        image = Image.new('RGB', (64, 64), color='blue')
        draw = ImageDraw.Draw(image)
        draw.rectangle([16, 16, 48, 48], fill='white')
        draw.text((20, 25), "SSH", fill='black')
        
        # Create menu
        menu = pystray.Menu(
            pystray.MenuItem("Show", self.show_window, default=True),
            pystray.MenuItem("Start Tunnel", self.start_tunnel_tray),
            pystray.MenuItem("Stop Tunnel", self.stop_tunnel_tray),
            pystray.MenuItem("Status", self.check_status_tray),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("Exit", self.quit_app)
        )
        
        self.icon = pystray.Icon("SSH Tunnel Manager", image, menu=menu)
        return self.icon
    
    def load_config(self):
        if os.path.exists(CONFIG_FILE):
            self.config.read(CONFIG_FILE)
        else:
            self.config['VPS'] = {'user': '', 'ip': ''}
            self.config['Tunnel'] = {'ports': ''}
            self.config['Settings'] = {
                'auto_start': 'False',
                'minimize_to_tray': 'True',
                'start_with_windows': 'False'
            }
    
    def load_saved_tunnels(self):
        """Load saved tunnel configurations from config file"""
        try:
            logging.info(f"Loading tunnels from config file: {CONFIG_FILE}")
            
            if os.path.exists(CONFIG_FILE):
                config = configparser.ConfigParser()
                config.read(CONFIG_FILE)
                
                logging.info(f"Config file sections found: {config.sections()}")
                
                # Load tunnel configurations
                tunnel_count = 0
                for section in config.sections():
                    if section.startswith('Tunnel_'):
                        tunnel_name = section[7:]  # Remove 'Tunnel_' prefix
                        tunnel_config = {
                            'name': tunnel_name,
                            'user': config.get(section, 'user', fallback=''),
                            'host': config.get(section, 'host', fallback=''),
                            'ports': config.get(section, 'ports', fallback=''),
                            'description': config.get(section, 'description', fallback=''),
                            'last_pid': config.get(section, 'last_pid', fallback=None),
                            'last_started': config.get(section, 'last_started', fallback=None)
                        }
                        self._saved_tunnels[tunnel_name] = tunnel_config
                        tunnel_count += 1
                        logging.info(f"Loaded tunnel: {tunnel_name} -> {tunnel_config['user']}@{tunnel_config['host']} (last PID: {tunnel_config['last_pid']})")
                
                logging.info(f"Successfully loaded {tunnel_count} saved tunnel configurations")
                
                # Auto-detect external tunnels that match saved configurations
                self.detect_external_tunnels()
            else:
                logging.info(f"Config file does not exist: {CONFIG_FILE}")
                
        except Exception as e:
            logging.error(f"Error loading saved tunnels: {e}")
            import traceback
            logging.error(f"Traceback: {traceback.format_exc()}")
    
    def detect_external_tunnels(self):
        """Auto-detect external SSH tunnels that match saved configurations"""
        try:
            logging.info("Auto-detecting external tunnels...")
            detected_count = 0
            
            # Get all running SSH processes via process_utils
            ssh_processes = pu.scan_ssh_tunnels()
            
            # Match running processes to saved tunnel configurations
            for tunnel_name, config in self._saved_tunnels.items():
                user_host = f"{config['user']}@{config['host']}"
                ports = config['ports']
                last_pid = config.get('last_pid')
                
                # First, check if the last known PID is still running
                if last_pid:
                    try:
                        last_pid = int(last_pid)
                        if last_pid in ssh_processes:
                            proc_info = ssh_processes[last_pid]
                            if (user_host in proc_info['user_host'] and 
                                ports in proc_info['ports']):
                                # Found exact match by PID
                                logging.info(f"Detected tunnel '{tunnel_name}' by PID {last_pid}")
                                detected_count += 1
                                continue
                        else:
                            # PID no longer exists, clear it
                            self.clear_tunnel_pid(tunnel_name)
                    except (ValueError, TypeError):
                        pass
                
                # If PID match failed, try to match by connection details
                for proc_info in ssh_processes.values():
                    if (user_host in proc_info['user_host'] and 
                        ports in proc_info['ports']):
                        # Found match by connection details
                        logging.info(f"Detected tunnel '{tunnel_name}' by connection match (PID: {proc_info['pid']})")
                        # Update the PID in config
                        self.save_tunnel_pid(tunnel_name, proc_info['pid'])
                        detected_count += 1
                        break
            
            logging.info(f"Auto-detection complete: {detected_count} external tunnels matched to saved configurations")
            
        except Exception as e:
            logging.error(f"Error during external tunnel detection: {e}")
            import traceback
            logging.error(f"Traceback: {traceback.format_exc()}")
    
    def save_tunnel_config(self, tunnel_config):
        """Save a tunnel configuration to the config file"""
        try:
            logging.info(f"Saving tunnel configuration: {tunnel_config['name']} to {CONFIG_FILE}")
            
            config = configparser.ConfigParser()
            if os.path.exists(CONFIG_FILE):
                config.read(CONFIG_FILE)
                logging.info(f"Existing config sections: {config.sections()}")
            else:
                logging.info("Creating new config file")
            
            section_name = f"Tunnel_{tunnel_config['name']}"
            if not config.has_section(section_name):
                config.add_section(section_name)
                logging.info(f"Added new section: {section_name}")
            
            config.set(section_name, 'user', tunnel_config['user'])
            config.set(section_name, 'host', tunnel_config['host'])
            config.set(section_name, 'ports', tunnel_config['ports'])
            config.set(section_name, 'description', tunnel_config.get('description', ''))
            
            with open(CONFIG_FILE, 'w') as f:
                config.write(f)
            
            logging.info(f"Config file written successfully. File size: {os.path.getsize(CONFIG_FILE)} bytes")
            
            # Update in-memory storage
            self._saved_tunnels[tunnel_config['name']] = tunnel_config
            logging.info(f"Saved tunnel configuration: {tunnel_config['name']} -> {tunnel_config['user']}@{tunnel_config['host']}")
            logging.info(f"Total saved tunnels in memory: {len(self._saved_tunnels)}")
            
        except Exception as e:
            logging.error(f"Error saving tunnel config: {e}")
            import traceback
            logging.error(f"Traceback: {traceback.format_exc()}")
            raise
    
    def save_tunnel_pid(self, tunnel_name, pid):
        """Save tunnel PID to config file for tracking across restarts"""
        try:
            logging.info(f"Attempting to save PID {pid} for tunnel '{tunnel_name}' to {CONFIG_FILE}")
            
            config = configparser.ConfigParser()
            if os.path.exists(CONFIG_FILE):
                config.read(CONFIG_FILE)
                logging.info(f"Loaded existing config with sections: {config.sections()}")
            else:
                logging.info("Config file does not exist, creating new one")
            
            section_name = f"Tunnel_{tunnel_name}"
            
            # Ensure section exists
            if not config.has_section(section_name):
                logging.warning(f"Section '{section_name}' does not exist, cannot save PID")
                return
            
            # Save PID and timestamp
            config.set(section_name, 'last_pid', str(pid))
            config.set(section_name, 'last_started', str(int(time.time())))
            
            # Write to file
            with open(CONFIG_FILE, 'w') as f:
                config.write(f)
            
            logging.info(f"Successfully saved PID {pid} for tunnel '{tunnel_name}' to config file")
            
            # Verify the save worked
            if os.path.exists(CONFIG_FILE):
                verify_config = configparser.ConfigParser()
                verify_config.read(CONFIG_FILE)
                if verify_config.has_section(section_name) and verify_config.has_option(section_name, 'last_pid'):
                    saved_pid = verify_config.get(section_name, 'last_pid')
                    logging.info(f"Verification: PID {saved_pid} confirmed saved for tunnel '{tunnel_name}'")
                else:
                    logging.error(f"Verification failed: PID not found in saved config for tunnel '{tunnel_name}'")
            
        except Exception as e:
            logging.error(f"Error saving tunnel PID: {e}")
            import traceback
            logging.error(f"Traceback: {traceback.format_exc()}")
    
    def clear_tunnel_pid(self, tunnel_name):
        """Clear tunnel PID from config when tunnel stops"""
        try:
            config = configparser.ConfigParser()
            if os.path.exists(CONFIG_FILE):
                config.read(CONFIG_FILE)
            
            section_name = f"Tunnel_{tunnel_name}"
            if config.has_section(section_name):
                if config.has_option(section_name, 'last_pid'):
                    config.remove_option(section_name, 'last_pid')
                if config.has_option(section_name, 'last_started'):
                    config.remove_option(section_name, 'last_started')
                
                with open(CONFIG_FILE, 'w') as f:
                    config.write(f)
                
                logging.info(f"Cleared PID for tunnel '{tunnel_name}'")
        except Exception as e:
            logging.error(f"Error clearing tunnel PID: {e}")
    
    def delete_tunnel_config(self, tunnel_name):
        """Delete a tunnel configuration"""
        try:
            config = configparser.ConfigParser()
            if os.path.exists(CONFIG_FILE):
                config.read(CONFIG_FILE)
            
            section_name = f"Tunnel_{tunnel_name}"
            if config.has_section(section_name):
                config.remove_section(section_name)
                
                with open(CONFIG_FILE, 'w') as f:
                    config.write(f)
            
            # Remove from in-memory storage
            if tunnel_name in self._saved_tunnels:
                del self._saved_tunnels[tunnel_name]
            
            logging.info(f"Deleted tunnel configuration: {tunnel_name}")
            
        except Exception as e:
            logging.error(f"Error deleting tunnel config: {e}")
            raise
    
    def save_config(self):
        self.config['VPS'] = {
            'user': self.user_entry.get(),
            'ip': self.ip_entry.get()
        }
        self.config['Tunnel'] = {
            'ports': self.ports_entry.get()
        }
        self.config['Settings'] = {
            'auto_start': str(self.auto_start_var.get()),
            'minimize_to_tray': str(self.minimize_to_tray_var.get()),
            'start_with_windows': str(self.start_with_windows_var.get())
        }
        
        with open(CONFIG_FILE, 'w') as f:
            self.config.write(f)
        
        # Handle Windows startup
        if self.start_with_windows_var.get():
            self.enable_startup()
        else:
            self.disable_startup()
        
        messagebox.showinfo("Success", "Configuration saved!")
        logging.info("Configuration saved")
    
    def build_ssh_command(self, test=False):
        user = self.user_entry.get() if hasattr(self, 'user_entry') else self.config.get('VPS', 'user')
        ip = self.ip_entry.get() if hasattr(self, 'ip_entry') else self.config.get('VPS', 'ip')
        ports = self.ports_entry.get().strip() if hasattr(self, 'ports_entry') else self.config.get('Tunnel', 'ports')
        
        if not user or not ip or not ports:
            raise ValueError("All fields are required!")
        
        # Build -R flags for multiple ports
        r_flags = []
        for pair in ports.split(','):
            pair = pair.strip()
            if ':' in pair:
                remote, local = pair.split(':')
                r_flags.extend(['-R', f'{remote.strip()}:127.0.0.1:{local.strip()}'])
        
        # SSH command with connection options
        base_cmd = ['ssh',
                   '-vvv',
                   '-o', 'ExitOnForwardFailure=yes', 
                   '-o', 'StrictHostKeyChecking=no', 
                   '-o', 'UserKnownHostsFile=NUL',
                   '-o', 'ConnectTimeout=10',
                   '-o', 'ServerAliveInterval=60',
                   '-o', 'ServerAliveCountMax=3'] + r_flags + ['-N', f'{user}@{ip}']
        
        if test:
            return ['ssh', '-o', 'ConnectTimeout=5', '-o', 'BatchMode=yes', f'{user}@{ip}', 'echo', 'Connection test successful']
        
        return base_cmd
    
    def start_tunnel(self):
        """Start tunnel from GUI"""
        try:
            if self.is_tunnel_running():
                raise ValueError("Tunnel is already running!")
            
            self._start_tunnel_process()
            if not self.headless:
                messagebox.showinfo("Success", f"Tunnel started! PID: {self.process.pid}")
            
        except Exception as e:
            error_msg = str(e)
            logging.error(f"Failed to start tunnel: {error_msg}")
            if not self.headless:
                self.status_label.config(text="Status: Error")
                messagebox.showerror("Error", error_msg)
    
    def start_tunnel_background(self):
        """Start tunnel in background without GUI messages"""
        try:
            if self.is_tunnel_running():
                logging.info("Tunnel is already running")
                return
            
            self._start_tunnel_process()
            logging.info(f"Tunnel started in background, PID: {self.process.pid}")
            
        except Exception as e:
            logging.error(f"Failed to start tunnel in background: {e}")
    
    def _start_tunnel_process(self):
        """Internal method to start the SSH tunnel process"""
        cmd = self.build_ssh_command()
        logging.info(f"Starting SSH command: {' '.join(cmd)}")
        
        # Use DETACHED_PROCESS to prevent console window without breaking SSH functionality
        creation_flags = 0
        if os.name == 'nt':
            creation_flags = subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.DETACHED_PROCESS

        # Start process with logging of stdout/stderr (-vvv output)
        self.process = self._popen_with_logging(cmd, creation_flags)
        
        # Give it a moment to start and check if it failed immediately
        time.sleep(2)
        
        if self.process.poll() is not None:
            raise ValueError("SSH failed to start: process exited immediately")
        
        if not self.headless:
            self.status_label.config(text="Status: Running")
        
        # Start monitoring thread
        threading.Thread(target=self.monitor_tunnel, daemon=True).start()
    
    def monitor_tunnel(self):
        """Monitor tunnel process and restart if needed"""
        while self.running and self.process:
            if self.process.poll() is not None:
                # Process has died
                logging.warning("Tunnel process died, attempting restart...")
                if not self.headless:
                    self.status_label.config(text="Status: Reconnecting...")
                
                time.sleep(5)  # Wait before restart
                
                if self.config.getboolean('Settings', 'auto_start', fallback=False):
                    try:
                        self._start_tunnel_process()
                        logging.info("Tunnel restarted successfully")
                    except Exception as e:
                        logging.error(f"Failed to restart tunnel: {e}")
                        if not self.headless:
                            self.status_label.config(text="Status: Failed")
                break
            
            time.sleep(60)  # Check every 60 seconds (reduced frequency)
    
    def _get_cached_ssh_processes(self, force_refresh=False):
        """Get SSH processes with caching to reduce resource usage"""
        current_time = time.time()
        
        # Return cached data if still valid
        if not force_refresh and (current_time - self._last_scan_time) < self._cache_ttl:
            return self._tunnel_cache
        
        # Perform fresh scan via process_utils
        try:
            scanned = pu.scan_ssh_tunnels()
            # ensure dict[int, dict]
            self._tunnel_cache = dict(scanned)
        except Exception as e:
            logging.error(f"Error scanning SSH processes: {e}")
            self._tunnel_cache = {}

        self._last_scan_time = current_time
        return self._tunnel_cache
    
    # Tunnel Management Methods
    def add_tunnel_dialog(self):
        """Open dialog to add a new tunnel configuration. Prefills connection if selected."""
        prefill = None
        try:
            if hasattr(self, 'tunnel_tree'):
                selection = self.tunnel_tree.selection()
                if selection:
                    item = selection[0]
                    # Determine connection text (user@host)
                    connection_text = self.get_connection_for_item(item)
                    if connection_text and '@' in connection_text:
                        user, host = connection_text.split('@', 1)
                        prefill = {'user': user, 'host': host}
        except Exception:
            pass
        self.tunnel_config_dialog(prefill=prefill)
    
    def edit_selected_tunnel(self):
        """Edit the selected tunnel configuration"""
        selection = self.tunnel_tree.selection()
        if not selection:
            messagebox.showwarning("No Selection", "Please select a tunnel to edit.")
            return
        
        item = selection[0]
        values = self.tunnel_tree.item(item, 'values')
        tunnel_name = values[0]
        
        if tunnel_name in self._saved_tunnels:
            self.tunnel_config_dialog(self._saved_tunnels[tunnel_name])
        else:
            messagebox.showwarning("Cannot Edit", "This is a running process, not a saved configuration.")
    
    def start_selected_tunnel(self):
        """Start the selected tunnel"""
        selection = self.tunnel_tree.selection()
        if not selection:
            messagebox.showwarning("No Selection", "Please select a tunnel to start.")
            return
        
        item = selection[0]
        values = self.tunnel_tree.item(item, 'values')
        tunnel_name = values[0]
        status = values[3]
        
        # Prevent starting if already active or detected externally
        if "Active" in status or "External" in status:
            messagebox.showinfo("Already Running", "This tunnel is already running.")
            return
        
        if tunnel_name in self._saved_tunnels:
            self.start_saved_tunnel(tunnel_name)
        else:
            messagebox.showwarning("Cannot Start", "This tunnel configuration is not available.")
    
    def delete_selected_tunnel(self):
        """Delete the selected tunnel configuration"""
        selection = self.tunnel_tree.selection()
        if not selection:
            messagebox.showwarning("No Selection", "Please select a tunnel to delete.")
            return
        
        item = selection[0]
        values = self.tunnel_tree.item(item, 'values')
        tunnel_name = values[0]
        status = values[3]
        
        # Do not allow deleting if running (active or external)
        if "Active" in status or "External" in status:
            messagebox.showwarning("Cannot Delete", "Stop the tunnel before deleting its configuration.")
            return
        
        if tunnel_name in self._saved_tunnels:
            try:
                self.delete_tunnel_config(tunnel_name)
                messagebox.showinfo("Success", f"Tunnel '{tunnel_name}' deleted successfully.")
                self.refresh_tunnels()
            except Exception as e:
                messagebox.showerror("Error", f"Failed to delete tunnel: {e}")
        else:
            messagebox.showwarning("Cannot Delete", "This is a running process, not a saved configuration.")
    
    def tunnel_config_dialog(self, existing_config=None, prefill=None):
        """Open dialog to add/edit tunnel configuration (delegates to tunnel_ui)."""
        return tunnel_ui.tunnel_config_dialog(self, existing_config, prefill)
    
    def start_saved_tunnel(self, tunnel_name):
        """Start a saved tunnel configuration"""
        if tunnel_name not in self._saved_tunnels:
            messagebox.showerror("Error", f"Tunnel '{tunnel_name}' not found.")
            return
        
        config = self._saved_tunnels[tunnel_name]
        
        try:
            # Build SSH command
            r_flags = []
            for pair in config['ports'].split(','):
                pair = pair.strip()
                if ':' in pair:
                    remote, local = pair.split(':')
                    r_flags.extend(['-R', f'{remote.strip()}:127.0.0.1:{local.strip()}'])
            
            # Build SSH command based on authentication method
            auth_method = config.get('auth_method', 'key')
            
            if auth_method == 'password':
                # For password authentication, we need to use sshpass or expect
                # Check if sshpass is available, otherwise use expect-like approach
                cmd = ['ssh',
                       '-vvv',
                       '-o', 'ExitOnForwardFailure=yes', 
                       '-o', 'StrictHostKeyChecking=no', 
                       '-o', 'UserKnownHostsFile=NUL',
                       '-o', 'ConnectTimeout=10',
                       '-o', 'ServerAliveInterval=60',
                       '-o', 'ServerAliveCountMax=3',
                       '-o', 'PreferredAuthentications=password',
                       '-o', 'PubkeyAuthentication=no'] + r_flags + ['-N', f"{config['user']}@{config['host']}"]
            else:
                # SSH key authentication (default)
                cmd = ['ssh',
                       '-vvv',
                       '-o', 'ExitOnForwardFailure=yes', 
                       '-o', 'StrictHostKeyChecking=no', 
                       '-o', 'UserKnownHostsFile=NUL',
                       '-o', 'PreferredAuthentications=publickey',
                       '-o', 'ConnectTimeout=10',
                       '-o', 'ServerAliveInterval=60',
                       '-o', 'ServerAliveCountMax=3'] + r_flags + ['-N', f"{config['user']}@{config['host']}"]
            
            # Start the process
            creation_flags = 0
            if os.name == 'nt':
                creation_flags = subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.DETACHED_PROCESS
            
            if auth_method == 'password':
                # For password authentication, we need to handle the password prompt
                password = config.get('password', '')
                if not password:
                    raise ValueError("Password is required for password authentication")
                
                # Try using sshpass first (most reliable for password auth)
                try:
                    # Check if sshpass is available
                    subprocess.run(['sshpass', '-V'], capture_output=True, check=True)
                    sshpass_cmd = ['sshpass', '-p', password] + cmd
                    process = self._popen_with_logging(sshpass_cmd, creation_flags)
                except (FileNotFoundError, subprocess.CalledProcessError):
                    # sshpass not available, create a simple expect-like solution
                    # For Windows, we'll use a different approach
                    if os.name == 'nt':
                        # On Windows, we'll use a batch script approach
                        import tempfile
                        with tempfile.NamedTemporaryFile(mode='w', suffix='.bat', delete=False) as f:
                            f.write(f'@echo off\n')
                            f.write(f'echo {password} | {" ".join(cmd)}\n')
                            batch_file = f.name
                        
                        process = self._popen_with_logging(['cmd', '/c', batch_file], creation_flags)
                        
                        # Clean up batch file after a delay
                        def cleanup_batch():
                            time.sleep(10)
                            try:
                                os.unlink(batch_file)
                            except:
                                pass
                        threading.Thread(target=cleanup_batch, daemon=True).start()
                    else:
                        # On Unix-like systems, use expect if available
                        try:
                            import pexpect
                            cmd_str = ' '.join(cmd)
                            child = pexpect.spawn(cmd_str)
                            child.expect('password:')
                            child.sendline(password)
                            # Convert pexpect child to subprocess-like object
                            process = type('Process', (), {
                                'pid': child.pid,
                                'poll': lambda: child.isalive() and None or 0,
                                'terminate': child.terminate,
                                'kill': child.kill
                            })()
                        except ImportError:
                            # Fallback: warn user about limitations
                            messagebox.showwarning(
                                "Limited Support", 
                                "Password authentication requires 'sshpass' or 'pexpect'.\n"
                                "Please install sshpass for better password support, or use SSH keys."
                            )
                            # Try basic approach anyway
                            process = self._popen_with_logging(cmd, creation_flags)
            else:
                # SSH key authentication
                process = self._popen_with_logging(cmd, creation_flags)
            
            # Give it a moment to start
            time.sleep(2)
            
            if process.poll() is not None:
                raise ValueError("SSH failed to start: process exited immediately")
            
            # Track the tunnel
            self._active_tunnels[tunnel_name] = {
                'process': process,
                'config': config,
                'start_time': time.time()
            }
            
            # Save PID to config for persistence across restarts
            self.save_tunnel_pid(tunnel_name, process.pid)
            
            messagebox.showinfo("Success", f"Tunnel '{tunnel_name}' started successfully!\nPID: {process.pid}")
            logging.info(f"Started tunnel '{tunnel_name}' with PID: {process.pid}")
            
            # Refresh the display
            self.refresh_tunnels()
            
        except Exception as e:
            messagebox.showerror("Error", f"Failed to start tunnel '{tunnel_name}': {e}")
            logging.error(f"Failed to start tunnel '{tunnel_name}': {e}")

    def _popen_with_logging(self, cmd, creation_flags=0):
        """Start a subprocess with pipes and stream its output to the logger."""
        try:
            proc = subprocess.Popen(
                cmd,
                stdin=subprocess.DEVNULL,  # prevent ssh from trying to read prompts
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                creationflags=creation_flags
            )
        except Exception as e:
            logging.error(f"Failed to launch process: {e}")
            raise

        # Start reader threads
        threading.Thread(target=self._log_stream, args=(proc.stderr, logging.INFO, "[SSH] "), daemon=True).start()
        threading.Thread(target=self._log_stream, args=(proc.stdout, logging.DEBUG, "[SSH-OUT] "), daemon=True).start()
        return proc

    def _log_stream(self, stream, level, prefix=""):
        """Continuously read a stream and log each line."""
        try:
            for line in iter(stream.readline, ''):
                if line:
                    logging.log(level, f"{prefix}{line.rstrip()}")
        except Exception as e:
            logging.debug(f"Stream logger ended: {e}")
        finally:
            try:
                stream.close()
            except Exception:
                pass
    
    def quick_start_tunnel(self):
        """Create and start a tunnel from Connection tab settings"""
        try:
            # Get settings from Connection tab
            user = self.user_entry.get().strip()
            host = self.ip_entry.get().strip()
            ports = self.ports_entry.get().strip()
            
            if not all([user, host, ports]):
                messagebox.showerror("Missing Information", "Please fill in all connection details first.")
                return
            
            # Create tunnel configuration
            tunnel_name = "QuickStart"
            tunnel_config = {
                'name': tunnel_name,
                'user': user,
                'host': host,
                'ports': ports,
                'description': 'Quick start tunnel from Connection tab'
            }
            
            # Save the configuration
            self.save_tunnel_config(tunnel_config)
            
            # Start the tunnel
            self.start_saved_tunnel(tunnel_name)
            
            # Switch to Active Tunnels tab to show the result
            if hasattr(self, 'notebook'):
                for i in range(self.notebook.index('end')):
                    if 'Active Tunnels' in self.notebook.tab(i, 'text'):
                        self.notebook.select(i)
                        break
            
            logging.info(f"Quick start tunnel '{tunnel_name}' created and started")
            
        except Exception as e:
            messagebox.showerror("Error", f"Failed to create quick start tunnel: {e}")
            logging.error(f"Quick start tunnel error: {e}")
    
    def find_external_tunnels(self):
        """Find and display external SSH tunnel processes for management"""
        try:
            # Use process_utils to build the list
            external_processes = pu.list_external_tunnels()
            
            if not external_processes:
                messagebox.showinfo("No External Tunnels", "No external SSH tunnel processes found.")
                return
            
            # Show dialog with external processes
            self.show_external_tunnels_dialog(external_processes)
            
        except Exception as e:
            messagebox.showerror("Error", f"Failed to find external tunnels: {e}")
            logging.error(f"Error finding external tunnels: {e}")
    
    def show_external_tunnels_dialog(self, processes):
        """Show dialog with external SSH processes for management (delegates to tunnel_ui)."""
        return tunnel_ui.show_external_tunnels_dialog(self, processes)
    
    def stop_tunnel(self):
        """Stop tunnel from GUI"""
        try:
            self._stop_tunnel_process()
            if not self.headless:
                messagebox.showinfo("Success", "Tunnel stopped!")
        except Exception as e:
            if not self.headless:
                messagebox.showerror("Error", str(e))
    
    def _stop_tunnel_process(self):
        """Internal method to stop the SSH tunnel process"""
        stopped_any = False
        
        # First try to stop our tracked process
        if self.process and self.process.poll() is None:
            try:
                self.process.terminate()
                self.process.wait(timeout=5)
                stopped_any = True
                logging.info("Stopped tracked SSH process")
            except subprocess.TimeoutExpired:
                self.process.kill()
                stopped_any = True
                logging.info("Killed tracked SSH process")
            except Exception as e:
                logging.error(f"Error stopping tracked process: {e}")
        
        # Also look for any SSH processes with reverse tunnels
        user = self.config.get('VPS', 'user')
        ip = self.config.get('VPS', 'ip')
        user_ip = f"{user}@{ip}"
        
        for proc in psutil.process_iter(['pid', 'name', 'cmdline']):
            try:
                if proc.name().lower() in ['ssh.exe', 'ssh'] and proc.cmdline():
                    cmdline = ' '.join(proc.cmdline())
                    if '-R' in cmdline and user_ip in cmdline:
                        proc.terminate()
                        try:
                            proc.wait(timeout=5)
                        except psutil.TimeoutExpired:
                            proc.kill()
                        stopped_any = True
                        logging.info(f"Stopped SSH process PID: {proc.pid}")
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue
        
        self.process = None
        if not self.headless:
            self.status_label.config(text="Status: Stopped")
        
        if not stopped_any:
            logging.warning("No running tunnel found to stop")
    
    def is_tunnel_running(self):
        # First check our tracked process
        if self.process and self.process.poll() is None:
            return True
            
        # Use cached process scan for efficiency
        ssh_processes = self._get_cached_ssh_processes()
        user = self.config.get('VPS', 'user')
        ip = self.config.get('VPS', 'ip')
        user_ip = f"{user}@{ip}"
        
        for proc_info in ssh_processes.values():
            if user_ip in proc_info.get('cmdline', ''):
                return True
        return False
    
    def test_ssh_connection(self):
        """Test SSH connection and diagnose authentication issues"""
        try:
            user = self.user_entry.get()
            ip = self.ip_entry.get()
            
            if not user or not ip:
                raise ValueError("Username and IP are required!")
            
            test_cmd = ['ssh', '-v', '-o', 'BatchMode=yes', '-o', 'ConnectTimeout=5', 
                       '-o', 'StrictHostKeyChecking=no', f'{user}@{ip}', 'echo', 'SSH connection successful']
            
            result = subprocess.run(test_cmd, capture_output=True, text=True, timeout=10)
            
            if result.returncode == 0:
                messagebox.showinfo("SSH Test Success", "SSH key authentication is working!")
                logging.info("SSH connection test successful")
            else:
                error_msg = result.stderr
                if "Permission denied" in error_msg:
                    messagebox.showerror("SSH Test Failed", 
                        "SSH key authentication failed. Possible issues:\n\n"
                        "1. SSH key not properly set up on VPS\n"
                        "2. SSH agent not running on Windows\n"
                        "3. Wrong username or IP\n\n"
                        f"Error details:\n{error_msg[:300]}...")
                else:
                    messagebox.showerror("SSH Test Failed", f"Connection failed:\n{error_msg[:300]}...")
                logging.error(f"SSH connection test failed: {error_msg}")
                    
        except subprocess.TimeoutExpired:
            messagebox.showerror("SSH Test Failed", "Connection timed out. Check IP address and network connectivity.")
            logging.error("SSH connection test timed out")
        except Exception as e:
            messagebox.showerror("Error", str(e))
            logging.error(f"SSH connection test error: {e}")
    
    def check_status(self):
        if self.is_tunnel_running():
            if not self.headless:
                self.status_label.config(text="Status: Running")
                messagebox.showinfo("Status", "Tunnel is running.")
        else:
            if not self.headless:
                self.status_label.config(text="Status: Stopped")
                messagebox.showinfo("Status", "Tunnel is stopped.")
    
    def test_tunnel(self):
        try:
            if not self.is_tunnel_running():
                raise ValueError("Start the tunnel first!")
            
            user = self.user_entry.get()
            ip = self.ip_entry.get()
            ports = self.ports_entry.get().strip().split(',')[0]
            remote_port = ports.split(':')[0].strip() if ':' in ports else '11434'
            
            test_cmd = ['ssh', f'{user}@{ip}', 'curl', '-s', f'http://localhost:{remote_port}/']
            
            result = subprocess.run(test_cmd, capture_output=True, text=True, timeout=10)
            
            if result.returncode == 0 and result.stdout:
                messagebox.showinfo("Test Success", f"Tunnel works! Response snippet: {result.stdout[:100]}...")
                logging.info("Tunnel test successful")
            else:
                messagebox.showerror("Test Failed", f"Error: {result.stderr or 'No response'}")
                logging.error(f"Tunnel test failed: {result.stderr}")
        except Exception as e:
            messagebox.showerror("Error", str(e))
            logging.error(f"Tunnel test error: {e}")
    
    def view_logs(self):
        """Open log file in notepad"""
        try:
            subprocess.run(['notepad.exe', LOG_FILE], check=True)
        except Exception as e:
            messagebox.showerror("Error", f"Could not open log file: {e}")
    
    def update_log_display(self):
        """Update the log display in GUI (delegates to tunnel_ui)."""
        return tunnel_ui.update_log_display(self)
    
    # System tray methods
    def minimize_to_tray(self):
        """Minimize application to system tray"""
        if self.headless:
            return
        
        self.root.withdraw()
        if not self.icon:
            self.icon = self.create_tray_icon()
            threading.Thread(target=self.icon.run, daemon=True).start()
    
    def show_window(self, icon=None, item=None):
        """Show the main window"""
        if self.headless:
            return
        
        self.root.deiconify()
        self.root.lift()
        self.root.focus_force()
    
    def on_closing(self):
        """Handle window close event"""
        # If launched from a console, exiting should release the terminal
        if getattr(self, '_launched_from_console', False):
            self.quit_app()
            return
        # Otherwise, check minimize-to-tray preference
        if hasattr(self, 'minimize_to_tray_var') and self.minimize_to_tray_var.get():
            self.minimize_to_tray()
        else:
            self.quit_app()
    
    def start_tunnel_tray(self, icon=None, item=None):
        """Start tunnel from tray menu"""
        self.start_tunnel_background()
    
    def stop_tunnel_tray(self, icon=None, item=None):
        """Stop tunnel from tray menu"""
        self._stop_tunnel_process()
    
    def check_status_tray(self, icon=None, item=None):
        """Check status from tray menu"""
        status = "Running" if self.is_tunnel_running() else "Stopped"
        # You could show a notification here if desired
        logging.info(f"Tunnel status: {status}")
    
    def quit_app(self, icon=None, item=None):
        """Quit the application gracefully"""
        try:
            logging.info("Initiating graceful shutdown...")
            self.running = False

            # Stop system tray icon early to avoid blocking
            if hasattr(self, 'icon') and self.icon:
                try:
                    self.icon.stop()
                    logging.info("System tray icon stopped")
                except Exception as e:
                    logging.error(f"Error stopping system tray: {e}")
                finally:
                    self.icon = None

            # Stop any running tunnel process tracked by self.process
            if hasattr(self, 'process') and self.process:
                try:
                    proc = self.process
                    if proc.poll() is None:
                        proc.terminate()
                        try:
                            proc.wait(timeout=5)
                        except Exception:
                            proc.kill()
                    logging.info("Primary tunnel process stopped")
                except Exception as e:
                    logging.error(f"Error stopping primary tunnel process: {e}")
                finally:
                    self.process = None

            # Stop any active tunnels started by this app
            if hasattr(self, '_active_tunnels'):
                for tunnel_name, tunnel_info in list(self._active_tunnels.items()):
                    try:
                        process = tunnel_info.get('process')
                        if process and process.poll() is None:
                            process.terminate()
                            try:
                                process.wait(timeout=5)
                            except Exception:
                                process.kill()
                            logging.info(f"Terminated active tunnel: {tunnel_name}")
                    except Exception as e:
                        logging.error(f"Error stopping active tunnel {tunnel_name}: {e}")
                self._active_tunnels.clear()

            # Close file logging to release file handles
            try:
                close_file_logging()
            except Exception as e:
                logging.error(f"Error closing file logging: {e}")

            # Close GUI
            if not self.headless and hasattr(self, 'root'):
                try:
                    self.root.quit()
                    self.root.destroy()
                    logging.info("GUI closed")
                except Exception as e:
                    logging.error(f"Error closing GUI: {e}")

            logging.info("Application shutdown complete")
            
        except Exception as e:
            logging.error(f"Error during shutdown: {e}")
        finally:
            # Ensure process terminates so calling terminal is released
            try:
                sys.exit(0)
            except SystemExit:
                pass
            # Fallback hard-exit if something is still blocking (e.g., orphaned threads)
            os._exit(0)
    
    # Enhanced UI Methods
    def update_tunnel_list(self):
        """Update the tunnel list showing all saved tunnels with their status, grouped by connection"""
        if self.headless or not hasattr(self, 'tunnel_tree'):
            return
        
        try:
            logging.info("Updating tunnel list display")
            
            # Clear existing items
            for item in self.tunnel_tree.get_children():
                self.tunnel_tree.delete(item)
            
            # Get running SSH processes for status checking
            ssh_processes = self._get_cached_ssh_processes()
            tunnel_count = len(self._saved_tunnels)
            
            logging.info(f"Displaying {tunnel_count} saved tunnels")
            
            # Group tunnels by connection (user@host)
            connections = {}
            for tunnel_name, config in self._saved_tunnels.items():
                user_host = f"{config['user']}@{config['host']}"
                connections.setdefault(user_host, []).append((tunnel_name, config))

            # Insert groups and children
            for user_host, items in sorted(connections.items()):
                parent_id = self.tunnel_tree.insert('', 'end', text=user_host, open=True)
                for tunnel_name, config in items:
                    ports = config['ports']
                    description = config.get('description', '')

                    # Check if this tunnel is currently running
                    running_pid = None
                    running_duration = "-"
                    status = "⚫ Stopped"

                    # Check in active tunnels first (tunnels started by this app)
                    if tunnel_name in self._active_tunnels:
                        process = self._active_tunnels[tunnel_name]['process']
                        if process.poll() is None:  # Still running
                            running_pid = process.pid
                            start_time = self._active_tunnels[tunnel_name]['start_time']
                            running_duration = self.format_duration(time.time() - start_time)
                            status = "🟢 Active"
                        else:
                            # Process died, remove from active tunnels
                            del self._active_tunnels[tunnel_name]
                            logging.info(f"Removed dead process for tunnel: {tunnel_name}")

                    # Also check in SSH processes (in case started externally)
                    if not running_pid:
                        for proc_info in ssh_processes.values():
                            if (user_host in proc_info.get('user_host', '') and 
                                ports in proc_info.get('ports', '')):
                                running_pid = proc_info['pid']
                                running_duration = self.format_duration(time.time() - proc_info['create_time'])
                                status = "🟡 External"
                                break

                    # Insert child row under connection parent
                    self.tunnel_tree.insert(parent_id, 'end', values=(
                        tunnel_name,
                        ports,
                        description,
                        status,
                        running_pid if running_pid else "-",
                        running_duration
                    ))
            
            # Update status bar
            active_count = len([t for t in self._saved_tunnels.keys() if t in self._active_tunnels])
            if hasattr(self, 'tunnel_count_label'):
                self.tunnel_count_label.config(text=f"Tunnels: {tunnel_count} ({active_count} active)")
            
            logging.info(f"Tunnel list updated: {tunnel_count} total, {active_count} active")
                
        except Exception as e:
            logging.error(f"Error updating tunnel list: {e}")
            import traceback
            logging.error(f"Traceback: {traceback.format_exc()}")
    
    def extract_user_host(self, cmdline):
        """Extract user@host from SSH command line"""
        try:
            return pu.extract_user_host(cmdline)
        except:
            return "Unknown"
    
    def extract_port_mappings(self, cmdline):
        """Extract port mappings from SSH command line"""
        try:
            return pu.extract_port_mappings(cmdline)
        except:
            return "Unknown"
    
    def format_duration(self, seconds):
        """Format duration in human readable format"""
        try:
            return pu.format_duration(int(seconds))
        except Exception:
            return "Unknown"
    
    def refresh_tunnels(self):
        """Manually refresh the tunnel list"""
        # Force cache refresh
        self._get_cached_ssh_processes(force_refresh=True)
        self.update_tunnel_list()
        logging.info("Tunnel list refreshed")
    
    def stop_selected_tunnel(self):
        """Stop the selected tunnel from the list"""
        try:
            selection = self.tunnel_tree.selection()
            if not selection:
                messagebox.showwarning("No Selection", "Please select a tunnel to stop.")
                return
            
            item = selection[0]
            values = self.tunnel_tree.item(item, 'values')
            # If a parent (connection) node is selected, do nothing
            if not values:
                messagebox.showwarning("Invalid Selection", "Select a specific tunnel under a connection.")
                return
            tunnel_name = values[0]  # First column is tunnel name
            connection = self.get_connection_for_item(item)   # Connection from parent text
            pid_str = values[4]      # PID is in the 5th value column now
            
            if pid_str == "-":
                messagebox.showwarning("No Process", "This tunnel is not currently running.")
                return
            
            try:
                pid = int(pid_str)
            except ValueError:
                messagebox.showerror("Error", "Invalid process ID.")
                return
            
            # Execute stop without confirmation
            try:
                proc = psutil.Process(pid)
                proc.terminate()
                proc.wait(timeout=5)
                
                # Clear PID from config and active tunnels
                self.clear_tunnel_pid(tunnel_name)
                if tunnel_name in self._active_tunnels:
                    del self._active_tunnels[tunnel_name]
                
                messagebox.showinfo("Success", f"Tunnel '{tunnel_name}' stopped successfully.")
                logging.info(f"Stopped tunnel '{tunnel_name}' (PID: {pid})")
                self.refresh_tunnels()
                
            except psutil.TimeoutExpired:
                proc.kill()
                
                # Clear PID from config and active tunnels
                self.clear_tunnel_pid(tunnel_name)
                if tunnel_name in self._active_tunnels:
                    del self._active_tunnels[tunnel_name]
                
                messagebox.showinfo("Forced Stop", f"Tunnel '{tunnel_name}' was forcefully terminated.")
                logging.info(f"Force killed tunnel '{tunnel_name}' (PID: {pid})")
                self.refresh_tunnels()
            except Exception as e:
                messagebox.showerror("Error", f"Failed to stop tunnel: {e}")
                    
        except Exception as e:
            messagebox.showerror("Error", f"Failed to stop tunnel: {e}")
            logging.error(f"Error stopping selected tunnel: {e}")

    def get_connection_for_item(self, item_id):
        """Helper to derive connection text (user@host) for a tree item."""
        try:
            parent = self.tunnel_tree.parent(item_id)
            if parent:
                return self.tunnel_tree.item(parent, 'text')
            return self.tunnel_tree.item(item_id, 'text')
        except Exception:
            return ""

    def stop_all_tunnels(self):
        """Stop all running tunnels"""
        try:
            count = pu.stop_all_ssh_tunnels(timeout=3.0)
            # refresh cache
            self._get_cached_ssh_processes(force_refresh=True)
            if not self.headless:
                if count > 0:
                    messagebox.showinfo("Success", "All SSH tunnels have been stopped.")
                else:
                    messagebox.showinfo("No Tunnels", "No SSH tunnels were running.")
        except Exception:
            return ""
        except Exception as e:
            messagebox.showerror("Error", f"Failed to stop tunnels: {e}")
            logging.error(f"Error stopping all tunnels: {e}")
    
    def view_tunnel_details(self):
        """Show detailed information about the selected tunnel (delegates to tunnel_ui)."""
        return tunnel_ui.view_tunnel_details(self)
    
    def restart_selected_tunnel(self):
        """Restart the selected tunnel"""
        try:
            selection = self.tunnel_tree.selection()
            if not selection:
                messagebox.showwarning("No Selection", "Please select a tunnel to restart.")
                return
            
            item = selection[0]
            values = self.tunnel_tree.item(item, 'values')
            if not values:
                messagebox.showwarning("Invalid Selection", "Select a specific tunnel under a connection.")
                return
            tunnel_name = values[0]
            pid_str = values[4]
            user_host = self.get_connection_for_item(item)
            
            try:
                # If a process exists, stop it first
                if pid_str and pid_str != "-":
                    pid = int(pid_str)
                    proc = psutil.Process(pid)
                    proc.terminate()
                    proc.wait(timeout=5)
                # Start from saved configuration
                if tunnel_name in self._saved_tunnels:
                    self.start_saved_tunnel(tunnel_name)
                    messagebox.showinfo("Success", f"Tunnel {tunnel_name} ({user_host}) restarted successfully.")
                    logging.info(f"Restarted tunnel {tunnel_name} ({user_host})")
                    self.refresh_tunnels()
                else:
                    messagebox.showwarning("Not Managed", "This tunnel is not a saved configuration.")
                
            except psutil.NoSuchProcess:
                messagebox.showerror("Error", "The selected process no longer exists.")
                self.refresh_tunnels()
            except psutil.TimeoutExpired:
                try:
                    proc.kill()
                except Exception:
                    pass
                messagebox.showwarning("Warning", "Had to force-kill the tunnel. Please start manually.")
                
        except Exception as e:
            messagebox.showerror("Error", f"Failed to restart tunnel: {e}")
            logging.error(f"Error restarting selected tunnel: {e}")
    
    def show_tunnel_context_menu(self, event):
        """Show context menu for tunnel list (delegates to tunnel_ui)."""
        return tunnel_ui.show_tunnel_context_menu(self, event)

    def get_connection_for_item(self, item_id):
        """Return the connection label (user@host) for a given tree item.
        If the item is a child, return its parent's text. If it's a parent, return its own text."""
        try:
            parent = self.tunnel_tree.parent(item_id)
            if parent:
                return self.tunnel_tree.item(parent, 'text') or ""
            # parentless -> it's a top-level connection node
            return self.tunnel_tree.item(item_id, 'text') or ""
        except Exception as e:
            logging.error(f"Error getting connection for item: {e}")
            return ""

    def restart_all_under_connection(self):
        """Restart all tunnels that belong to the selected connection (parent node).
        If a child tunnel is selected, applies to its parent connection."""
        try:
            selection = self.tunnel_tree.selection()
            if not selection:
                messagebox.showwarning("No Selection", "Please select a connection or a tunnel under it.")
                return
            item = selection[0]
            values = self.tunnel_tree.item(item, 'values')

            # Determine parent connection node and list of children
            parent = item if not values else self.tunnel_tree.parent(item)
            if not parent:
                # If values present but no parent, treat as invalid
                messagebox.showwarning("Invalid Selection", "Please select a connection group or a tunnel under it.")
                return
            connection = self.tunnel_tree.item(parent, 'text')
            children = self.tunnel_tree.get_children(parent)

            restarted = 0
            errors = []
            for child in children:
                cvals = self.tunnel_tree.item(child, 'values')
                if not cvals:
                    continue
                tunnel_name = cvals[0]
                pid_str = cvals[4] if len(cvals) > 4 else "-"
                # Stop existing process if any
                try:
                    if pid_str and pid_str != "-":
                        pid = int(pid_str)
                        proc = psutil.Process(pid)
                        proc.terminate()
                        try:
                            proc.wait(timeout=5)
                        except psutil.TimeoutExpired:
                            proc.kill()
                except psutil.NoSuchProcess:
                    pass
                except Exception as e:
                    errors.append(f"{tunnel_name}: stop error {e}")
                # Start from saved config
                try:
                    if tunnel_name in self._saved_tunnels:
                        self.start_saved_tunnel(tunnel_name)
                        restarted += 1
                    else:
                        errors.append(f"{tunnel_name}: not a saved configuration")
                except Exception as e:
                    errors.append(f"{tunnel_name}: start error {e}")

            # Refresh UI after batch
            self.refresh_tunnels()

            # Report outcome
            if errors:
                messagebox.showwarning(
                    "Restart Completed with Issues",
                    f"Connection: {connection}\nRestarted: {restarted}\nIssues: {len(errors)}\n\n" + "\n".join(errors[:10]) + ("\n..." if len(errors) > 10 else "")
                )
            else:
                messagebox.showinfo("Success", f"Restarted {restarted} tunnel(s) under {connection}.")
            logging.info(f"Restarted {restarted} tunnel(s) under {connection}; errors: {len(errors)}")

        except Exception as e:
            logging.error(f"Error restarting all tunnels under connection: {e}")
            messagebox.showerror("Error", f"Failed to restart all tunnels: {e}")
    
    def copy_tunnel_command(self):
        """Copy the SSH command of selected tunnel to clipboard (delegates to tunnel_ui)."""
        return tunnel_ui.copy_tunnel_command(self)
    
    def update_status_bar(self):
        """Update the bottom status bar (delegates to tunnel_ui)."""
        return tunnel_ui.update_status_bar(self)
    
    def open_config_file(self):
        """Open the configuration file in default editor"""
        try:
            if os.path.exists(CONFIG_FILE):
                os.startfile(CONFIG_FILE)
            else:
                messagebox.showwarning("File Not Found", "Configuration file does not exist yet.")
        except Exception as e:
            messagebox.showerror("Error", f"Could not open config file: {e}")
    
    def clear_logs(self):
        """Clear the log file and display"""
        try:
            # Temporarily close file logging to release file handle
            close_file_logging()
            
            try:
                # Clear log file
                with open(LOG_FILE, 'w') as f:
                    f.write("")
                
                # Clear log display
                if hasattr(self, 'log_text'):
                    self.log_text.delete(1.0, tk.END)
                
                # Reopen file logging
                setup_file_logging()
                
                logging.info("Log file cleared")
                messagebox.showinfo("Success", "Logs cleared successfully.")
                
            except Exception as e:
                # Make sure to reopen logging even if clearing failed
                setup_file_logging()
                raise e
                
        except Exception as e:
            messagebox.showerror("Error", f"Failed to clear logs: {e}")
    
    # Windows startup methods
    def enable_startup(self):
        """Add application to Windows startup"""
        try:
            key_path = r"SOFTWARE\Microsoft\Windows\CurrentVersion\Run"
            key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, key_path, 0, winreg.KEY_SET_VALUE)
            
            # Get the path to the current executable
            if getattr(sys, 'frozen', False):
                # Running as compiled exe
                app_path = f'"{sys.executable}" --headless'
            else:
                # Running as Python script
                app_path = f'"{sys.executable}" "{os.path.abspath(__file__)}" --headless'
            
            winreg.SetValueEx(key, "SSH Tunnel Manager", 0, winreg.REG_SZ, app_path)
            winreg.CloseKey(key)
            logging.info("Added to Windows startup")
        except Exception as e:
            logging.error(f"Failed to add to startup: {e}")
    
    def disable_startup(self):
        """Remove application from Windows startup"""
        try:
            key_path = r"SOFTWARE\Microsoft\Windows\CurrentVersion\Run"
            key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, key_path, 0, winreg.KEY_SET_VALUE)
            winreg.DeleteValue(key, "SSH Tunnel Manager")
            winreg.CloseKey(key)
            logging.info("Removed from Windows startup")
        except FileNotFoundError:
            pass  # Key doesn't exist, which is fine
        except Exception as e:
            logging.error(f"Failed to remove from startup: {e}")
    
    def is_startup_enabled(self):
        """Check if application is set to start with Windows"""
        try:
            key_path = r"SOFTWARE\Microsoft\Windows\CurrentVersion\Run"
            key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, key_path, 0, winreg.KEY_READ)
            value, _ = winreg.QueryValueEx(key, "SSH Tunnel Manager")
            winreg.CloseKey(key)
            return True
        except FileNotFoundError:
            return False
        except Exception:
            return False
    
    def load_config(self):
        """Load application configuration from config file"""
        try:
            if os.path.exists(CONFIG_FILE):
                self.config.read(CONFIG_FILE)
                logging.info(f"Configuration loaded from {CONFIG_FILE}")
            else:
                logging.info(f"Configuration file {CONFIG_FILE} not found - starting with empty config")
        except Exception as e:
            logging.error(f"Error loading configuration: {e}")
    
    def save_config(self):
        """Save application configuration to config file"""
        try:
            with open(CONFIG_FILE, 'w') as f:
                self.config.write(f)
            logging.info(f"Configuration saved to {CONFIG_FILE}")
        except Exception as e:
            logging.error(f"Error saving configuration: {e}")
    
    def load_saved_tunnels(self):
        """Load saved tunnel configurations from config file (delegates to config_store)."""
        try:
            self._saved_tunnels = cs_load_saved_tunnels(self.config, CONFIG_FILE)
        except Exception as e:
            logging.error(f"Error loading saved tunnels: {e}")
            self._saved_tunnels = {}
    
    def save_tunnel_config(self, tunnel_config):
        """Save a tunnel configuration to the config file (delegates to config_store)."""
        try:
            cs_save_tunnel_config(self.config, CONFIG_FILE, tunnel_config)
            # Keep in-memory cache in sync
            self._saved_tunnels[tunnel_config['name']] = tunnel_config.copy()
        except Exception as e:
            logging.error(f"Error saving tunnel configuration: {e}")
            raise
    
    def delete_tunnel_config(self, tunnel_name):
        """Delete a tunnel configuration (delegates to config_store)."""
        try:
            cs_delete_tunnel_config(self.config, CONFIG_FILE, tunnel_name)
            if tunnel_name in self._saved_tunnels:
                del self._saved_tunnels[tunnel_name]
        except Exception as e:
            logging.error(f"Error deleting tunnel configuration: {e}")
            raise
    
    def save_tunnel_pid(self, tunnel_name, pid):
        """Save the PID of a running tunnel for persistence (delegates to config_store)."""
        try:
            cs_save_tunnel_pid(self.config, CONFIG_FILE, tunnel_name, pid)
        except Exception as e:
            logging.error(f"Error saving tunnel PID: {e}")
    
    def clear_tunnel_pid(self, tunnel_name):
        """Clear the saved PID for a tunnel (delegates to config_store)."""
        try:
            cs_clear_tunnel_pid(self.config, CONFIG_FILE, tunnel_name)
        except Exception as e:
            logging.error(f"Error clearing tunnel PID: {e}")
    
    def _encrypt_password(self, password):
        """Deprecated wrapper to config_store.encrypt_password."""
        return cs_encrypt_password(password)
    
    def _decrypt_password(self, encrypted_password):
        """Deprecated wrapper to config_store.decrypt_password."""
        return cs_decrypt_password(encrypted_password)
    
    def quit_app_legacy(self):
        """Deprecated: retained to avoid method name override. Do not use."""
        try:
            self.quit_app()
        except Exception:
            pass
    
    def run(self):
        """Run the application"""
        if self.headless:
            # Run in headless mode
            logging.info("Starting in headless mode")
            self.icon = self.create_tray_icon()
            try:
                # Do not block the terminal; run the tray loop detached
                self.icon.run_detached()
            except AttributeError:
                # Fallback for older pystray: run in a daemon thread
                threading.Thread(target=self.icon.run, daemon=True).start()
                # Keep the process alive until interrupted
                try:
                    while self.running:
                        time.sleep(0.5)
                except KeyboardInterrupt:
                    pass
                finally:
                    self.quit_app()
        else:
            # Run with GUI
            self.root.mainloop()

def main():
    parser = argparse.ArgumentParser(description='SSH Reverse Tunnel Manager')
    parser.add_argument('--headless', action='store_true', help='Run in headless mode (system tray only)')
    parser.add_argument('--debug', action='store_true', help='Enable debug logging to console')
    args = parser.parse_args()
    
    # Create the application
    app = TunnelManager(headless=args.headless)
    
    # Set up signal handler for graceful shutdown on Ctrl+C
    def signal_handler(signum, frame):
        logging.info(f"Received signal {signum}, initiating graceful shutdown...")
        app.quit_app()
    
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)
    
    try:
        # Run the application
        app.run()
    except KeyboardInterrupt:
        logging.info("KeyboardInterrupt received, shutting down gracefully...")
        app.quit_app()
    except Exception as e:
        logging.error(f"Unexpected error in main: {e}")
        app.quit_app()

if __name__ == "__main__":
    main()
