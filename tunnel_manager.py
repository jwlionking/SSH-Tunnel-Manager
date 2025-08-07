import tkinter as tk
from tkinter import messagebox, ttk
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

# Config file to save settings - use absolute path to ensure persistence
# Detect if running as executable or script and use appropriate directory
if getattr(sys, 'frozen', False):
    # Running as executable (PyInstaller)
    APP_DIR = os.path.dirname(sys.executable)
else:
    # Running as script
    APP_DIR = os.path.dirname(os.path.abspath(__file__))

CONFIG_FILE = os.path.join(APP_DIR, 'tunnel_manager.ini')
LOG_FILE = os.path.join(APP_DIR, 'tunnel_manager.log')

# Setup logging - configure later to allow file operations
# Initial setup without file handler to prevent file locking
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler() if '--debug' in sys.argv else logging.NullHandler()
    ]
)

# Global variable to track file handler
_log_file_handler = None

def setup_file_logging():
    """Setup file logging handler - called after app initialization"""
    global _log_file_handler
    try:
        if _log_file_handler is None:
            _log_file_handler = logging.FileHandler(LOG_FILE)
            _log_file_handler.setFormatter(logging.Formatter('%(asctime)s - %(levelname)s - %(message)s'))
            logging.getLogger().addHandler(_log_file_handler)
            logging.info("File logging initialized")
    except Exception as e:
        print(f"Warning: Could not setup file logging: {e}")

def close_file_logging():
    """Close file logging handler to allow file operations"""
    global _log_file_handler
    try:
        if _log_file_handler:
            logging.getLogger().removeHandler(_log_file_handler)
            _log_file_handler.close()
            _log_file_handler = None
            logging.info("File logging closed")
    except Exception as e:
        print(f"Warning: Could not close file logging: {e}")

class TunnelManager:
    def __init__(self, headless=False):
        self.headless = headless
        self.process = None
        self.config = configparser.ConfigParser()
        
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
            self.root = tk.Tk()
            self.root.title("SSH Reverse Tunnel Manager")
            self.root.geometry("800x600")
            self.root.protocol("WM_DELETE_WINDOW", self.on_closing)
            self.create_widgets()
            
            # Populate tunnel list after GUI is created
            self.update_tunnel_list()
        
        # Auto-start tunnel if enabled
        if self.config.getboolean('Settings', 'auto_start', fallback=False):
            self.start_tunnel_background()
    
    def create_widgets(self):
        # Configure modern styling
        self.setup_modern_style()
        
        # Create notebook for tabbed interface
        self.notebook = ttk.Notebook(self.root)
        self.notebook.pack(fill=tk.BOTH, expand=True, padx=10, pady=5)
        
        # Create tabs
        self.create_tunnels_tab()
        self.create_settings_tab()
        self.create_logs_tab()
        
        # Status bar at bottom
        self.create_status_bar()
        
        # Start periodic updates with optimized intervals
        self.schedule_updates()
        
        # Bind window focus events for adaptive updates
        self.root.bind('<FocusIn>', self.on_window_focus)
        self.root.bind('<FocusOut>', self.on_window_unfocus)
        
        # Bind tab change events to refresh data
        self.notebook.bind('<<NotebookTabChanged>>', self.on_tab_changed)
    
    def setup_modern_style(self):
        """Configure modern, professional styling"""
        style = ttk.Style()
        
        # Configure colors and fonts
        self.colors = {
            'primary': '#2E86AB',      # Professional blue
            'secondary': '#A23B72',    # Accent purple
            'success': '#28A745',      # Success green
            'warning': '#FFC107',      # Warning yellow
            'danger': '#DC3545',       # Error red
            'dark': '#343A40',         # Dark text
            'light': '#F8F9FA',        # Light background
            'white': '#FFFFFF'
        }
        
        # Configure ttk styles
        style.configure('Title.TLabel', font=('Segoe UI', 14, 'bold'), foreground=self.colors['dark'])
        style.configure('Heading.TLabel', font=('Segoe UI', 11, 'bold'), foreground=self.colors['primary'])
        style.configure('Status.TLabel', font=('Segoe UI', 10, 'bold'))
        style.configure('Success.TLabel', foreground=self.colors['success'])
        style.configure('Warning.TLabel', foreground=self.colors['warning'])
        style.configure('Danger.TLabel', foreground=self.colors['danger'])
        
        # Button styles
        style.configure('Primary.TButton', font=('Segoe UI', 9, 'bold'))
        style.configure('Success.TButton', font=('Segoe UI', 9))
        style.configure('Danger.TButton', font=('Segoe UI', 9))
    
    def create_connection_tab(self):
        """Create the connection configuration tab"""
        conn_frame = ttk.Frame(self.notebook)
        self.notebook.add(conn_frame, text="🔗 Connection")
        
        # Main container with padding
        main_container = ttk.Frame(conn_frame)
        main_container.pack(fill=tk.BOTH, expand=True, padx=20, pady=20)
        
        # Title
        title_label = ttk.Label(main_container, text="SSH Connection Configuration", style='Title.TLabel')
        title_label.pack(anchor=tk.W, pady=(0, 20))
        
        # Connection details frame
        details_frame = ttk.LabelFrame(main_container, text="Server Details", padding=15)
        details_frame.pack(fill=tk.X, pady=(0, 20))
        
        # VPS Username
        user_frame = ttk.Frame(details_frame)
        user_frame.pack(fill=tk.X, pady=5)
        ttk.Label(user_frame, text="Username:", style='Heading.TLabel').pack(anchor=tk.W)
        self.user_entry = ttk.Entry(user_frame, font=('Segoe UI', 10), width=40)
        self.user_entry.pack(fill=tk.X, pady=(5, 0))
        self.user_entry.insert(0, self.config.get('VPS', 'user', fallback='root'))
        
        # VPS IP
        ip_frame = ttk.Frame(details_frame)
        ip_frame.pack(fill=tk.X, pady=5)
        ttk.Label(ip_frame, text="Server IP Address:", style='Heading.TLabel').pack(anchor=tk.W)
        self.ip_entry = ttk.Entry(ip_frame, font=('Segoe UI', 10), width=40)
        self.ip_entry.pack(fill=tk.X, pady=(5, 0))
        self.ip_entry.insert(0, self.config.get('VPS', 'ip', fallback='31.220.99.112'))
        
        # Ports
        ports_frame = ttk.Frame(details_frame)
        ports_frame.pack(fill=tk.X, pady=5)
        ttk.Label(ports_frame, text="Port Mappings (remote:local, comma-separated):", style='Heading.TLabel').pack(anchor=tk.W)
        self.ports_entry = ttk.Entry(ports_frame, font=('Segoe UI', 10), width=40)
        self.ports_entry.pack(fill=tk.X, pady=(5, 0))
        self.ports_entry.insert(0, self.config.get('Tunnel', 'ports', fallback='11434:11434'))
        
        # Action buttons
        action_frame = ttk.Frame(main_container)
        action_frame.pack(fill=tk.X, pady=10)
        
        button_container = ttk.Frame(action_frame)
        button_container.pack()
        
        # Primary action buttons
        ttk.Button(button_container, text="💾 Save Configuration", 
                  command=self.save_config, style='Primary.TButton', width=20).pack(side=tk.LEFT, padx=5)
        ttk.Button(button_container, text="🔍 Test Connection", 
                  command=self.test_ssh_connection, width=20).pack(side=tk.LEFT, padx=5)
        
        # Quick actions frame
        quick_frame = ttk.LabelFrame(main_container, text="Quick Start", padding=15)
        quick_frame.pack(fill=tk.X, pady=10)
        
        quick_buttons = ttk.Frame(quick_frame)
        quick_buttons.pack()
        
        ttk.Button(quick_buttons, text="🚀 Create & Start Tunnel", 
                  command=self.quick_start_tunnel, style='Success.TButton', width=20).pack(side=tk.LEFT, padx=5)
        ttk.Button(quick_buttons, text="⏹️ Stop All Tunnels", 
                  command=self.stop_all_tunnels, style='Danger.TButton', width=15).pack(side=tk.LEFT, padx=5)
        
        # Info text
        info_frame = ttk.Frame(quick_frame)
        info_frame.pack(fill=tk.X, pady=(10, 0))
        ttk.Label(info_frame, text="ℹ️ This creates a tunnel named 'QuickStart' and starts it immediately.", 
                 font=('Segoe UI', 9), foreground='#6c757d').pack(anchor=tk.W)
        
        # Status display
        status_frame = ttk.LabelFrame(main_container, text="Connection Status", padding=15)
        status_frame.pack(fill=tk.X, pady=10)
        
        self.status_label = ttk.Label(status_frame, text="● Idle", style='Status.TLabel', font=('Segoe UI', 12, 'bold'))
        self.status_label.pack(anchor=tk.W)
        
        self.status_detail = ttk.Label(status_frame, text="No active connections", font=('Segoe UI', 9))
        self.status_detail.pack(anchor=tk.W, pady=(5, 0))
    
    def create_tunnels_tab(self):
        """Create the main Tunnels tab showing all saved tunnels with status"""
        self.tunnels_frame = ttk.Frame(self.notebook)
        self.notebook.add(self.tunnels_frame, text="🔗 Tunnels")
        
        # Header with buttons
        header_frame = ttk.Frame(self.tunnels_frame)
        header_frame.pack(fill=tk.X, padx=20, pady=(20, 10))
        
        ttk.Label(header_frame, text="SSH Tunnel Manager", style='Title.TLabel').pack(side=tk.LEFT)
        
        button_frame = ttk.Frame(header_frame)
        button_frame.pack(side=tk.RIGHT)
        
        ttk.Button(button_frame, text="➕ Add Tunnel", command=self.add_tunnel_dialog, 
                  style='Success.TButton').pack(side=tk.LEFT, padx=2)
        ttk.Button(button_frame, text="🔍 Find External", command=self.find_external_tunnels, 
                  style='Primary.TButton').pack(side=tk.LEFT, padx=2)
        ttk.Button(button_frame, text="🔄 Refresh", command=self.refresh_tunnels).pack(side=tk.LEFT, padx=2)
        
        # Tunnels list frame
        list_frame = ttk.LabelFrame(self.tunnels_frame, text="Saved Tunnels", padding=10)
        list_frame.pack(fill=tk.BOTH, expand=True, padx=20, pady=(10, 20))
        
        # Create treeview for tunnel list
        columns = ('Name', 'Connection', 'Ports', 'Description', 'Status', 'PID', 'Duration')
        self.tunnel_tree = ttk.Treeview(list_frame, columns=columns, show='headings', height=12)
        
        # Configure columns
        self.tunnel_tree.heading('Name', text='Tunnel Name')
        self.tunnel_tree.heading('Connection', text='Connection')
        self.tunnel_tree.heading('Ports', text='Port Mappings')
        self.tunnel_tree.heading('Description', text='Description')
        self.tunnel_tree.heading('Status', text='Status')
        self.tunnel_tree.heading('PID', text='PID')
        self.tunnel_tree.heading('Duration', text='Uptime')
        
        self.tunnel_tree.column('Name', width=120)
        self.tunnel_tree.column('Connection', width=150)
        self.tunnel_tree.column('Ports', width=120)
        self.tunnel_tree.column('Description', width=150)
        self.tunnel_tree.column('Status', width=80, anchor=tk.CENTER)
        self.tunnel_tree.column('PID', width=60, anchor=tk.CENTER)
        self.tunnel_tree.column('Duration', width=80, anchor=tk.CENTER)
        
        # Scrollbar for treeview
        tree_scroll = ttk.Scrollbar(list_frame, orient=tk.VERTICAL, command=self.tunnel_tree.yview)
        self.tunnel_tree.configure(yscrollcommand=tree_scroll.set)
        
        self.tunnel_tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        tree_scroll.pack(side=tk.RIGHT, fill=tk.Y)
        
        # Context menu for tunnels
        self.create_tunnel_context_menu()
        
        # Action buttons for selected tunnel
        action_frame = ttk.Frame(self.tunnels_frame)
        action_frame.pack(fill=tk.X, padx=20, pady=10)
        
        # Left side - tunnel management
        left_buttons = ttk.Frame(action_frame)
        left_buttons.pack(side=tk.LEFT)
        
        ttk.Button(left_buttons, text="🚀 Start", 
                  command=self.start_selected_tunnel, style='Success.TButton').pack(side=tk.LEFT, padx=2)
        ttk.Button(left_buttons, text="🛑 Stop", 
                  command=self.stop_selected_tunnel, style='Danger.TButton').pack(side=tk.LEFT, padx=2)
        ttk.Button(left_buttons, text="🔄 Restart", 
                  command=self.restart_selected_tunnel).pack(side=tk.LEFT, padx=2)
        
        # Right side - configuration management
        right_buttons = ttk.Frame(action_frame)
        right_buttons.pack(side=tk.RIGHT)
        
        ttk.Button(right_buttons, text="✏️ Edit", 
                  command=self.edit_selected_tunnel).pack(side=tk.LEFT, padx=2)
        ttk.Button(right_buttons, text="🗑️ Delete", 
                  command=self.delete_selected_tunnel, style='Danger.TButton').pack(side=tk.LEFT, padx=2)
        ttk.Button(right_buttons, text="📊 Details", 
                  command=self.view_tunnel_details).pack(side=tk.LEFT, padx=2)
    
    def create_settings_tab(self):
        """Create the settings and preferences tab"""
        settings_frame = ttk.Frame(self.notebook)
        self.notebook.add(settings_frame, text="⚙️ Settings")
        
        main_container = ttk.Frame(settings_frame)
        main_container.pack(fill=tk.BOTH, expand=True, padx=20, pady=20)
        
        ttk.Label(main_container, text="Application Settings", style='Title.TLabel').pack(anchor=tk.W, pady=(0, 20))
        
        # Startup settings
        startup_frame = ttk.LabelFrame(main_container, text="Startup & Automation", padding=15)
        startup_frame.pack(fill=tk.X, pady=(0, 15))
        
        self.auto_start_var = tk.BooleanVar()
        self.auto_start_var.set(self.config.getboolean('Settings', 'auto_start', fallback=False))
        ttk.Checkbutton(startup_frame, text="🚀 Auto-start tunnel when application launches", 
                       variable=self.auto_start_var, style='TCheckbutton').pack(anchor=tk.W, pady=5)
        
        self.start_with_windows_var = tk.BooleanVar()
        self.start_with_windows_var.set(self.is_startup_enabled())
        ttk.Checkbutton(startup_frame, text="🪟 Start with Windows (run in system tray)", 
                       variable=self.start_with_windows_var).pack(anchor=tk.W, pady=5)
        
        # UI settings
        ui_frame = ttk.LabelFrame(main_container, text="User Interface", padding=15)
        ui_frame.pack(fill=tk.X, pady=(0, 15))
        
        self.minimize_to_tray_var = tk.BooleanVar()
        self.minimize_to_tray_var.set(self.config.getboolean('Settings', 'minimize_to_tray', fallback=True))
        ttk.Checkbutton(ui_frame, text="📱 Minimize to system tray instead of closing", 
                       variable=self.minimize_to_tray_var).pack(anchor=tk.W, pady=5)
        
        # Advanced settings
        advanced_frame = ttk.LabelFrame(main_container, text="Advanced Options", padding=15)
        advanced_frame.pack(fill=tk.X, pady=(0, 15))
        
        ttk.Button(advanced_frame, text="📁 Open Config File", 
                  command=self.open_config_file).pack(side=tk.LEFT, padx=5)
        ttk.Button(advanced_frame, text="📄 View Log File", 
                  command=self.view_logs).pack(side=tk.LEFT, padx=5)
        ttk.Button(advanced_frame, text="🗑️ Clear Logs", 
                  command=self.clear_logs).pack(side=tk.LEFT, padx=5)
        
        # System tray controls
        tray_frame = ttk.Frame(main_container)
        tray_frame.pack(fill=tk.X, pady=20)
        
        ttk.Button(tray_frame, text="📱 Minimize to Tray", 
                  command=self.minimize_to_tray, width=20).pack(side=tk.LEFT, padx=5)
    
    def create_logs_tab(self):
        """Create the logs and activity tab"""
        logs_frame = ttk.Frame(self.notebook)
        self.notebook.add(logs_frame, text="📋 Activity Log")
        
        main_container = ttk.Frame(logs_frame)
        main_container.pack(fill=tk.BOTH, expand=True, padx=20, pady=20)
        
        # Header
        header_frame = ttk.Frame(main_container)
        header_frame.pack(fill=tk.X, pady=(0, 15))
        
        ttk.Label(header_frame, text="Application Activity Log", style='Title.TLabel').pack(side=tk.LEFT)
        ttk.Button(header_frame, text="🔄 Refresh", command=self.update_log_display).pack(side=tk.RIGHT, padx=5)
        ttk.Button(header_frame, text="🗑️ Clear", command=self.clear_logs).pack(side=tk.RIGHT)
        
        # Log display
        log_frame = ttk.LabelFrame(main_container, text="Recent Activity", padding=10)
        log_frame.pack(fill=tk.BOTH, expand=True)
        
        # Create text widget with scrollbar
        text_frame = ttk.Frame(log_frame)
        text_frame.pack(fill=tk.BOTH, expand=True)
        
        self.log_text = tk.Text(text_frame, wrap=tk.WORD, font=('Consolas', 9), 
                               bg='#f8f9fa', fg='#343a40', relief=tk.FLAT, padx=10, pady=10)
        log_scrollbar = ttk.Scrollbar(text_frame, orient=tk.VERTICAL, command=self.log_text.yview)
        self.log_text.configure(yscrollcommand=log_scrollbar.set)
        
        self.log_text.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        log_scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        
        # Configure text tags for colored output
        self.log_text.tag_configure('INFO', foreground='#28a745')
        self.log_text.tag_configure('WARNING', foreground='#ffc107')
        self.log_text.tag_configure('ERROR', foreground='#dc3545')
        self.log_text.tag_configure('DEBUG', foreground='#6c757d')
    
    def create_status_bar(self):
        """Create bottom status bar"""
        status_bar = ttk.Frame(self.root, relief=tk.SUNKEN)
        status_bar.pack(side=tk.BOTTOM, fill=tk.X, padx=5, pady=2)
        
        # Connection indicator
        self.connection_indicator = ttk.Label(status_bar, text="●", foreground='red', font=('Segoe UI', 12))
        self.connection_indicator.pack(side=tk.LEFT, padx=5)
        
        # Status text
        self.status_bar_text = ttk.Label(status_bar, text="Ready", font=('Segoe UI', 9))
        self.status_bar_text.pack(side=tk.LEFT, padx=5)
        
        # Right side info
        self.tunnel_count_label = ttk.Label(status_bar, text="Tunnels: 0", font=('Segoe UI', 9))
        self.tunnel_count_label.pack(side=tk.RIGHT, padx=5)
        
        # Update status periodically
        self.update_status_bar()
    
    def create_tunnel_context_menu(self):
        """Create right-click context menu for tunnel list"""
        self.tunnel_context_menu = tk.Menu(self.root, tearoff=0)
        self.tunnel_context_menu.add_command(label="🛑 Stop Tunnel", command=self.stop_selected_tunnel)
        self.tunnel_context_menu.add_command(label="🔄 Restart Tunnel", command=self.restart_selected_tunnel)
        self.tunnel_context_menu.add_separator()
        self.tunnel_context_menu.add_command(label="📊 View Details", command=self.view_tunnel_details)
        self.tunnel_context_menu.add_command(label="📋 Copy Command", command=self.copy_tunnel_command)
        
        # Bind right-click to treeview
        self.tunnel_tree.bind("<Button-3>", self.show_tunnel_context_menu)
    
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
            self.config['VPS'] = {'user': 'root', 'ip': '31.220.99.112'}
            self.config['Tunnel'] = {'ports': '11434:11434'}
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
            
            # Get all running SSH processes
            ssh_processes = {}
            for proc in psutil.process_iter(['pid', 'name', 'cmdline', 'create_time']):
                try:
                    if proc.name().lower() in ['ssh.exe', 'ssh'] and proc.cmdline():
                        cmdline = ' '.join(proc.cmdline())
                        if '-R' in cmdline and '@' in cmdline:
                            ssh_processes[proc.pid] = {
                                'pid': proc.pid,
                                'cmdline': cmdline,
                                'create_time': proc.create_time(),
                                'user_host': self.extract_user_host(cmdline),
                                'ports': self.extract_port_mappings(cmdline)
                            }
                except (psutil.NoSuchProcess, psutil.AccessDenied, IndexError):
                    continue
            
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
                r_flags.extend(['-R', f'{remote.strip()}:localhost:{local.strip()}'])
        
        # SSH command with connection options
        base_cmd = ['ssh', 
                   '-o', 'StrictHostKeyChecking=no', 
                   '-o', 'UserKnownHostsFile=NUL',
                   '-o', 'BatchMode=yes',
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
        
        self.process = subprocess.Popen(
            cmd, 
            stdout=subprocess.PIPE, 
            stderr=subprocess.PIPE,
            creationflags=creation_flags
        )
        
        # Give it a moment to start and check if it failed immediately
        time.sleep(2)
        
        if self.process.poll() is not None:
            stdout, stderr = self.process.communicate()
            error_msg = stderr.decode('utf-8', errors='ignore') if stderr else "Unknown error"
            raise ValueError(f"SSH failed to start: {error_msg}")
        
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
        
        # Perform fresh scan
        self._tunnel_cache.clear()
        try:
            for proc in psutil.process_iter(['pid', 'name', 'cmdline', 'create_time']):
                try:
                    if proc.name().lower() in ['ssh.exe', 'ssh'] and proc.cmdline():
                        cmdline = ' '.join(proc.cmdline())
                        if '-R' in cmdline and '@' in cmdline:
                            self._tunnel_cache[proc.pid] = {
                                'pid': proc.pid,
                                'cmdline': cmdline,
                                'create_time': proc.create_time(),
                                'user_host': self.extract_user_host(cmdline),
                                'ports': self.extract_port_mappings(cmdline)
                            }
                except (psutil.NoSuchProcess, psutil.AccessDenied, IndexError):
                    continue
        except Exception as e:
            logging.error(f"Error scanning SSH processes: {e}")
        
        self._last_scan_time = current_time
        return self._tunnel_cache
    
    def schedule_updates(self):
        """Initialize UI with current data - no automatic updates"""
        if self.headless:
            return
        
        # Only update status bar on startup - no process scanning
        # User must manually refresh tunnel list and logs
        pass
    
    def on_window_focus(self, event=None):
        """Handle window gaining focus - NO automatic refresh to avoid performance issues"""
        self._window_visible = True
        # DO NOT auto-refresh on focus - this was causing performance issues
        # User can manually refresh using the refresh button
    
    def on_window_unfocus(self, event=None):
        """Handle window losing focus"""
        self._window_visible = False
    
    def on_tab_changed(self, event=None):
        """Handle tab change - NO automatic refresh to avoid performance issues"""
        # DO NOT auto-refresh on tab change - this was causing constant process scanning
        # User can manually refresh using the refresh button
        pass
    
    # Tunnel Management Methods
    def add_tunnel_dialog(self):
        """Open dialog to add a new tunnel configuration"""
        self.tunnel_config_dialog()
    
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
        
        if "Running" in status:
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
        
        if "Running" in status:
            messagebox.showwarning("Cannot Delete", "Stop the tunnel before deleting its configuration.")
            return
        
        if tunnel_name in self._saved_tunnels:
            if messagebox.askyesno("Confirm Delete", f"Delete tunnel configuration '{tunnel_name}'?"):
                try:
                    self.delete_tunnel_config(tunnel_name)
                    messagebox.showinfo("Success", f"Tunnel '{tunnel_name}' deleted successfully.")
                    self.refresh_tunnels()
                except Exception as e:
                    messagebox.showerror("Error", f"Failed to delete tunnel: {e}")
        else:
            messagebox.showwarning("Cannot Delete", "This is a running process, not a saved configuration.")
    
    def tunnel_config_dialog(self, existing_config=None):
        """Open dialog to add/edit tunnel configuration"""
        dialog = tk.Toplevel(self.root)
        dialog.title("Add Tunnel" if not existing_config else "Edit Tunnel")
        dialog.geometry("700x600")
        dialog.resizable(False, False)
        dialog.transient(self.root)
        dialog.grab_set()
        
        # Center the dialog
        dialog.update_idletasks()
        x = (dialog.winfo_screenwidth() // 2) - (dialog.winfo_width() // 2)
        y = (dialog.winfo_screenheight() // 2) - (dialog.winfo_height() // 2)
        dialog.geometry(f"+{x}+{y}")
        
        # Main frame
        main_frame = ttk.Frame(dialog, padding=20)
        main_frame.pack(fill=tk.BOTH, expand=True)
        
        # Title
        title_text = "Add New Tunnel" if not existing_config else "Edit Tunnel Configuration"
        ttk.Label(main_frame, text=title_text, style='Title.TLabel').pack(pady=(0, 20))
        
        # Form fields
        fields_frame = ttk.Frame(main_frame)
        fields_frame.pack(fill=tk.X, pady=(0, 20))
        
        # Tunnel Name
        ttk.Label(fields_frame, text="Tunnel Name:", style='Heading.TLabel').pack(anchor=tk.W)
        name_entry = ttk.Entry(fields_frame, font=('Segoe UI', 10), width=50)
        name_entry.pack(fill=tk.X, pady=(5, 10))
        
        # Username
        ttk.Label(fields_frame, text="Username:", style='Heading.TLabel').pack(anchor=tk.W)
        user_entry = ttk.Entry(fields_frame, font=('Segoe UI', 10), width=50)
        user_entry.pack(fill=tk.X, pady=(5, 10))
        
        # Host/IP
        ttk.Label(fields_frame, text="Host/IP Address:", style='Heading.TLabel').pack(anchor=tk.W)
        host_entry = ttk.Entry(fields_frame, font=('Segoe UI', 10), width=50)
        host_entry.pack(fill=tk.X, pady=(5, 10))
        
        # Ports
        ttk.Label(fields_frame, text="Port Mappings (remote:local, comma-separated):", style='Heading.TLabel').pack(anchor=tk.W)
        ports_entry = ttk.Entry(fields_frame, font=('Segoe UI', 10), width=50)
        ports_entry.pack(fill=tk.X, pady=(5, 10))
        
        # Authentication method
        ttk.Label(fields_frame, text="Authentication Method:", style='Heading.TLabel').pack(anchor=tk.W)
        auth_frame = ttk.Frame(fields_frame)
        auth_frame.pack(fill=tk.X, pady=(5, 10))
        
        auth_var = tk.StringVar(value="key")
        ttk.Radiobutton(auth_frame, text="SSH Key (default)", variable=auth_var, value="key").pack(side=tk.LEFT, padx=(0, 20))
        ttk.Radiobutton(auth_frame, text="Username/Password", variable=auth_var, value="password").pack(side=tk.LEFT)
        
        # Password field (initially hidden)
        password_frame = ttk.Frame(fields_frame)
        password_frame.pack(fill=tk.X, pady=(5, 10))
        
        password_label = ttk.Label(password_frame, text="Password:", style='Heading.TLabel')
        password_entry = ttk.Entry(password_frame, font=('Segoe UI', 10), width=50, show="*")
        
        def toggle_password_field():
            if auth_var.get() == "password":
                password_label.pack(anchor=tk.W)
                password_entry.pack(fill=tk.X, pady=(5, 0))
            else:
                password_label.pack_forget()
                password_entry.pack_forget()
        
        # Bind radio button changes
        auth_frame.winfo_children()[0].configure(command=toggle_password_field)
        auth_frame.winfo_children()[1].configure(command=toggle_password_field)
        
        # Description
        ttk.Label(fields_frame, text="Description (optional):", style='Heading.TLabel').pack(anchor=tk.W)
        desc_entry = ttk.Entry(fields_frame, font=('Segoe UI', 10), width=50)
        desc_entry.pack(fill=tk.X, pady=(5, 10))
        
        # Fill existing data if editing
        if existing_config:
            name_entry.insert(0, existing_config['name'])
            user_entry.insert(0, existing_config['user'])
            host_entry.insert(0, existing_config['host'])
            ports_entry.insert(0, existing_config['ports'])
            desc_entry.insert(0, existing_config.get('description', ''))
            
            # Set authentication method
            auth_method = existing_config.get('auth_method', 'key')
            auth_var.set(auth_method)
            
            # Set password if using password auth
            if auth_method == 'password' and 'password' in existing_config:
                password_entry.insert(0, existing_config['password'])
            
            # Show/hide password field based on auth method
            toggle_password_field()
            
            name_entry.config(state='readonly')  # Don't allow name changes when editing
        
        # Buttons
        button_frame = ttk.Frame(main_frame)
        button_frame.pack(fill=tk.X, pady=10)
        
        def save_tunnel():
            name = name_entry.get().strip()
            user = user_entry.get().strip()
            host = host_entry.get().strip()
            ports = ports_entry.get().strip()
            description = desc_entry.get().strip()
            auth_method = auth_var.get()
            password = password_entry.get().strip() if auth_method == "password" else ""
            
            # Validate required fields
            required_fields = [name, user, host, ports]
            if auth_method == "password":
                required_fields.append(password)
            
            if not all(required_fields):
                missing = "Please fill in all required fields."
                if auth_method == "password" and not password:
                    missing += " Password is required when using password authentication."
                messagebox.showerror("Validation Error", missing)
                return
            
            # Validate name uniqueness (only for new tunnels)
            if not existing_config and name in self._saved_tunnels:
                messagebox.showerror("Name Exists", "A tunnel with this name already exists.")
                return
            
            # Validate port format
            try:
                for pair in ports.split(','):
                    pair = pair.strip()
                    if ':' not in pair:
                        raise ValueError("Invalid port format")
                    remote, local = pair.split(':')
                    int(remote.strip())
                    int(local.strip())
            except ValueError:
                messagebox.showerror("Invalid Ports", "Port mappings must be in format 'remote:local' (e.g., '8080:80,9000:9000')")
                return
            
            tunnel_config = {
                'name': name,
                'user': user,
                'host': host,
                'ports': ports,
                'description': description,
                'auth_method': auth_method,
                'password': password if auth_method == 'password' else ''
            }
            
            try:
                self.save_tunnel_config(tunnel_config)
                action = "updated" if existing_config else "added"
                messagebox.showinfo("Success", f"Tunnel '{name}' {action} successfully!")
                dialog.destroy()
                self.refresh_tunnels()
            except Exception as e:
                messagebox.showerror("Error", f"Failed to save tunnel: {e}")
        
        def test_connection():
            user = user_entry.get().strip()
            host = host_entry.get().strip()
            auth_method = auth_var.get()
            password = password_entry.get().strip() if auth_method == "password" else ""
            
            if not user or not host:
                messagebox.showerror("Missing Info", "Please enter username and host first.")
                return
            
            if auth_method == "password" and not password:
                messagebox.showerror("Missing Password", "Please enter password for password authentication.")
                return
            
            try:
                if auth_method == "password":
                    # Test password authentication
                    try:
                        # Try sshpass first
                        subprocess.run(['sshpass', '-V'], capture_output=True, check=True)
                        test_cmd = ['sshpass', '-p', password, 'ssh', '-o', 'ConnectTimeout=5', 
                                   '-o', 'StrictHostKeyChecking=no', '-o', 'PreferredAuthentications=password',
                                   '-o', 'PubkeyAuthentication=no', f'{user}@{host}', 'echo', 'Connection test successful']
                    except (FileNotFoundError, subprocess.CalledProcessError):
                        messagebox.showwarning("Limited Testing", 
                                             "Password connection testing requires 'sshpass'.\n"
                                             "The tunnel may still work, but testing is limited.")
                        return
                else:
                    # Test SSH key authentication
                    test_cmd = ['ssh', '-o', 'BatchMode=yes', '-o', 'ConnectTimeout=5', 
                               '-o', 'StrictHostKeyChecking=no', f'{user}@{host}', 'echo', 'Connection test successful']
                
                result = subprocess.run(test_cmd, capture_output=True, text=True, timeout=15)
                
                if result.returncode == 0:
                    messagebox.showinfo("Test Success", "SSH connection successful!")
                else:
                    error_msg = result.stderr[:300] if result.stderr else "Unknown error"
                    messagebox.showerror("Test Failed", f"Connection failed:\n{error_msg}...")
            except Exception as e:
                messagebox.showerror("Test Error", f"Connection test failed: {e}")
        
        # Button layout - fix ordering
        ttk.Button(button_frame, text="Test Connection", command=test_connection).pack(side=tk.LEFT, padx=5)
        
        # Right side buttons (pack in reverse order since they're on the right)
        ttk.Button(button_frame, text="Cancel", command=dialog.destroy).pack(side=tk.RIGHT, padx=5)
        ttk.Button(button_frame, text="Save", command=save_tunnel, style='Primary.TButton').pack(side=tk.RIGHT, padx=5)
    
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
                    r_flags.extend(['-R', f'{remote.strip()}:localhost:{local.strip()}'])
            
            # Build SSH command based on authentication method
            auth_method = config.get('auth_method', 'key')
            
            if auth_method == 'password':
                # For password authentication, we need to use sshpass or expect
                # Check if sshpass is available, otherwise use expect-like approach
                cmd = ['ssh', 
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
                       '-o', 'StrictHostKeyChecking=no', 
                       '-o', 'UserKnownHostsFile=NUL',
                       '-o', 'BatchMode=yes',
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
                    process = subprocess.Popen(
                        sshpass_cmd,
                        stdout=subprocess.PIPE,
                        stderr=subprocess.PIPE,
                        creationflags=creation_flags
                    )
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
                        
                        process = subprocess.Popen(
                            ['cmd', '/c', batch_file],
                            stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE,
                            creationflags=creation_flags
                        )
                        
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
                            process = subprocess.Popen(
                                cmd,
                                stdin=subprocess.PIPE,
                                stdout=subprocess.PIPE,
                                stderr=subprocess.PIPE,
                                creationflags=creation_flags
                            )
            else:
                # SSH key authentication
                process = subprocess.Popen(
                    cmd, 
                    stdout=subprocess.PIPE, 
                    stderr=subprocess.PIPE,
                    creationflags=creation_flags
                )
            
            # Give it a moment to start
            time.sleep(2)
            
            if process.poll() is not None:
                stdout, stderr = process.communicate()
                error_msg = stderr.decode('utf-8', errors='ignore') if stderr else "Unknown error"
                raise ValueError(f"SSH failed to start: {error_msg}")
            
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
            # Force a fresh scan to find all SSH processes
            external_processes = []
            
            for proc in psutil.process_iter(['pid', 'name', 'cmdline', 'create_time']):
                try:
                    if proc.name().lower() in ['ssh.exe', 'ssh'] and proc.cmdline():
                        cmdline = ' '.join(proc.cmdline())
                        if '-R' in cmdline and '@' in cmdline:
                            # This is an SSH reverse tunnel
                            user_host = self.extract_user_host(cmdline)
                            ports = self.extract_port_mappings(cmdline)
                            duration = self.format_duration(time.time() - proc.create_time())
                            
                            external_processes.append({
                                'pid': proc.pid,
                                'user_host': user_host,
                                'ports': ports,
                                'duration': duration,
                                'cmdline': cmdline
                            })
                except (psutil.NoSuchProcess, psutil.AccessDenied, IndexError):
                    continue
            
            if not external_processes:
                messagebox.showinfo("No External Tunnels", "No external SSH tunnel processes found.")
                return
            
            # Show dialog with external processes
            self.show_external_tunnels_dialog(external_processes)
            
        except Exception as e:
            messagebox.showerror("Error", f"Failed to find external tunnels: {e}")
            logging.error(f"Error finding external tunnels: {e}")
    
    def show_external_tunnels_dialog(self, processes):
        """Show dialog with external SSH processes for management"""
        dialog = tk.Toplevel(self.root)
        dialog.title("External SSH Tunnels Found")
        dialog.geometry("700x400")
        dialog.resizable(True, True)
        dialog.transient(self.root)
        dialog.grab_set()
        
        # Center the dialog
        dialog.update_idletasks()
        x = (dialog.winfo_screenwidth() // 2) - (dialog.winfo_width() // 2)
        y = (dialog.winfo_screenheight() // 2) - (dialog.winfo_height() // 2)
        dialog.geometry(f"+{x}+{y}")
        
        # Main frame
        main_frame = ttk.Frame(dialog, padding=20)
        main_frame.pack(fill=tk.BOTH, expand=True)
        
        # Title
        ttk.Label(main_frame, text="External SSH Tunnel Processes", style='Title.TLabel').pack(pady=(0, 20))
        
        # Info
        info_text = "These SSH tunnel processes are running but not managed by this app.\nYou can stop them or manage them here."
        ttk.Label(main_frame, text=info_text, font=('Segoe UI', 9)).pack(pady=(0, 15))
        
        # Process list
        list_frame = ttk.Frame(main_frame)
        list_frame.pack(fill=tk.BOTH, expand=True, pady=(0, 15))
        
        columns = ('PID', 'Connection', 'Ports', 'Uptime')
        tree = ttk.Treeview(list_frame, columns=columns, show='headings', height=8)
        
        # Configure columns
        tree.heading('PID', text='Process ID')
        tree.heading('Connection', text='Connection')
        tree.heading('Ports', text='Port Mappings')
        tree.heading('Uptime', text='Uptime')
        
        tree.column('PID', width=80, anchor=tk.CENTER)
        tree.column('Connection', width=200)
        tree.column('Ports', width=150)
        tree.column('Uptime', width=100, anchor=tk.CENTER)
        
        # Add processes to tree
        for proc in processes:
            tree.insert('', 'end', values=(
                proc['pid'], proc['user_host'], proc['ports'], proc['duration']
            ))
        
        # Scrollbar
        scrollbar = ttk.Scrollbar(list_frame, orient=tk.VERTICAL, command=tree.yview)
        tree.configure(yscrollcommand=scrollbar.set)
        
        tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        
        # Buttons
        button_frame = ttk.Frame(main_frame)
        button_frame.pack(fill=tk.X, pady=10)
        
        def stop_selected():
            selection = tree.selection()
            if not selection:
                messagebox.showwarning("No Selection", "Please select a process to stop.")
                return
            
            item = selection[0]
            values = tree.item(item, 'values')
            pid = int(values[0])
            connection = values[1]
            
            if messagebox.askyesno("Confirm Stop", f"Stop SSH process {connection} (PID: {pid})?"):
                try:
                    proc = psutil.Process(pid)
                    proc.terminate()
                    proc.wait(timeout=5)
                    messagebox.showinfo("Success", f"Process {pid} stopped successfully.")
                    tree.delete(item)
                    logging.info(f"Stopped external SSH process PID: {pid}")
                except psutil.TimeoutExpired:
                    proc.kill()
                    messagebox.showinfo("Success", f"Process {pid} force-killed.")
                    tree.delete(item)
                except psutil.NoSuchProcess:
                    messagebox.showwarning("Process Gone", "Process no longer exists.")
                    tree.delete(item)
                except Exception as e:
                    messagebox.showerror("Error", f"Failed to stop process: {e}")
        
        def stop_all():
            if messagebox.askyesno("Confirm Stop All", "Stop ALL external SSH tunnel processes?"):
                stopped_count = 0
                for proc in processes:
                    try:
                        p = psutil.Process(proc['pid'])
                        p.terminate()
                        p.wait(timeout=3)
                        stopped_count += 1
                    except:
                        try:
                            p.kill()
                            stopped_count += 1
                        except:
                            pass
                
                messagebox.showinfo("Success", f"Stopped {stopped_count} processes.")
                dialog.destroy()
                self.refresh_tunnels()
        
        # Button layout
        ttk.Button(button_frame, text="🛑 Stop Selected", command=stop_selected, 
                  style='Danger.TButton').pack(side=tk.LEFT, padx=5)
        ttk.Button(button_frame, text="⏹️ Stop All", command=stop_all, 
                  style='Danger.TButton').pack(side=tk.LEFT, padx=5)
        ttk.Button(button_frame, text="Close", command=dialog.destroy).pack(side=tk.RIGHT, padx=5)
    
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
        """Update the log display in GUI"""
        if self.headless or not hasattr(self, 'log_text'):
            return
        
        try:
            if os.path.exists(LOG_FILE):
                # Only read if log tab is visible or window is focused
                current_tab = self.notebook.tab(self.notebook.select(), "text")
                if "Activity Log" not in current_tab and not self._window_visible:
                    # Skip update if log tab not visible and window not focused
                    pass
                else:
                    with open(LOG_FILE, 'r') as f:
                        lines = f.readlines()
                        # Show last 50 lines (increased from 20 for better context)
                        recent_lines = lines[-50:] if len(lines) > 50 else lines
                        
                    self.log_text.delete(1.0, tk.END)
                    self.log_text.insert(tk.END, ''.join(recent_lines))
                    self.log_text.see(tk.END)
        except Exception as e:
            logging.error(f"Error updating log display: {e}")
        
        # No automatic scheduling - updates are manual only
    
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
        # Check if minimize to tray is enabled (if the variable exists)
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
            
            # Stop any running tunnel process
            if hasattr(self, 'process') and self.process:
                try:
                    self._stop_tunnel_process()
                    logging.info("Tunnel process stopped")
                except Exception as e:
                    logging.error(f"Error stopping tunnel process: {e}")
            
            # Stop any active tunnels started by this app
            if hasattr(self, '_active_tunnels'):
                for tunnel_name, tunnel_info in list(self._active_tunnels.items()):
                    try:
                        process = tunnel_info['process']
                        if process.poll() is None:  # Still running
                            process.terminate()
                            logging.info(f"Terminated active tunnel: {tunnel_name}")
                    except Exception as e:
                        logging.error(f"Error stopping active tunnel {tunnel_name}: {e}")
            
            # Stop system tray icon FIRST to prevent blocking
            if hasattr(self, 'icon') and self.icon:
                try:
                    # Set icon to None first to prevent any further calls
                    icon_ref = self.icon
                    self.icon = None
                    icon_ref.stop()
                    logging.info("System tray icon stopped")
                except Exception as e:
                    logging.error(f"Error stopping system tray: {e}")
            
            # Close GUI
            if not self.headless and hasattr(self, 'root'):
                try:
                    # Use after_idle to ensure clean shutdown
                    self.root.after_idle(lambda: (
                        self.root.quit(),
                        self.root.destroy()
                    ))
                    logging.info("GUI shutdown scheduled")
                except Exception as e:
                    logging.error(f"Error closing GUI: {e}")
                    # Force quit if normal shutdown fails
                    try:
                        self.root.quit()
                        self.root.destroy()
                    except:
                        pass
            
            logging.info("Application shutdown complete")
            
        except Exception as e:
            logging.error(f"Error during shutdown: {e}")
        finally:
            # Use os._exit for immediate termination if needed
            import os
            os._exit(0)
    
    # Enhanced UI Methods
    def update_tunnel_list(self):
        """Update the tunnel list showing all saved tunnels with their status"""
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
            
            # Display all saved tunnel configurations
            for tunnel_name, config in self._saved_tunnels.items():
                user_host = f"{config['user']}@{config['host']}"
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
                
                # Insert saved tunnel configuration with status
                self.tunnel_tree.insert('', 'end', values=(
                    tunnel_name, 
                    user_host, 
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
            parts = cmdline.split()
            for part in parts:
                if '@' in part and not part.startswith('-'):
                    return part
            return "Unknown"
        except:
            return "Unknown"
    
    def extract_port_mappings(self, cmdline):
        """Extract port mappings from SSH command line"""
        try:
            parts = cmdline.split()
            mappings = []
            for i, part in enumerate(parts):
                if part == '-R' and i + 1 < len(parts):
                    mapping = parts[i + 1]
                    if ':' in mapping:
                        mappings.append(mapping)
            return ', '.join(mappings) if mappings else "Unknown"
        except:
            return "Unknown"
    
    def format_duration(self, seconds):
        """Format duration in human readable format"""
        try:
            if seconds < 60:
                return f"{int(seconds)}s"
            elif seconds < 3600:
                return f"{int(seconds//60)}m {int(seconds%60)}s"
            else:
                hours = int(seconds // 3600)
                minutes = int((seconds % 3600) // 60)
                return f"{hours}h {minutes}m"
        except:
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
            tunnel_name = values[0]  # First column is tunnel name
            connection = values[1]   # Second column is connection
            pid_str = values[5]      # PID is in the 6th column
            
            if pid_str == "-":
                messagebox.showwarning("No Process", "This tunnel is not currently running.")
                return
            
            try:
                pid = int(pid_str)
            except ValueError:
                messagebox.showerror("Error", "Invalid process ID.")
                return
            
            # Confirm action
            if messagebox.askyesno("Confirm Stop", f"Stop tunnel '{tunnel_name}' ({connection}, PID: {pid})?"):
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
                    
                    messagebox.showinfo("Success", f"Tunnel '{tunnel_name}' force-killed.")
                    logging.info(f"Force-killed tunnel '{tunnel_name}' (PID: {pid})")
                    self.refresh_tunnels()
                    
                except psutil.NoSuchProcess:
                    # Process already gone, just clear the PID
                    self.clear_tunnel_pid(tunnel_name)
                    if tunnel_name in self._active_tunnels:
                        del self._active_tunnels[tunnel_name]
                    
                    messagebox.showwarning("Process Not Found", "The selected process no longer exists.")
                    self.refresh_tunnels()
                    
        except Exception as e:
            messagebox.showerror("Error", f"Failed to stop tunnel: {e}")
            logging.error(f"Error stopping selected tunnel: {e}")
    
    def stop_all_tunnels(self):
        """Stop all running tunnels"""
        try:
            # Use cached processes and force refresh after stopping
            ssh_processes = self._get_cached_ssh_processes(force_refresh=True)
            tunnel_count = len(ssh_processes)
            stopped_count = 0
            
            for proc_info in ssh_processes.values():
                try:
                    proc = psutil.Process(proc_info['pid'])
                    proc.terminate()
                    proc.wait(timeout=3)
                    stopped_count += 1
                except psutil.TimeoutExpired:
                    try:
                        proc.kill()
                        stopped_count += 1
                    except psutil.NoSuchProcess:
                        pass
                except psutil.NoSuchProcess:
                    pass
            
            if tunnel_count == 0:
                messagebox.showinfo("No Tunnels", "No active tunnels found.")
            else:
                messagebox.showinfo("Success", f"Stopped {stopped_count} of {tunnel_count} tunnels.")
                logging.info(f"Stopped {stopped_count} of {tunnel_count} tunnels")
            
            # Force cache refresh after stopping
            self._get_cached_ssh_processes(force_refresh=True)
            self.refresh_tunnels()
            
        except Exception as e:
            messagebox.showerror("Error", f"Failed to stop tunnels: {e}")
            logging.error(f"Error stopping all tunnels: {e}")
    
    def view_tunnel_details(self):
        """Show detailed information about the selected tunnel"""
        try:
            selection = self.tunnel_tree.selection()
            if not selection:
                messagebox.showwarning("No Selection", "Please select a tunnel to view details.")
                return
            
            item = selection[0]
            values = self.tunnel_tree.item(item, 'values')
            pid = int(values[0])
            
            try:
                proc = psutil.Process(pid)
                info = f"""Tunnel Details:

Process ID: {pid}
Connection: {values[1]}
Port Mappings: {values[2]}
Status: {values[3]}
Uptime: {values[4]}

Process Info:
Executable: {proc.exe()}
Command Line: {' '.join(proc.cmdline())}
CPU Usage: {proc.cpu_percent():.1f}%
Memory Usage: {proc.memory_info().rss / 1024 / 1024:.1f} MB
Start Time: {datetime.fromtimestamp(proc.create_time()).strftime('%Y-%m-%d %H:%M:%S')}
"""
                messagebox.showinfo("Tunnel Details", info)
                
            except psutil.NoSuchProcess:
                messagebox.showerror("Error", "The selected process no longer exists.")
                self.refresh_tunnels()
                
        except Exception as e:
            messagebox.showerror("Error", f"Failed to get tunnel details: {e}")
    
    def restart_selected_tunnel(self):
        """Restart the selected tunnel"""
        try:
            selection = self.tunnel_tree.selection()
            if not selection:
                messagebox.showwarning("No Selection", "Please select a tunnel to restart.")
                return
            
            item = selection[0]
            values = self.tunnel_tree.item(item, 'values')
            pid = int(values[0])
            user_host = values[1]
            
            # Get the command line to recreate the tunnel
            try:
                proc = psutil.Process(pid)
                cmdline = proc.cmdline()
                
                # Stop the current tunnel
                proc.terminate()
                proc.wait(timeout=5)
                
                # Start new tunnel with same parameters
                subprocess.Popen(cmdline, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
                
                messagebox.showinfo("Success", f"Tunnel {user_host} restarted successfully.")
                logging.info(f"Restarted tunnel {user_host} (was PID: {pid})")
                self.refresh_tunnels()
                
            except psutil.NoSuchProcess:
                messagebox.showerror("Error", "The selected process no longer exists.")
                self.refresh_tunnels()
            except psutil.TimeoutExpired:
                proc.kill()
                messagebox.showwarning("Warning", "Had to force-kill the tunnel. Please start manually.")
                
        except Exception as e:
            messagebox.showerror("Error", f"Failed to restart tunnel: {e}")
            logging.error(f"Error restarting selected tunnel: {e}")
    
    def show_tunnel_context_menu(self, event):
        """Show context menu for tunnel list"""
        try:
            # Select the item under cursor
            item = self.tunnel_tree.identify_row(event.y)
            if item:
                self.tunnel_tree.selection_set(item)
                self.tunnel_context_menu.post(event.x_root, event.y_root)
        except Exception as e:
            logging.error(f"Error showing context menu: {e}")
    
    def copy_tunnel_command(self):
        """Copy the SSH command of selected tunnel to clipboard"""
        try:
            selection = self.tunnel_tree.selection()
            if not selection:
                messagebox.showwarning("No Selection", "Please select a tunnel to copy command.")
                return
            
            item = selection[0]
            values = self.tunnel_tree.item(item, 'values')
            pid = int(values[0])
            
            try:
                proc = psutil.Process(pid)
                cmdline = ' '.join(proc.cmdline())
                
                # Copy to clipboard
                self.root.clipboard_clear()
                self.root.clipboard_append(cmdline)
                self.root.update()
                
                messagebox.showinfo("Success", "SSH command copied to clipboard.")
                
            except psutil.NoSuchProcess:
                messagebox.showerror("Error", "The selected process no longer exists.")
                self.refresh_tunnels()
                
        except Exception as e:
            messagebox.showerror("Error", f"Failed to copy command: {e}")
    
    def update_status_bar(self):
        """Update the bottom status bar"""
        if self.headless or not hasattr(self, 'status_bar_text'):
            return
        
        try:
            # Update connection indicator
            if self.is_tunnel_running():
                self.connection_indicator.config(foreground='green')
                self.status_bar_text.config(text="Connected")
                self.status_label.config(text="● Running", foreground=self.colors['success'])
                self.status_detail.config(text="SSH tunnel is active")
            else:
                self.connection_indicator.config(foreground='red')
                self.status_bar_text.config(text="Disconnected")
                self.status_label.config(text="● Idle", foreground=self.colors['warning'])
                self.status_detail.config(text="No active connections")
        
        except Exception as e:
            logging.error(f"Error updating status bar: {e}")
        
        # No automatic scheduling - updates are manual only
    
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
            if messagebox.askyesno("Confirm Clear", "Clear all log entries?"):
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
        """Load saved tunnel configurations from config file"""
        try:
            self._saved_tunnels = {}
            
            # Load from config file
            for section_name in self.config.sections():
                if section_name.startswith('Tunnel_'):
                    tunnel_name = section_name[7:]  # Remove 'Tunnel_' prefix
                    section = self.config[section_name]
                    
                    tunnel_config = {
                        'name': tunnel_name,
                        'user': section.get('user', ''),
                        'host': section.get('host', ''),
                        'ports': section.get('ports', ''),
                        'description': section.get('description', ''),
                        'auth_method': section.get('auth_method', 'key'),
                        'password': self._decrypt_password(section.get('password', '')) if section.get('password') else ''
                    }
                    
                    self._saved_tunnels[tunnel_name] = tunnel_config
            
            logging.info(f"Loaded {len(self._saved_tunnels)} saved tunnel configurations")
            
        except Exception as e:
            logging.error(f"Error loading saved tunnels: {e}")
            self._saved_tunnels = {}
    
    def save_tunnel_config(self, tunnel_config):
        """Save a tunnel configuration to the config file"""
        try:
            tunnel_name = tunnel_config['name']
            section_name = f'Tunnel_{tunnel_name}'
            
            # Remove existing section if it exists
            if section_name in self.config:
                self.config.remove_section(section_name)
            
            # Create new section
            self.config.add_section(section_name)
            section = self.config[section_name]
            
            # Save tunnel configuration
            section['name'] = tunnel_config['name']
            section['user'] = tunnel_config['user']
            section['host'] = tunnel_config['host']
            section['ports'] = tunnel_config['ports']
            section['description'] = tunnel_config.get('description', '')
            section['auth_method'] = tunnel_config.get('auth_method', 'key')
            
            # Encrypt and save password if using password authentication
            if tunnel_config.get('auth_method') == 'password' and tunnel_config.get('password'):
                section['password'] = self._encrypt_password(tunnel_config['password'])
            
            # Save to file
            self.save_config()
            
            # Update in-memory storage
            self._saved_tunnels[tunnel_name] = tunnel_config.copy()
            
            logging.info(f"Saved tunnel configuration: {tunnel_name}")
            
        except Exception as e:
            logging.error(f"Error saving tunnel configuration: {e}")
            raise
    
    def delete_tunnel_config(self, tunnel_name):
        """Delete a tunnel configuration"""
        try:
            section_name = f'Tunnel_{tunnel_name}'
            
            # Remove from config file
            if section_name in self.config:
                self.config.remove_section(section_name)
                self.save_config()
            
            # Remove from in-memory storage
            if tunnel_name in self._saved_tunnels:
                del self._saved_tunnels[tunnel_name]
            
            logging.info(f"Deleted tunnel configuration: {tunnel_name}")
            
        except Exception as e:
            logging.error(f"Error deleting tunnel configuration: {e}")
            raise
    
    def save_tunnel_pid(self, tunnel_name, pid):
        """Save the PID of a running tunnel for persistence"""
        try:
            section_name = f'Tunnel_{tunnel_name}'
            if section_name in self.config:
                self.config[section_name]['pid'] = str(pid)
                self.save_config()
        except Exception as e:
            logging.error(f"Error saving tunnel PID: {e}")
    
    def clear_tunnel_pid(self, tunnel_name):
        """Clear the saved PID for a tunnel"""
        try:
            section_name = f'Tunnel_{tunnel_name}'
            if section_name in self.config and 'pid' in self.config[section_name]:
                del self.config[section_name]['pid']
                self.save_config()
        except Exception as e:
            logging.error(f"Error clearing tunnel PID: {e}")
    
    def _encrypt_password(self, password):
        """Simple password encryption for storage (base64 encoding)"""
        try:
            import base64
            # Simple base64 encoding - not cryptographically secure but better than plaintext
            # In production, use proper encryption like Fernet
            encoded = base64.b64encode(password.encode('utf-8')).decode('utf-8')
            return f"enc:{encoded}"
        except Exception as e:
            logging.error(f"Error encrypting password: {e}")
            return password
    
    def _decrypt_password(self, encrypted_password):
        """Simple password decryption from storage"""
        try:
            if encrypted_password.startswith('enc:'):
                import base64
                encoded = encrypted_password[4:]  # Remove 'enc:' prefix
                decoded = base64.b64decode(encoded.encode('utf-8')).decode('utf-8')
                return decoded
            else:
                # Not encrypted, return as-is (for backward compatibility)
                return encrypted_password
        except Exception as e:
            logging.error(f"Error decrypting password: {e}")
            return encrypted_password
    
    def quit_app(self):
        """Properly shutdown the application and cleanup resources"""
        try:
            logging.info("Shutting down application...")
            
            # Close file logging to release file handles
            close_file_logging()
            
            # Stop any running processes
            if hasattr(self, 'process') and self.process:
                try:
                    self.process.terminate()
                except:
                    pass
            
            # Stop system tray icon
            if hasattr(self, 'icon') and self.icon:
                try:
                    self.icon.stop()
                except:
                    pass
            
            # Close GUI if running
            if not self.headless and hasattr(self, 'root'):
                try:
                    self.root.quit()
                    self.root.destroy()
                except:
                    pass
                    
        except Exception as e:
            print(f"Error during shutdown: {e}")
        finally:
            # Force exit
            sys.exit(0)
    
    def run(self):
        """Run the application"""
        if self.headless:
            # Run in headless mode
            logging.info("Starting in headless mode")
            self.icon = self.create_tray_icon()
            self.icon.run()
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
