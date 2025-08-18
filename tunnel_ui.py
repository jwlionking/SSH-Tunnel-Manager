import tkinter as tk
from tkinter import ttk, messagebox
import subprocess
import time
import psutil
import logging
from datetime import datetime
import os
from logger_utils import LOG_FILE

# UI builder functions operate on the manager instance to avoid large refactors

def create_widgets(m):
    m.root = tk.Tk()
    m.root.title("SSH Reverse Tunnel Manager")
    m.root.geometry("1000x700")
    m.root.protocol("WM_DELETE_WINDOW", m.on_closing)
    
    setup_modern_style(m)
    
    m.notebook = ttk.Notebook(m.root)
    m.notebook.pack(fill=tk.BOTH, expand=True, padx=10, pady=5)

    # Ensure connection tab exists so test/quick-start methods have fields
    create_connection_tab(m)
    create_tunnels_tab(m)
    create_settings_tab(m)
    create_logs_tab(m)

    create_status_bar(m)

    schedule_updates(m)

    m.root.bind('<FocusIn>', lambda e: on_window_focus(m, e))
    m.root.bind('<FocusOut>', lambda e: on_window_unfocus(m, e))
    m.notebook.bind('<<NotebookTabChanged>>', lambda e: on_tab_changed(m, e))

    # Populate tunnel list after GUI is created
    m.update_tunnel_list()


def setup_modern_style(m):
    style = ttk.Style()
    m.colors = {
        'primary': '#2E86AB',
        'secondary': '#A23B72',
        'success': '#28A745',
        'warning': '#FFC107',
        'danger': '#DC3545',
        'dark': '#343A40',
        'light': '#F8F9FA',
        'white': '#FFFFFF',
    }
    style.configure('Title.TLabel', font=('Segoe UI', 14, 'bold'), foreground=m.colors['dark'])
    style.configure('Heading.TLabel', font=('Segoe UI', 11, 'bold'), foreground=m.colors['primary'])
    style.configure('Status.TLabel', font=('Segoe UI', 10, 'bold'))
    style.configure('Success.TLabel', foreground=m.colors['success'])
    style.configure('Warning.TLabel', foreground=m.colors['warning'])
    style.configure('Danger.TLabel', foreground=m.colors['danger'])
    style.configure('Primary.TButton', font=('Segoe UI', 9, 'bold'))
    style.configure('Success.TButton', font=('Segoe UI', 9))
    style.configure('Danger.TButton', font=('Segoe UI', 9))


def create_connection_tab(m):
    conn_frame = ttk.Frame(m.notebook)
    m.notebook.add(conn_frame, text="🔗 Connection")

    main_container = ttk.Frame(conn_frame)
    main_container.pack(fill=tk.BOTH, expand=True, padx=20, pady=20)

    ttk.Label(main_container, text="SSH Connection Configuration", style='Title.TLabel').pack(anchor=tk.W, pady=(0, 20))

    details_frame = ttk.LabelFrame(main_container, text="Server Details", padding=15)
    details_frame.pack(fill=tk.X, pady=(0, 20))

    user_frame = ttk.Frame(details_frame)
    user_frame.pack(fill=tk.X, pady=5)
    ttk.Label(user_frame, text="Username:", style='Heading.TLabel').pack(anchor=tk.W)
    m.user_entry = ttk.Entry(user_frame, font=('Segoe UI', 10), width=40)
    m.user_entry.pack(fill=tk.X, pady=(5, 0))
    m.user_entry.insert(0, m.config.get('VPS', 'user', fallback=''))

    ip_frame = ttk.Frame(details_frame)
    ip_frame.pack(fill=tk.X, pady=5)
    ttk.Label(ip_frame, text="Server IP Address:", style='Heading.TLabel').pack(anchor=tk.W)
    m.ip_entry = ttk.Entry(ip_frame, font=('Segoe UI', 10), width=40)
    m.ip_entry.pack(fill=tk.X, pady=(5, 0))
    m.ip_entry.insert(0, m.config.get('VPS', 'ip', fallback=''))

    ports_frame = ttk.Frame(details_frame)
    ports_frame.pack(fill=tk.X, pady=5)
    ttk.Label(ports_frame, text="Port Mappings (remote:local, comma-separated):", style='Heading.TLabel').pack(anchor=tk.W)
    m.ports_entry = ttk.Entry(ports_frame, font=('Segoe UI', 10), width=40)
    m.ports_entry.pack(fill=tk.X, pady=(5, 0))
    m.ports_entry.insert(0, m.config.get('Tunnel', 'ports', fallback=''))

    action_frame = ttk.Frame(main_container)
    action_frame.pack(fill=tk.X, pady=10)

    button_container = ttk.Frame(action_frame)
    button_container.pack()

    ttk.Button(button_container, text="💾 Save Configuration",
               command=m.save_config, style='Primary.TButton', width=20).pack(side=tk.LEFT, padx=5)
    ttk.Button(button_container, text="🔍 Test Connection",
               command=m.test_ssh_connection, width=20).pack(side=tk.LEFT, padx=5)

    quick_frame = ttk.LabelFrame(main_container, text="Quick Start", padding=15)
    quick_frame.pack(fill=tk.X, pady=10)

    quick_buttons = ttk.Frame(quick_frame)
    quick_buttons.pack()

    ttk.Button(quick_buttons, text="🚀 Create & Start Tunnel",
               command=m.quick_start_tunnel, style='Success.TButton', width=20).pack(side=tk.LEFT, padx=5)
    ttk.Button(quick_buttons, text="⏹️ Stop All Tunnels",
               command=m.stop_all_tunnels, style='Danger.TButton', width=15).pack(side=tk.LEFT, padx=5)

    info_frame = ttk.Frame(quick_frame)
    info_frame.pack(fill=tk.X, pady=(10, 0))
    ttk.Label(info_frame, text="ℹ️ This creates a tunnel named 'QuickStart' and starts it immediately.",
              font=('Segoe UI', 9), foreground='#6c757d').pack(anchor=tk.W)

    status_frame = ttk.LabelFrame(main_container, text="Connection Status", padding=15)
    status_frame.pack(fill=tk.X, pady=10)

    m.status_label = ttk.Label(status_frame, text="● Idle", style='Status.TLabel', font=('Segoe UI', 12, 'bold'))
    m.status_label.pack(anchor=tk.W)

    m.status_detail = ttk.Label(status_frame, text="No active connections", font=('Segoe UI', 9))
    m.status_detail.pack(anchor=tk.W, pady=(5, 0))


def create_tunnels_tab(m):
    m.tunnels_frame = ttk.Frame(m.notebook)
    m.notebook.add(m.tunnels_frame, text="🔗 Tunnels")

    header_frame = ttk.Frame(m.tunnels_frame)
    header_frame.pack(fill=tk.X, padx=20, pady=(20, 10))

    ttk.Label(header_frame, text="SSH Tunnel Manager", style='Title.TLabel').pack(side=tk.LEFT)

    button_frame = ttk.Frame(header_frame)
    button_frame.pack(side=tk.RIGHT)

    ttk.Button(button_frame, text="➕ Add Tunnel", command=m.add_tunnel_dialog,
               style='Success.TButton').pack(side=tk.LEFT, padx=2)
    ttk.Button(button_frame, text="🔍 Find External", command=m.find_external_tunnels,
               style='Primary.TButton').pack(side=tk.LEFT, padx=2)
    ttk.Button(button_frame, text="🔄 Refresh", command=m.refresh_tunnels).pack(side=tk.LEFT, padx=2)

    list_frame = ttk.LabelFrame(m.tunnels_frame, text="Saved Tunnels", padding=10)
    list_frame.pack(fill=tk.BOTH, expand=True, padx=20, pady=(10, 20))

    columns = ('Name', 'Ports', 'Description', 'Status', 'PID', 'Duration')
    m.tunnel_tree = ttk.Treeview(list_frame, columns=columns, show='tree headings', height=12)

    m.tunnel_tree.heading('#0', text='Connection')
    m.tunnel_tree.heading('Name', text='Tunnel Name')
    m.tunnel_tree.heading('Ports', text='Port Mappings')
    m.tunnel_tree.heading('Description', text='Description')
    m.tunnel_tree.heading('Status', text='Status')
    m.tunnel_tree.heading('PID', text='PID')
    m.tunnel_tree.heading('Duration', text='Uptime')

    m.tunnel_tree.column('#0', width=180)
    m.tunnel_tree.column('Name', width=150)
    m.tunnel_tree.column('Ports', width=140)
    m.tunnel_tree.column('Description', width=180)
    m.tunnel_tree.column('Status', width=90, anchor=tk.CENTER)
    m.tunnel_tree.column('PID', width=70, anchor=tk.CENTER)
    m.tunnel_tree.column('Duration', width=90, anchor=tk.CENTER)

    tree_scroll = ttk.Scrollbar(list_frame, orient=tk.VERTICAL, command=m.tunnel_tree.yview)
    m.tunnel_tree.configure(yscrollcommand=tree_scroll.set)

    m.tunnel_tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
    tree_scroll.pack(side=tk.RIGHT, fill=tk.Y)

    create_tunnel_context_menu(m)

    action_frame = ttk.Frame(m.tunnels_frame)
    action_frame.pack(fill=tk.X, padx=20, pady=10)

    left_buttons = ttk.Frame(action_frame)
    left_buttons.pack(side=tk.LEFT)

    ttk.Button(left_buttons, text="🚀 Start",
               command=m.start_selected_tunnel, style='Success.TButton').pack(side=tk.LEFT, padx=2)
    ttk.Button(left_buttons, text="🛑 Stop",
               command=m.stop_selected_tunnel, style='Danger.TButton').pack(side=tk.LEFT, padx=2)
    ttk.Button(left_buttons, text="🔄 Restart",
               command=m.restart_selected_tunnel).pack(side=tk.LEFT, padx=2)
    ttk.Button(left_buttons, text="🔁 Restart All in Connection",
               command=m.restart_all_under_connection).pack(side=tk.LEFT, padx=6)

    right_buttons = ttk.Frame(action_frame)
    right_buttons.pack(side=tk.RIGHT)

    ttk.Button(right_buttons, text="✏️ Edit",
               command=m.edit_selected_tunnel).pack(side=tk.LEFT, padx=2)
    ttk.Button(right_buttons, text="🗑️ Delete",
               command=m.delete_selected_tunnel, style='Danger.TButton').pack(side=tk.LEFT, padx=2)
    ttk.Button(right_buttons, text="📊 Details",
               command=m.view_tunnel_details).pack(side=tk.LEFT, padx=2)


def create_settings_tab(m):
    settings_frame = ttk.Frame(m.notebook)
    m.notebook.add(settings_frame, text="⚙️ Settings")

    main_container = ttk.Frame(settings_frame)
    main_container.pack(fill=tk.BOTH, expand=True, padx=20, pady=20)

    ttk.Label(main_container, text="Application Settings", style='Title.TLabel').pack(anchor=tk.W, pady=(0, 20))

    startup_frame = ttk.LabelFrame(main_container, text="Startup & Automation", padding=15)
    startup_frame.pack(fill=tk.X, pady=(0, 15))

    m.auto_start_var = tk.BooleanVar()
    m.auto_start_var.set(m.config.getboolean('Settings', 'auto_start', fallback=False))
    ttk.Checkbutton(startup_frame, text="🚀 Auto-start tunnel when application launches",
                    variable=m.auto_start_var, style='TCheckbutton').pack(anchor=tk.W, pady=5)

    m.start_with_windows_var = tk.BooleanVar()
    m.start_with_windows_var.set(m.is_startup_enabled())
    ttk.Checkbutton(startup_frame, text="🪟 Start with Windows (run in system tray)",
                    variable=m.start_with_windows_var).pack(anchor=tk.W, pady=5)

    ui_frame = ttk.LabelFrame(main_container, text="User Interface", padding=15)
    ui_frame.pack(fill=tk.X, pady=(0, 15))

    m.minimize_to_tray_var = tk.BooleanVar()
    m.minimize_to_tray_var.set(m.config.getboolean('Settings', 'minimize_to_tray', fallback=True))
    ttk.Checkbutton(ui_frame, text="📱 Minimize to system tray instead of closing",
                    variable=m.minimize_to_tray_var).pack(anchor=tk.W, pady=5)

    advanced_frame = ttk.LabelFrame(main_container, text="Advanced Options", padding=15)
    advanced_frame.pack(fill=tk.X, pady=(0, 15))

    ttk.Button(advanced_frame, text="📁 Open Config File",
               command=m.open_config_file).pack(side=tk.LEFT, padx=5)
    ttk.Button(advanced_frame, text="📄 View Log File",
               command=m.view_logs).pack(side=tk.LEFT, padx=5)
    ttk.Button(advanced_frame, text="🗑️ Clear Logs",
               command=m.clear_logs).pack(side=tk.LEFT, padx=5)

    tray_frame = ttk.Frame(main_container)
    tray_frame.pack(fill=tk.X, pady=20)

    ttk.Button(tray_frame, text="📱 Minimize to Tray",
               command=m.minimize_to_tray, width=20).pack(side=tk.LEFT, padx=5)


def create_logs_tab(m):
    logs_frame = ttk.Frame(m.notebook)
    m.notebook.add(logs_frame, text="📋 Activity Log")

    main_container = ttk.Frame(logs_frame)
    main_container.pack(fill=tk.BOTH, expand=True, padx=20, pady=20)

    header_frame = ttk.Frame(main_container)
    header_frame.pack(fill=tk.X, pady=(0, 15))

    ttk.Label(header_frame, text="Application Activity Log", style='Title.TLabel').pack(side=tk.LEFT)
    ttk.Button(header_frame, text="🔄 Refresh", command=m.update_log_display).pack(side=tk.RIGHT, padx=5)
    ttk.Button(header_frame, text="🗑️ Clear", command=m.clear_logs).pack(side=tk.RIGHT)

    log_frame = ttk.LabelFrame(main_container, text="Recent Activity", padding=10)
    log_frame.pack(fill=tk.BOTH, expand=True)

    text_frame = ttk.Frame(log_frame)
    text_frame.pack(fill=tk.BOTH, expand=True)

    m.log_text = tk.Text(text_frame, wrap=tk.WORD, font=('Consolas', 9),
                         bg='#f8f9fa', fg='#343a40', relief=tk.FLAT, padx=10, pady=10)
    log_scrollbar = ttk.Scrollbar(text_frame, orient=tk.VERTICAL, command=m.log_text.yview)
    m.log_text.configure(yscrollcommand=log_scrollbar.set)

    m.log_text.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
    log_scrollbar.pack(side=tk.RIGHT, fill=tk.Y)

    m.log_text.tag_configure('INFO', foreground='#28a745')
    m.log_text.tag_configure('WARNING', foreground='#ffc107')
    m.log_text.tag_configure('ERROR', foreground='#dc3545')
    m.log_text.tag_configure('DEBUG', foreground='#6c757d')


def create_status_bar(m):
    status_bar = ttk.Frame(m.root, relief=tk.SUNKEN)
    status_bar.pack(side=tk.BOTTOM, fill=tk.X, padx=5, pady=2)

    m.connection_indicator = ttk.Label(status_bar, text="●", foreground='red', font=('Segoe UI', 12))
    m.connection_indicator.pack(side=tk.LEFT, padx=5)

    m.status_bar_text = ttk.Label(status_bar, text="Ready", font=('Segoe UI', 9))
    m.status_bar_text.pack(side=tk.LEFT, padx=5)

    m.tunnel_count_label = ttk.Label(status_bar, text="Tunnels: 0", font=('Segoe UI', 9))
    m.tunnel_count_label.pack(side=tk.RIGHT, padx=5)

    m.update_status_bar()


def create_tunnel_context_menu(m):
    m.tunnel_context_menu = tk.Menu(m.root, tearoff=0)
    m.tunnel_context_menu.add_command(label="🛑 Stop Tunnel", command=m.stop_selected_tunnel)
    m.tunnel_context_menu.add_command(label="🔄 Restart Tunnel", command=m.restart_selected_tunnel)
    m.tunnel_context_menu.add_command(label="🔁 Restart All in Connection", command=m.restart_all_under_connection)
    m.tunnel_context_menu.add_separator()
    m.tunnel_context_menu.add_command(label="📊 View Details", command=m.view_tunnel_details)
    m.tunnel_context_menu.add_command(label="📋 Copy Command", command=m.copy_tunnel_command)

    m.tunnel_tree.bind("<Button-3>", m.show_tunnel_context_menu)


def schedule_updates(m):
    if m.headless:
        return
    # No-op placeholder to match previous behavior
    pass


def on_window_focus(m, event=None):
    m._window_visible = True


def on_window_unfocus(m, event=None):
    m._window_visible = False


def on_tab_changed(m, event=None):
    # Intentionally no auto refresh
    pass


def tunnel_config_dialog(m, existing_config=None, prefill=None):
    """Open dialog to add/edit tunnel configuration (UI only)."""
    dialog = tk.Toplevel(m.root)
    dialog.title("Add Tunnel" if not existing_config else "Edit Tunnel")
    dialog.geometry("700x600")
    dialog.resizable(False, False)
    dialog.transient(m.root)
    dialog.grab_set()

    dialog.update_idletasks()
    x = (dialog.winfo_screenwidth() // 2) - (dialog.winfo_width() // 2)
    y = (dialog.winfo_screenheight() // 2) - (dialog.winfo_height() // 2)
    dialog.geometry(f"+{x}+{y}")

    main_frame = ttk.Frame(dialog, padding=20)
    main_frame.pack(fill=tk.BOTH, expand=True)

    title_text = "Add New Tunnel" if not existing_config else "Edit Tunnel Configuration"
    ttk.Label(main_frame, text=title_text, style='Title.TLabel').pack(pady=(0, 20))

    fields_frame = ttk.Frame(main_frame)
    fields_frame.pack(fill=tk.X, pady=(0, 20))

    ttk.Label(fields_frame, text="Tunnel Name:", style='Heading.TLabel').pack(anchor=tk.W)
    name_entry = ttk.Entry(fields_frame, font=('Segoe UI', 10), width=50)
    name_entry.pack(fill=tk.X, pady=(5, 10))

    ttk.Label(fields_frame, text="Username:", style='Heading.TLabel').pack(anchor=tk.W)
    user_entry = ttk.Entry(fields_frame, font=('Segoe UI', 10), width=50)
    user_entry.pack(fill=tk.X, pady=(5, 10))

    ttk.Label(fields_frame, text="Host/IP Address:", style='Heading.TLabel').pack(anchor=tk.W)
    host_entry = ttk.Entry(fields_frame, font=('Segoe UI', 10), width=50)
    host_entry.pack(fill=tk.X, pady=(5, 10))

    ttk.Label(fields_frame, text="Port Mappings (remote:local, comma-separated):", style='Heading.TLabel').pack(anchor=tk.W)
    ports_entry = ttk.Entry(fields_frame, font=('Segoe UI', 10), width=50)
    ports_entry.pack(fill=tk.X, pady=(5, 10))

    ttk.Label(fields_frame, text="Authentication Method:", style='Heading.TLabel').pack(anchor=tk.W)
    auth_frame = ttk.Frame(fields_frame)
    auth_frame.pack(fill=tk.X, pady=(5, 10))

    auth_var = tk.StringVar(value="key")
    ttk.Radiobutton(auth_frame, text="SSH Key (default)", variable=auth_var, value="key").pack(side=tk.LEFT, padx=(0, 20))
    ttk.Radiobutton(auth_frame, text="Username/Password", variable=auth_var, value="password").pack(side=tk.LEFT)

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

    auth_frame.winfo_children()[0].configure(command=toggle_password_field)
    auth_frame.winfo_children()[1].configure(command=toggle_password_field)

    ttk.Label(fields_frame, text="Description (optional):", style='Heading.TLabel').pack(anchor=tk.W)
    desc_entry = ttk.Entry(fields_frame, font=('Segoe UI', 10), width=50)
    desc_entry.pack(fill=tk.X, pady=(5, 10))

    if existing_config:
        name_entry.insert(0, existing_config['name'])
        user_entry.insert(0, existing_config['user'])
        host_entry.insert(0, existing_config['host'])
        ports_entry.insert(0, existing_config['ports'])
        desc_entry.insert(0, existing_config.get('description', ''))
        auth_method = existing_config.get('auth_method', 'key')
        auth_var.set(auth_method)
        if auth_method == 'password' and 'password' in existing_config:
            password_entry.insert(0, existing_config['password'])
        toggle_password_field()
        name_entry.config(state='readonly')
    else:
        if prefill:
            if prefill.get('user'):
                user_entry.insert(0, prefill['user'])
            if prefill.get('host'):
                host_entry.insert(0, prefill['host'])

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

        required = [name, user, host, ports]
        if auth_method == "password":
            required.append(password)
        if not all(required):
            missing = "Please fill in all required fields."
            if auth_method == "password" and not password:
                missing += " Password is required when using password authentication."
            messagebox.showerror("Validation Error", missing)
            return

        if not existing_config and name in m._saved_tunnels:
            messagebox.showerror("Name Exists", "A tunnel with this name already exists.")
            return

        try:
            for pair in ports.split(','):
                pair = pair.strip()
                if ':' not in pair:
                    raise ValueError("Invalid port format")
                remote, local = pair.split(':')
                int(remote.strip()); int(local.strip())
        except ValueError:
            messagebox.showerror("Invalid Ports", "Port mappings must be in format 'remote:local' (e.g., '8080:80,9000:9000')")
            return

        tunnel_cfg = {
            'name': name,
            'user': user,
            'host': host,
            'ports': ports,
            'description': description,
            'auth_method': auth_method,
            'password': password if auth_method == 'password' else ''
        }

        try:
            m.save_tunnel_config(tunnel_cfg)
            action = "updated" if existing_config else "added"
            messagebox.showinfo("Success", f"Tunnel '{name}' {action} successfully!")
            dialog.destroy()
            m.refresh_tunnels()
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
                try:
                    subprocess.run(['sshpass', '-V'], capture_output=True, check=True)
                    test_cmd = ['sshpass', '-p', password, 'ssh', '-o', 'ConnectTimeout=5',
                                '-o', 'StrictHostKeyChecking=no', '-o', 'PreferredAuthentications=password',
                                '-o', 'PubkeyAuthentication=no', f'{user}@{host}', 'echo', 'Connection test successful']
                except (FileNotFoundError, subprocess.CalledProcessError):
                    messagebox.showwarning("Limited Testing",
                                           "Password connection testing requires 'sshpass'.\nThe tunnel may still work, but testing is limited.")
                    return
            else:
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

    ttk.Button(button_frame, text="Test Connection", command=test_connection).pack(side=tk.LEFT, padx=5)
    ttk.Button(button_frame, text="Cancel", command=dialog.destroy).pack(side=tk.RIGHT, padx=5)
    ttk.Button(button_frame, text="Save", command=save_tunnel, style='Primary.TButton').pack(side=tk.RIGHT, padx=5)


def show_external_tunnels_dialog(m, processes):
    """Show dialog with external SSH processes for management (UI only)."""
    dialog = tk.Toplevel(m.root)
    dialog.title("External SSH Tunnels Found")
    dialog.geometry("700x500")
    dialog.resizable(True, True)
    dialog.transient(m.root)
    dialog.grab_set()

    dialog.update_idletasks()
    x = (dialog.winfo_screenwidth() // 2) - (dialog.winfo_width() // 2)
    y = (dialog.winfo_screenheight() // 2) - (dialog.winfo_height() // 2)
    dialog.geometry(f"+{x}+{y}")

    main_frame = ttk.Frame(dialog, padding=20)
    main_frame.pack(fill=tk.BOTH, expand=True)

    ttk.Label(main_frame, text="External SSH Tunnel Processes", style='Title.TLabel').pack(pady=(0, 20))
    info_text = "These SSH tunnel processes are running but not managed by this app.\nYou can stop them or manage them here."
    ttk.Label(main_frame, text=info_text, font=('Segoe UI', 9)).pack(pady=(0, 15))

    list_frame = ttk.Frame(main_frame)
    list_frame.pack(fill=tk.BOTH, expand=True, pady=(0, 15))

    columns = ('PID', 'Connection', 'Ports', 'Uptime')
    tree = ttk.Treeview(list_frame, columns=columns, show='headings', height=8)
    tree.heading('PID', text='Process ID')
    tree.heading('Connection', text='User@Host')
    tree.heading('Ports', text='Port Mappings')
    tree.heading('Uptime', text='Uptime')

    tree.column('PID', width=80, anchor=tk.CENTER)
    tree.column('Connection', width=220)
    tree.column('Ports', width=220)
    tree.column('Uptime', width=120, anchor=tk.CENTER)

    for p in processes:
        uptime = m.format_duration(int(time.time() - p.get('create_time', time.time())))
        tree.insert('', tk.END, values=(p['pid'], p.get('user_host',''), p.get('ports',''), uptime))

    scrollbar = ttk.Scrollbar(list_frame, orient=tk.VERTICAL, command=tree.yview)
    tree.configure(yscrollcommand=scrollbar.set)
    tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
    scrollbar.pack(side=tk.RIGHT, fill=tk.Y)

    button_frame = ttk.Frame(main_frame)
    button_frame.pack(fill=tk.X, pady=10)

    def stop_selected():
        item = tree.selection()
        if not item:
            messagebox.showwarning("No Selection", "Please select a process to stop.")
            return
        pid = int(tree.item(item[0], 'values')[0])
        try:
            proc = psutil.Process(pid)
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except psutil.TimeoutExpired:
                proc.kill()
            messagebox.showinfo("Success", f"Stopped process {pid}.")
            tree.delete(item[0])
        except psutil.NoSuchProcess:
            messagebox.showwarning("Not Found", f"Process {pid} no longer exists.")
            tree.delete(item[0])
        except Exception as e:
            messagebox.showerror("Error", f"Failed to stop process: {e}")

    def stop_all():
        stopped = 0
        for iid in tree.get_children():
            pid = int(tree.item(iid, 'values')[0])
            try:
                proc = psutil.Process(pid)
                proc.terminate()
                try:
                    proc.wait(timeout=5)
                except psutil.TimeoutExpired:
                    proc.kill()
                tree.delete(iid)
                stopped += 1
            except Exception:
                continue
        messagebox.showinfo("Done", f"Stopped {stopped} process(es).")
        try:
            m.refresh_tunnels()
        except Exception:
            pass

    ttk.Button(button_frame, text="🛑 Stop Selected", command=stop_selected, style='Danger.TButton').pack(side=tk.LEFT, padx=5)
    ttk.Button(button_frame, text="⏹️ Stop All", command=stop_all, style='Danger.TButton').pack(side=tk.LEFT, padx=5)
    ttk.Button(button_frame, text="Close", command=dialog.destroy).pack(side=tk.RIGHT, padx=5)


# ---- UI helpers moved from TunnelManager to keep UI concerns here ----

def update_log_display(m):
    """Update the log display in GUI (delegated from manager)."""
    if m.headless or not hasattr(m, 'log_text'):
        return
    try:
        if os.path.exists(LOG_FILE):
            current_tab = m.notebook.tab(m.notebook.select(), "text")
            if "Activity Log" not in current_tab and not getattr(m, '_window_visible', True):
                pass
            else:
                with open(LOG_FILE, 'r') as f:
                    lines = f.readlines()
                    recent_lines = lines[-50:] if len(lines) > 50 else lines
                m.log_text.delete(1.0, tk.END)
                m.log_text.insert(tk.END, ''.join(recent_lines))
                m.log_text.see(tk.END)
    except Exception as e:
        logging.error(f"Error updating log display: {e}")


def show_tunnel_context_menu(m, event):
    """Show context menu for tunnel list (delegated from manager)."""
    try:
        item = m.tunnel_tree.identify_row(event.y)
        if item:
            m.tunnel_tree.selection_set(item)
            m.tunnel_context_menu.post(event.x_root, event.y_root)
    except Exception as e:
        logging.error(f"Error showing context menu: {e}")


def view_tunnel_details(m):
    """Show detailed information about the selected tunnel (delegated)."""
    try:
        selection = m.tunnel_tree.selection()
        if not selection:
            messagebox.showwarning("No Selection", "Please select a tunnel to view details.")
            return
        item = selection[0]
        values = m.tunnel_tree.item(item, 'values')
        if not values:
            messagebox.showwarning("Invalid Selection", "Select a specific tunnel under a connection.")
            return
        tunnel_name = values[0]
        connection = m.get_connection_for_item(item)
        ports = values[1]
        status = values[3]
        duration = values[5]
        pid_str = values[4]
        if pid_str == "-":
            messagebox.showinfo("Tunnel Details", f"Tunnel Details:\n\nName: {tunnel_name}\nConnection: {connection}\nPort Mappings: {ports}\nStatus: {status}\nUptime: {duration}\n\nNo process is currently associated with this tunnel.")
            return
        pid = int(pid_str)
        try:
            proc = psutil.Process(pid)
            info = f"""Tunnel Details:

Name: {tunnel_name}
Process ID: {pid}
Connection: {connection}
Port Mappings: {ports}
Status: {status}
Uptime: {duration}

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
            m.refresh_tunnels()
    except Exception as e:
        messagebox.showerror("Error", f"Failed to get tunnel details: {e}")


def copy_tunnel_command(m):
    """Copy the SSH command of selected tunnel to clipboard (delegated)."""
    try:
        selection = m.tunnel_tree.selection()
        if not selection:
            messagebox.showwarning("No Selection", "Please select a tunnel to copy command.")
            return
        item = selection[0]
        values = m.tunnel_tree.item(item, 'values')
        if not values:
            messagebox.showwarning("Invalid Selection", "Select a specific tunnel under a connection.")
            return
        tunnel_name = values[0]
        pid_str = values[4]
        try:
            cmdline = None
            if pid_str and pid_str != "-":
                proc = psutil.Process(int(pid_str))
                cmdline = ' '.join(proc.cmdline())
            else:
                if tunnel_name not in m._saved_tunnels:
                    messagebox.showwarning("Not Managed", "This tunnel is not a saved configuration.")
                    return
                config = m._saved_tunnels[tunnel_name]
                r_flags = []
                for pair in config['ports'].split(','):
                    pair = pair.strip()
                    if ':' in pair:
                        remote, local = pair.split(':')
                        r_flags.extend(['-R', f'{remote.strip()}:localhost:{local.strip()}'])
                base = [
                    'ssh',
                    '-o', 'StrictHostKeyChecking=no',
                    '-o', 'UserKnownHostsFile=NUL',
                    '-o', 'BatchMode=yes',
                    '-o', 'ConnectTimeout=10',
                    '-o', 'ServerAliveInterval=60',
                    '-o', 'ServerAliveCountMax=3'
                ] + r_flags + ['-N', f"{config['user']}@{config['host']}"]
                cmdline = ' '.join(base)
            m.root.clipboard_clear()
            m.root.clipboard_append(cmdline)
            m.root.update()
            messagebox.showinfo("Success", "SSH command copied to clipboard.")
        except psutil.NoSuchProcess:
            messagebox.showerror("Error", "The selected process no longer exists.")
            m.refresh_tunnels()
    except Exception as e:
        messagebox.showerror("Error", f"Failed to copy command: {e}")


def update_status_bar(m):
    """Update the bottom status bar (delegated)."""
    if m.headless or not hasattr(m, 'status_bar_text'):
        return
    try:
        if m.is_tunnel_running():
            m.connection_indicator.config(foreground='green')
            m.status_bar_text.config(text="Connected")
            m.status_label.config(text="● Running", foreground=m.colors['success'])
            m.status_detail.config(text="SSH tunnel is active")
        else:
            m.connection_indicator.config(foreground='red')
            m.status_bar_text.config(text="Disconnected")
            m.status_label.config(text="● Idle", foreground=m.colors['warning'])
            m.status_detail.config(text="No active connections")
    except Exception as e:
        logging.error(f"Error updating status bar: {e}")
    # No automatic scheduling - updates are manual only
