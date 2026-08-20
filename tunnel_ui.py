import logging
import os
import subprocess
import time
from datetime import datetime

import psutil
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from logger_utils import LOG_FILE
from ssh_utils import TUNNEL_TYPE_LABELS, TUNNEL_TYPES, build_ssh_command, normalize_tunnel_type, validate_ports, wrap_password_command


def create_widgets(m):
    m.root = tk.Tk()
    m.root.title("SSH Tunnel Manager")
    m.root.geometry("1100x720")
    m.root.minsize(860, 560)
    m.root.protocol("WM_DELETE_WINDOW", m.on_closing)

    setup_modern_style(m)

    m.notebook = ttk.Notebook(m.root)
    m.notebook.pack(fill=tk.BOTH, expand=True, padx=10, pady=5)

    create_tunnels_tab(m)
    create_connection_tab(m)
    create_settings_tab(m)
    create_logs_tab(m)

    create_status_bar(m)
    schedule_updates(m)

    m.root.bind("<FocusIn>", lambda e: on_window_focus(m, e))
    m.root.bind("<FocusOut>", lambda e: on_window_unfocus(m, e))

    m.update_tunnel_list()
    m.update_log_display()


def setup_modern_style(m):
    style = ttk.Style()
    try:
        style.theme_use("vista" if "vista" in style.theme_names() else "clam")
    except tk.TclError:
        pass
    m.colors = {
        "primary": "#2E86AB",
        "secondary": "#A23B72",
        "success": "#28A745",
        "warning": "#C79100",
        "danger": "#DC3545",
        "dark": "#343A40",
        "light": "#F8F9FA",
        "white": "#FFFFFF",
    }
    style.configure("Title.TLabel", font=("Segoe UI", 14, "bold"), foreground=m.colors["dark"])
    style.configure("Heading.TLabel", font=("Segoe UI", 11, "bold"), foreground=m.colors["primary"])
    style.configure("Status.TLabel", font=("Segoe UI", 10, "bold"))
    style.configure("Success.TLabel", foreground=m.colors["success"])
    style.configure("Warning.TLabel", foreground=m.colors["warning"])
    style.configure("Danger.TLabel", foreground=m.colors["danger"])
    style.configure("Primary.TButton", font=("Segoe UI", 9, "bold"))
    style.configure("Success.TButton", font=("Segoe UI", 9))
    style.configure("Danger.TButton", font=("Segoe UI", 9))


def create_connection_tab(m):
    conn_frame = ttk.Frame(m.notebook)
    m.notebook.add(conn_frame, text="Connection")

    main_container = ttk.Frame(conn_frame)
    main_container.pack(fill=tk.BOTH, expand=True, padx=20, pady=20)

    ttk.Label(main_container, text="Quick Connection", style="Title.TLabel").pack(anchor=tk.W, pady=(0, 8))
    ttk.Label(
        main_container,
        text="Defaults for a one-off tunnel. Saved tunnels live on the Tunnels tab.",
        foreground="#6c757d",
    ).pack(anchor=tk.W, pady=(0, 16))

    details_frame = ttk.LabelFrame(main_container, text="Server Details", padding=15)
    details_frame.pack(fill=tk.X, pady=(0, 20))

    user_frame = ttk.Frame(details_frame)
    user_frame.pack(fill=tk.X, pady=5)
    ttk.Label(user_frame, text="Username", style="Heading.TLabel").pack(anchor=tk.W)
    m.user_entry = ttk.Entry(user_frame, font=("Segoe UI", 10), width=40)
    m.user_entry.pack(fill=tk.X, pady=(5, 0))
    m.user_entry.insert(0, m.config.get("VPS", "user", fallback=""))

    ip_frame = ttk.Frame(details_frame)
    ip_frame.pack(fill=tk.X, pady=5)
    ttk.Label(ip_frame, text="Server host or IP", style="Heading.TLabel").pack(anchor=tk.W)
    m.ip_entry = ttk.Entry(ip_frame, font=("Segoe UI", 10), width=40)
    m.ip_entry.pack(fill=tk.X, pady=(5, 0))
    m.ip_entry.insert(0, m.config.get("VPS", "ip", fallback=""))

    ports_frame = ttk.Frame(details_frame)
    ports_frame.pack(fill=tk.X, pady=5)
    ttk.Label(ports_frame, text="Port mappings (remote:local, comma-separated)", style="Heading.TLabel").pack(anchor=tk.W)
    m.ports_entry = ttk.Entry(ports_frame, font=("Segoe UI", 10), width=40)
    m.ports_entry.pack(fill=tk.X, pady=(5, 0))
    m.ports_entry.insert(0, m.config.get("Tunnel", "ports", fallback=""))
    ttk.Label(ports_frame, text="Example: 8080:8080, 3000:3000", foreground="#6c757d").pack(anchor=tk.W, pady=(4, 0))

    action_frame = ttk.Frame(main_container)
    action_frame.pack(fill=tk.X, pady=10)
    button_container = ttk.Frame(action_frame)
    button_container.pack()
    ttk.Button(button_container, text="Save Defaults", command=m.save_config, style="Primary.TButton", width=18).pack(
        side=tk.LEFT, padx=5
    )
    ttk.Button(button_container, text="Test Connection", command=m.test_ssh_connection, width=18).pack(side=tk.LEFT, padx=5)

    quick_frame = ttk.LabelFrame(main_container, text="Quick Start", padding=15)
    quick_frame.pack(fill=tk.X, pady=10)
    quick_buttons = ttk.Frame(quick_frame)
    quick_buttons.pack()
    ttk.Button(quick_buttons, text="Create & Start Tunnel", command=m.quick_start_tunnel, style="Success.TButton", width=22).pack(
        side=tk.LEFT, padx=5
    )
    ttk.Button(quick_buttons, text="Stop All Tunnels", command=m.stop_all_tunnels, style="Danger.TButton", width=18).pack(
        side=tk.LEFT, padx=5
    )
    ttk.Label(
        quick_frame,
        text="Creates or updates a saved tunnel named QuickStart, then starts it.",
        foreground="#6c757d",
    ).pack(anchor=tk.W, pady=(10, 0))

    status_frame = ttk.LabelFrame(main_container, text="Connection Status", padding=15)
    status_frame.pack(fill=tk.X, pady=10)
    m.status_label = ttk.Label(status_frame, text="Idle", style="Status.TLabel", font=("Segoe UI", 12, "bold"))
    m.status_label.pack(anchor=tk.W)
    m.status_detail = ttk.Label(status_frame, text="No active connections", font=("Segoe UI", 9))
    m.status_detail.pack(anchor=tk.W, pady=(5, 0))


def create_tunnels_tab(m):
    m.tunnels_frame = ttk.Frame(m.notebook)
    m.notebook.add(m.tunnels_frame, text="Tunnels")

    header_frame = ttk.Frame(m.tunnels_frame)
    header_frame.pack(fill=tk.X, padx=20, pady=(20, 10))
    ttk.Label(header_frame, text="Saved Tunnels", style="Title.TLabel").pack(side=tk.LEFT)

    button_frame = ttk.Frame(header_frame)
    button_frame.pack(side=tk.RIGHT)
    ttk.Button(button_frame, text="Add Tunnel", command=m.add_tunnel_dialog, style="Success.TButton").pack(side=tk.LEFT, padx=2)
    ttk.Button(button_frame, text="Find External", command=m.find_external_tunnels, style="Primary.TButton").pack(
        side=tk.LEFT, padx=2
    )
    ttk.Button(button_frame, text="Refresh", command=m.refresh_tunnels).pack(side=tk.LEFT, padx=2)

    list_frame = ttk.LabelFrame(m.tunnels_frame, text="Grouped by connection", padding=10)
    list_frame.pack(fill=tk.BOTH, expand=True, padx=20, pady=(10, 12))

    columns = ("Name", "Type", "Ports", "Description", "Status", "PID", "Duration")
    m.tunnel_tree = ttk.Treeview(list_frame, columns=columns, show="tree headings", height=12)
    m.tunnel_tree.heading("#0", text="Connection")
    m.tunnel_tree.heading("Name", text="Tunnel")
    m.tunnel_tree.heading("Type", text="Type")
    m.tunnel_tree.heading("Ports", text="Port mappings")
    m.tunnel_tree.heading("Description", text="Description")
    m.tunnel_tree.heading("Status", text="Status")
    m.tunnel_tree.heading("PID", text="PID")
    m.tunnel_tree.heading("Duration", text="Uptime")
    m.tunnel_tree.column("#0", width=180)
    m.tunnel_tree.column("Name", width=120)
    m.tunnel_tree.column("Type", width=80, anchor=tk.CENTER)
    m.tunnel_tree.column("Ports", width=140)
    m.tunnel_tree.column("Description", width=160)
    m.tunnel_tree.column("Status", width=90, anchor=tk.CENTER)
    m.tunnel_tree.column("PID", width=70, anchor=tk.CENTER)
    m.tunnel_tree.column("Duration", width=90, anchor=tk.CENTER)

    tree_scroll = ttk.Scrollbar(list_frame, orient=tk.VERTICAL, command=m.tunnel_tree.yview)
    m.tunnel_tree.configure(yscrollcommand=tree_scroll.set)
    m.tunnel_tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
    tree_scroll.pack(side=tk.RIGHT, fill=tk.Y)
    m.tunnel_tree.bind("<Double-1>", lambda e: m.edit_selected_tunnel())

    create_tunnel_context_menu(m)

    action_frame = ttk.Frame(m.tunnels_frame)
    action_frame.pack(fill=tk.X, padx=20, pady=(0, 16))
    left_buttons = ttk.Frame(action_frame)
    left_buttons.pack(side=tk.LEFT)
    ttk.Button(left_buttons, text="Start", command=m.start_selected_tunnel, style="Success.TButton").pack(side=tk.LEFT, padx=2)
    ttk.Button(left_buttons, text="Stop", command=m.stop_selected_tunnel, style="Danger.TButton").pack(side=tk.LEFT, padx=2)
    ttk.Button(left_buttons, text="Restart", command=m.restart_selected_tunnel).pack(side=tk.LEFT, padx=2)
    ttk.Button(left_buttons, text="Restart Connection", command=m.restart_all_under_connection).pack(side=tk.LEFT, padx=6)

    right_buttons = ttk.Frame(action_frame)
    right_buttons.pack(side=tk.RIGHT)
    ttk.Button(right_buttons, text="Edit", command=m.edit_selected_tunnel).pack(side=tk.LEFT, padx=2)
    ttk.Button(right_buttons, text="Delete", command=m.delete_selected_tunnel, style="Danger.TButton").pack(side=tk.LEFT, padx=2)
    ttk.Button(right_buttons, text="Details", command=m.view_tunnel_details).pack(side=tk.LEFT, padx=2)


def create_settings_tab(m):
    settings_frame = ttk.Frame(m.notebook)
    m.notebook.add(settings_frame, text="Settings")

    main_container = ttk.Frame(settings_frame)
    main_container.pack(fill=tk.BOTH, expand=True, padx=20, pady=20)
    ttk.Label(main_container, text="Application Settings", style="Title.TLabel").pack(anchor=tk.W, pady=(0, 20))

    startup_frame = ttk.LabelFrame(main_container, text="Startup & automation", padding=15)
    startup_frame.pack(fill=tk.X, pady=(0, 15))

    m.auto_start_var = tk.BooleanVar(value=m.config.getboolean("Settings", "auto_start", fallback=False))
    m.start_with_windows_var = tk.BooleanVar(value=m.is_startup_enabled())
    m.stop_on_exit_var = tk.BooleanVar(value=m.config.getboolean("Settings", "stop_tunnels_on_exit", fallback=True))

    ttk.Checkbutton(
        startup_frame,
        text="Auto-start saved tunnels when the app launches",
        variable=m.auto_start_var,
        command=m.save_settings_quiet,
    ).pack(anchor=tk.W, pady=5)
    ttk.Checkbutton(
        startup_frame,
        text="Start with Windows (system tray / headless)",
        variable=m.start_with_windows_var,
        command=m.save_settings_quiet,
    ).pack(anchor=tk.W, pady=5)
    ttk.Checkbutton(
        startup_frame,
        text="Stop tunnels started by this app when exiting",
        variable=m.stop_on_exit_var,
        command=m.save_settings_quiet,
    ).pack(anchor=tk.W, pady=5)

    ui_frame = ttk.LabelFrame(main_container, text="User interface", padding=15)
    ui_frame.pack(fill=tk.X, pady=(0, 15))
    m.minimize_to_tray_var = tk.BooleanVar(value=m.config.getboolean("Settings", "minimize_to_tray", fallback=True))
    ttk.Checkbutton(
        ui_frame,
        text="Minimize to the system tray instead of quitting",
        variable=m.minimize_to_tray_var,
        command=m.save_settings_quiet,
    ).pack(anchor=tk.W, pady=5)

    security_frame = ttk.LabelFrame(main_container, text="SSH security", padding=15)
    security_frame.pack(fill=tk.X, pady=(0, 15))
    ttk.Label(security_frame, text="Host key policy").pack(anchor=tk.W)
    m.host_key_policy_var = tk.StringVar(value=m.config.get("Settings", "host_key_policy", fallback="accept-new"))
    policy_row = ttk.Frame(security_frame)
    policy_row.pack(fill=tk.X, pady=(6, 0))
    for label, value in (
        ("Accept new keys (recommended)", "accept-new"),
        ("Strict (yes)", "yes"),
        ("Disable checking (insecure)", "no"),
    ):
        ttk.Radiobutton(
            policy_row,
            text=label,
            value=value,
            variable=m.host_key_policy_var,
            command=m.save_settings_quiet,
        ).pack(side=tk.LEFT, padx=(0, 16))

    advanced_frame = ttk.LabelFrame(main_container, text="Files", padding=15)
    advanced_frame.pack(fill=tk.X, pady=(0, 15))
    ttk.Button(advanced_frame, text="Open Config File", command=m.open_config_file).pack(side=tk.LEFT, padx=5)
    ttk.Button(advanced_frame, text="View Log File", command=m.view_logs).pack(side=tk.LEFT, padx=5)
    ttk.Button(advanced_frame, text="Clear Logs", command=m.clear_logs).pack(side=tk.LEFT, padx=5)

    ttk.Button(main_container, text="Minimize to Tray", command=m.minimize_to_tray, width=20).pack(anchor=tk.W, pady=8)


def create_logs_tab(m):
    logs_frame = ttk.Frame(m.notebook)
    m.notebook.add(logs_frame, text="Activity Log")

    main_container = ttk.Frame(logs_frame)
    main_container.pack(fill=tk.BOTH, expand=True, padx=20, pady=20)
    header_frame = ttk.Frame(main_container)
    header_frame.pack(fill=tk.X, pady=(0, 15))
    ttk.Label(header_frame, text="Application Activity Log", style="Title.TLabel").pack(side=tk.LEFT)
    ttk.Button(header_frame, text="Refresh", command=m.update_log_display).pack(side=tk.RIGHT, padx=5)
    ttk.Button(header_frame, text="Clear", command=m.clear_logs).pack(side=tk.RIGHT)

    log_frame = ttk.LabelFrame(main_container, text="Recent activity", padding=10)
    log_frame.pack(fill=tk.BOTH, expand=True)
    text_frame = ttk.Frame(log_frame)
    text_frame.pack(fill=tk.BOTH, expand=True)
    m.log_text = tk.Text(
        text_frame,
        wrap=tk.WORD,
        font=("Consolas", 9),
        bg="#f8f9fa",
        fg="#343a40",
        relief=tk.FLAT,
        padx=10,
        pady=10,
    )
    log_scrollbar = ttk.Scrollbar(text_frame, orient=tk.VERTICAL, command=m.log_text.yview)
    m.log_text.configure(yscrollcommand=log_scrollbar.set)
    m.log_text.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
    log_scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
    m.log_text.tag_configure("INFO", foreground="#28a745")
    m.log_text.tag_configure("WARNING", foreground="#c79100")
    m.log_text.tag_configure("ERROR", foreground="#dc3545")
    m.log_text.tag_configure("DEBUG", foreground="#6c757d")


def create_status_bar(m):
    status_bar = ttk.Frame(m.root, relief=tk.SUNKEN)
    status_bar.pack(side=tk.BOTTOM, fill=tk.X, padx=5, pady=2)
    m.connection_indicator = ttk.Label(status_bar, text="●", foreground="red", font=("Segoe UI", 12))
    m.connection_indicator.pack(side=tk.LEFT, padx=5)
    m.status_bar_text = ttk.Label(status_bar, text="Ready", font=("Segoe UI", 9))
    m.status_bar_text.pack(side=tk.LEFT, padx=5)
    m.tunnel_count_label = ttk.Label(status_bar, text="Tunnels: 0", font=("Segoe UI", 9))
    m.tunnel_count_label.pack(side=tk.RIGHT, padx=5)
    m.update_status_bar()


def create_tunnel_context_menu(m):
    m.tunnel_context_menu = tk.Menu(m.root, tearoff=0)
    m.tunnel_context_menu.add_command(label="Start Tunnel", command=m.start_selected_tunnel)
    m.tunnel_context_menu.add_command(label="Stop Tunnel", command=m.stop_selected_tunnel)
    m.tunnel_context_menu.add_command(label="Restart Tunnel", command=m.restart_selected_tunnel)
    m.tunnel_context_menu.add_command(label="Restart All in Connection", command=m.restart_all_under_connection)
    m.tunnel_context_menu.add_separator()
    m.tunnel_context_menu.add_command(label="View Details", command=m.view_tunnel_details)
    m.tunnel_context_menu.add_command(label="Copy Command", command=m.copy_tunnel_command)
    m.tunnel_context_menu.add_command(label="Edit", command=m.edit_selected_tunnel)
    m.tunnel_tree.bind("<Button-3>", m.show_tunnel_context_menu)


def schedule_updates(m):
    if m.headless:
        return

    def tick():
        if not getattr(m, "running", False):
            return
        try:
            if getattr(m, "_window_visible", True):
                m.refresh_tunnels_quiet()
                current = m.notebook.tab(m.notebook.select(), "text")
                if "Activity Log" in current:
                    m.update_log_display()
        except Exception as exc:
            logging.debug("Periodic update failed: %s", exc)
        if hasattr(m, "root"):
            m.root.after(5000, tick)

    m.root.after(5000, tick)


def on_window_focus(m, event=None):
    if event and event.widget is not m.root:
        return
    m._window_visible = True


def on_window_unfocus(m, event=None):
    if event and event.widget is not m.root:
        return
    m._window_visible = False


def tunnel_config_dialog(m, existing_config=None, prefill=None):
    dialog = tk.Toplevel(m.root)
    dialog.title("Add Tunnel" if not existing_config else "Edit Tunnel")
    dialog.geometry("720x740")
    dialog.resizable(False, False)
    dialog.transient(m.root)
    dialog.grab_set()
    dialog.update_idletasks()
    x = (dialog.winfo_screenwidth() // 2) - (dialog.winfo_width() // 2)
    y = (dialog.winfo_screenheight() // 2) - (dialog.winfo_height() // 2)
    dialog.geometry(f"+{x}+{y}")

    main_frame = ttk.Frame(dialog, padding=20)
    main_frame.pack(fill=tk.BOTH, expand=True)
    ttk.Label(
        main_frame,
        text="Add New Tunnel" if not existing_config else "Edit Tunnel Configuration",
        style="Title.TLabel",
    ).pack(pady=(0, 16))

    fields_frame = ttk.Frame(main_frame)
    fields_frame.pack(fill=tk.X, pady=(0, 12))

    def labeled_entry(parent, label, **kwargs):
        ttk.Label(parent, text=label, style="Heading.TLabel").pack(anchor=tk.W)
        entry = ttk.Entry(parent, font=("Segoe UI", 10), width=50, **kwargs)
        entry.pack(fill=tk.X, pady=(5, 10))
        return entry

    name_entry = labeled_entry(fields_frame, "Tunnel name")
    user_entry = labeled_entry(fields_frame, "Username")
    host_entry = labeled_entry(fields_frame, "Host / IP address")

    ttk.Label(fields_frame, text="Tunnel type", style="Heading.TLabel").pack(anchor=tk.W)
    type_row = ttk.Frame(fields_frame)
    type_row.pack(fill=tk.X, pady=(5, 10))
    type_var = tk.StringVar(value="reverse")
    type_combo = ttk.Combobox(
        type_row,
        textvariable=type_var,
        values=list(TUNNEL_TYPES),
        state="readonly",
        width=16,
    )
    type_combo.pack(side=tk.LEFT)
    type_hint = ttk.Label(type_row, text=TUNNEL_TYPE_LABELS["reverse"], foreground="#6c757d")
    type_hint.pack(side=tk.LEFT, padx=(10, 0))

    ports_label = ttk.Label(fields_frame, text="Port mappings (remote:local, comma-separated)", style="Heading.TLabel")
    ports_label.pack(anchor=tk.W)
    ports_entry = ttk.Entry(fields_frame, font=("Segoe UI", 10), width=50)
    ports_entry.pack(fill=tk.X, pady=(5, 4))
    ports_hint = ttk.Label(fields_frame, text="Example: 8080:8080  (remote 8080 → this PC 8080)", foreground="#6c757d")
    ports_hint.pack(anchor=tk.W, pady=(0, 10))

    def update_type_hints(*_args):
        kind = normalize_tunnel_type(type_var.get())
        type_hint.config(text=TUNNEL_TYPE_LABELS.get(kind, kind))
        if kind == "local":
            ports_label.config(text="Port mappings (local:remote, comma-separated)")
            ports_hint.config(text="Example: 4000:4000  (this PC 4000 → remote 4000)")
        elif kind == "dynamic":
            ports_label.config(text="SOCKS listen port")
            ports_hint.config(text="Example: 1080  (local SOCKS5 proxy)")
        else:
            ports_label.config(text="Port mappings (remote:local, comma-separated)")
            ports_hint.config(text="Example: 8080:8080  (remote 8080 → this PC 8080)")

    type_combo.bind("<<ComboboxSelected>>", update_type_hints)

    ttk.Label(fields_frame, text="SSH port", style="Heading.TLabel").pack(anchor=tk.W)
    port_row = ttk.Frame(fields_frame)
    port_row.pack(fill=tk.X, pady=(5, 10))
    ssh_port_entry = ttk.Entry(port_row, font=("Segoe UI", 10), width=8)
    ssh_port_entry.insert(0, "22")
    ssh_port_entry.pack(side=tk.LEFT)

    ttk.Label(fields_frame, text="Identity file (optional)", style="Heading.TLabel").pack(anchor=tk.W)
    ident_row = ttk.Frame(fields_frame)
    ident_row.pack(fill=tk.X, pady=(5, 10))
    identity_entry = ttk.Entry(ident_row, font=("Segoe UI", 10))
    identity_entry.pack(side=tk.LEFT, fill=tk.X, expand=True)

    def browse_identity():
        path = filedialog.askopenfilename(
            parent=dialog,
            title="Select SSH private key",
            filetypes=[("All files", "*.*"), ("Private keys", "id_* *.pem")],
        )
        if path:
            identity_entry.delete(0, tk.END)
            identity_entry.insert(0, path)

    ttk.Button(ident_row, text="Browse", command=browse_identity).pack(side=tk.LEFT, padx=(8, 0))

    ttk.Label(fields_frame, text="Authentication", style="Heading.TLabel").pack(anchor=tk.W)
    auth_frame = ttk.Frame(fields_frame)
    auth_frame.pack(fill=tk.X, pady=(5, 10))
    auth_var = tk.StringVar(value="key")

    password_frame = ttk.Frame(fields_frame)
    password_label = ttk.Label(password_frame, text="Password", style="Heading.TLabel")
    password_entry = ttk.Entry(password_frame, font=("Segoe UI", 10), width=50, show="*")

    def toggle_password_field(*_args):
        if auth_var.get() == "password":
            password_label.pack(anchor=tk.W)
            password_entry.pack(fill=tk.X, pady=(5, 0))
            password_frame.pack(fill=tk.X, pady=(0, 10))
        else:
            password_label.pack_forget()
            password_entry.pack_forget()
            password_frame.pack_forget()

    ttk.Radiobutton(auth_frame, text="SSH key (recommended)", variable=auth_var, value="key", command=toggle_password_field).pack(
        side=tk.LEFT, padx=(0, 20)
    )
    ttk.Radiobutton(
        auth_frame, text="Username / password", variable=auth_var, value="password", command=toggle_password_field
    ).pack(side=tk.LEFT)

    auto_start_var = tk.BooleanVar(value=False)
    ttk.Checkbutton(fields_frame, text="Auto-start this tunnel when the app launches", variable=auto_start_var).pack(
        anchor=tk.W, pady=(0, 10)
    )

    desc_entry = labeled_entry(fields_frame, "Description (optional)")

    if existing_config:
        name_entry.insert(0, existing_config["name"])
        user_entry.insert(0, existing_config["user"])
        host_entry.insert(0, existing_config["host"])
        type_var.set(normalize_tunnel_type(existing_config.get("tunnel_type", "reverse")))
        update_type_hints()
        ports_entry.insert(0, existing_config["ports"])
        desc_entry.insert(0, existing_config.get("description", ""))
        identity_entry.insert(0, existing_config.get("identity_file", ""))
        ssh_port_entry.delete(0, tk.END)
        ssh_port_entry.insert(0, str(existing_config.get("ssh_port", "22") or "22"))
        auto_start_var.set(bool(existing_config.get("auto_start")))
        auth_var.set(existing_config.get("auth_method", "key"))
        if existing_config.get("auth_method") == "password" and existing_config.get("password"):
            password_entry.insert(0, existing_config["password"])
        toggle_password_field()
        name_entry.config(state="readonly")
    elif prefill:
        if prefill.get("user"):
            user_entry.insert(0, prefill["user"])
        if prefill.get("host"):
            host_entry.insert(0, prefill["host"])

    button_frame = ttk.Frame(main_frame)
    button_frame.pack(fill=tk.X, pady=8)

    def save_tunnel():
        name = name_entry.get().strip()
        user = user_entry.get().strip()
        host = host_entry.get().strip()
        ports = ports_entry.get().strip()
        tunnel_type = normalize_tunnel_type(type_var.get())
        description = desc_entry.get().strip()
        auth_method = auth_var.get()
        password = password_entry.get() if auth_method == "password" else ""
        identity_file = identity_entry.get().strip()
        ssh_port = ssh_port_entry.get().strip() or "22"

        if not all([name, user, host, ports]) or (auth_method == "password" and not password):
            messagebox.showerror("Validation Error", "Please fill in all required fields.", parent=dialog)
            return
        if not existing_config and name in m._saved_tunnels:
            messagebox.showerror("Name Exists", "A tunnel with this name already exists.", parent=dialog)
            return
        try:
            validate_ports(ports, tunnel_type)
            port_num = int(ssh_port)
            if port_num < 1 or port_num > 65535:
                raise ValueError("SSH port out of range")
        except ValueError as exc:
            messagebox.showerror("Invalid Input", str(exc), parent=dialog)
            return
        if identity_file and not os.path.exists(identity_file):
            messagebox.showerror("Missing Key", f"Identity file not found:\n{identity_file}", parent=dialog)
            return

        tunnel_cfg = {
            "name": name,
            "user": user,
            "host": host,
            "ports": ports,
            "tunnel_type": tunnel_type,
            "description": description,
            "auth_method": auth_method,
            "password": password if auth_method == "password" else "",
            "identity_file": identity_file,
            "ssh_port": str(port_num),
            "auto_start": bool(auto_start_var.get()),
        }
        try:
            m.save_tunnel_config(tunnel_cfg)
            dialog.destroy()
            m.refresh_tunnels()
            messagebox.showinfo("Saved", f"Tunnel '{name}' saved.")
        except Exception as exc:
            messagebox.showerror("Error", f"Failed to save tunnel: {exc}", parent=dialog)

    def test_connection():
        user = user_entry.get().strip()
        host = host_entry.get().strip()
        auth_method = auth_var.get()
        password = password_entry.get() if auth_method == "password" else ""
        identity_file = identity_entry.get().strip()
        if not user or not host:
            messagebox.showerror("Missing Info", "Enter username and host first.", parent=dialog)
            return
        try:
            ssh_port = int(ssh_port_entry.get().strip() or "22")
            cmd = build_ssh_command(
                user,
                host,
                test=True,
                auth_method=auth_method,
                identity_file=identity_file,
                ssh_port=ssh_port,
                host_key_policy=m.host_key_policy(),
            )
            env = None
            cleanup = None
            if auth_method == "password":
                if not password:
                    messagebox.showerror("Missing Password", "Enter a password first.", parent=dialog)
                    return
                cmd, env, cleanup = wrap_password_command(cmd, password)
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=15, env=env)
            if cleanup:
                from ssh_utils import cleanup_path

                cleanup_path(cleanup)
            if result.returncode == 0:
                messagebox.showinfo("Test Success", "SSH connection successful.", parent=dialog)
            else:
                error_msg = (result.stderr or result.stdout or "Unknown error")[:400]
                messagebox.showerror("Test Failed", f"Connection failed:\n{error_msg}", parent=dialog)
        except Exception as exc:
            messagebox.showerror("Test Error", f"Connection test failed: {exc}", parent=dialog)

    ttk.Button(button_frame, text="Test Connection", command=test_connection).pack(side=tk.LEFT, padx=5)
    ttk.Button(button_frame, text="Cancel", command=dialog.destroy).pack(side=tk.RIGHT, padx=5)
    ttk.Button(button_frame, text="Save", command=save_tunnel, style="Primary.TButton").pack(side=tk.RIGHT, padx=5)


def show_external_tunnels_dialog(m, processes):
    dialog = tk.Toplevel(m.root)
    dialog.title("External SSH Tunnels")
    dialog.geometry("760x500")
    dialog.transient(m.root)
    dialog.grab_set()
    dialog.update_idletasks()
    x = (dialog.winfo_screenwidth() // 2) - (dialog.winfo_width() // 2)
    y = (dialog.winfo_screenheight() // 2) - (dialog.winfo_height() // 2)
    dialog.geometry(f"+{x}+{y}")

    main_frame = ttk.Frame(dialog, padding=20)
    main_frame.pack(fill=tk.BOTH, expand=True)
    ttk.Label(main_frame, text="External SSH tunnel processes", style="Title.TLabel").pack(pady=(0, 12))
    ttk.Label(
        main_frame,
        text="These ssh -R / -L / -D processes are running on this machine. Stop one, or leave them alone.",
        foreground="#6c757d",
    ).pack(pady=(0, 12))

    list_frame = ttk.Frame(main_frame)
    list_frame.pack(fill=tk.BOTH, expand=True, pady=(0, 15))
    columns = ("PID", "Type", "Connection", "Ports", "Uptime")
    tree = ttk.Treeview(list_frame, columns=columns, show="headings", height=8)
    for col, label, width, anchor in (
        ("PID", "PID", 70, tk.CENTER),
        ("Type", "Type", 80, tk.CENTER),
        ("Connection", "User@Host", 220, tk.W),
        ("Ports", "Port mappings", 220, tk.W),
        ("Uptime", "Uptime", 100, tk.CENTER),
    ):
        tree.heading(col, text=label)
        tree.column(col, width=width, anchor=anchor)

    for proc in processes:
        uptime = proc.get("duration") or m.format_duration(int(time.time() - proc.get("create_time", time.time())))
        tree.insert(
            "",
            tk.END,
            values=(
                proc["pid"],
                proc.get("tunnel_type", "reverse"),
                proc.get("user_host", ""),
                proc.get("ports", ""),
                uptime,
            ),
        )

    scrollbar = ttk.Scrollbar(list_frame, orient=tk.VERTICAL, command=tree.yview)
    tree.configure(yscrollcommand=scrollbar.set)
    tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
    scrollbar.pack(side=tk.RIGHT, fill=tk.Y)

    button_frame = ttk.Frame(main_frame)
    button_frame.pack(fill=tk.X, pady=10)

    def stop_selected():
        item = tree.selection()
        if not item:
            messagebox.showwarning("No Selection", "Select a process to stop.", parent=dialog)
            return
        pid = int(tree.item(item[0], "values")[0])
        if pu_terminate(pid):
            tree.delete(item[0])
            messagebox.showinfo("Stopped", f"Stopped process {pid}.", parent=dialog)
            m.refresh_tunnels()
        else:
            messagebox.showerror("Error", f"Could not stop process {pid}.", parent=dialog)

    def stop_all():
        if not messagebox.askyesno("Stop All", "Stop every listed SSH tunnel process?", parent=dialog):
            return
        stopped = 0
        for iid in list(tree.get_children()):
            pid = int(tree.item(iid, "values")[0])
            if pu_terminate(pid):
                tree.delete(iid)
                stopped += 1
        messagebox.showinfo("Done", f"Stopped {stopped} process(es).", parent=dialog)
        m.refresh_tunnels()

    ttk.Button(button_frame, text="Stop Selected", command=stop_selected, style="Danger.TButton").pack(side=tk.LEFT, padx=5)
    ttk.Button(button_frame, text="Stop All", command=stop_all, style="Danger.TButton").pack(side=tk.LEFT, padx=5)
    ttk.Button(button_frame, text="Close", command=dialog.destroy).pack(side=tk.RIGHT, padx=5)


def pu_terminate(pid: int) -> bool:
    try:
        proc = psutil.Process(pid)
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except psutil.TimeoutExpired:
            proc.kill()
        return True
    except psutil.NoSuchProcess:
        return True
    except Exception as exc:
        logging.error("Failed to stop pid %s: %s", pid, exc)
        return False


def update_log_display(m):
    if m.headless or not hasattr(m, "log_text"):
        return
    try:
        if not os.path.exists(LOG_FILE):
            return
        with open(LOG_FILE, "r", encoding="utf-8", errors="replace") as handle:
            lines = handle.readlines()
        recent = lines[-200:] if len(lines) > 200 else lines
        at_end = m.log_text.yview()[1] >= 0.95
        m.log_text.delete("1.0", tk.END)
        for line in recent:
            tag = None
            if " - ERROR - " in line:
                tag = "ERROR"
            elif " - WARNING - " in line:
                tag = "WARNING"
            elif " - DEBUG - " in line:
                tag = "DEBUG"
            elif " - INFO - " in line:
                tag = "INFO"
            if tag:
                m.log_text.insert(tk.END, line, tag)
            else:
                m.log_text.insert(tk.END, line)
        if at_end:
            m.log_text.see(tk.END)
    except Exception as exc:
        logging.error("Error updating log display: %s", exc)


def show_tunnel_context_menu(m, event):
    try:
        item = m.tunnel_tree.identify_row(event.y)
        if item:
            m.tunnel_tree.selection_set(item)
            m.tunnel_context_menu.post(event.x_root, event.y_root)
    except Exception as exc:
        logging.error("Error showing context menu: %s", exc)


def view_tunnel_details(m):
    try:
        selection = m.tunnel_tree.selection()
        if not selection:
            messagebox.showwarning("No Selection", "Select a tunnel to view details.")
            return
        item = selection[0]
        values = m.tunnel_tree.item(item, "values")
        if not values:
            messagebox.showwarning("Invalid Selection", "Select a specific tunnel under a connection.")
            return
        name, tunnel_type, ports, _desc, status, pid_str, duration = values[:7]
        connection = m.get_connection_for_item(item)
        saved = m._saved_tunnels.get(name, {})
        extra = ""
        if saved:
            extra = (
                f"\nType: {TUNNEL_TYPE_LABELS.get(normalize_tunnel_type(saved.get('tunnel_type', tunnel_type)), tunnel_type)}"
                f"\nAuth: {saved.get('auth_method', 'key')}"
                f"\nSSH port: {saved.get('ssh_port', '22')}"
                f"\nIdentity: {saved.get('identity_file') or '(default agent/keys)'}"
                f"\nAuto-start: {'yes' if saved.get('auto_start') else 'no'}"
            )
        if pid_str == "-":
            messagebox.showinfo(
                "Tunnel Details",
                f"Name: {name}\nConnection: {connection}\nType: {tunnel_type}\nPorts: {ports}\nStatus: {status}\nUptime: {duration}{extra}",
            )
            return
        proc = psutil.Process(int(pid_str))
        info = (
            f"Name: {name}\nPID: {pid_str}\nConnection: {connection}\nType: {tunnel_type}\nPorts: {ports}\n"
            f"Status: {status}\nUptime: {duration}{extra}\n\n"
            f"Executable: {proc.exe()}\nCommand: {' '.join(proc.cmdline())}\n"
            f"Memory: {proc.memory_info().rss / 1024 / 1024:.1f} MB\n"
            f"Started: {datetime.fromtimestamp(proc.create_time()).strftime('%Y-%m-%d %H:%M:%S')}"
        )
        messagebox.showinfo("Tunnel Details", info)
    except psutil.NoSuchProcess:
        messagebox.showerror("Error", "The selected process no longer exists.")
        m.refresh_tunnels()
    except Exception as exc:
        messagebox.showerror("Error", f"Failed to get tunnel details: {exc}")


def copy_tunnel_command(m):
    try:
        selection = m.tunnel_tree.selection()
        if not selection:
            messagebox.showwarning("No Selection", "Select a tunnel first.")
            return
        item = selection[0]
        values = m.tunnel_tree.item(item, "values")
        if not values:
            messagebox.showwarning("Invalid Selection", "Select a specific tunnel under a connection.")
            return
        name = values[0]
        pid_str = values[5]
        cmdline = None
        if pid_str and pid_str != "-":
            cmdline = " ".join(psutil.Process(int(pid_str)).cmdline())
        elif name in m._saved_tunnels:
            cfg = m._saved_tunnels[name]
            cmdline = " ".join(
                build_ssh_command(
                    cfg["user"],
                    cfg["host"],
                    cfg["ports"],
                    auth_method=cfg.get("auth_method", "key"),
                    identity_file=cfg.get("identity_file", ""),
                    ssh_port=int(cfg.get("ssh_port") or 22),
                    host_key_policy=m.host_key_policy(),
                    tunnel_type=cfg.get("tunnel_type", "reverse"),
                )
            )
        else:
            messagebox.showwarning("Not Managed", "This tunnel is not a saved configuration.")
            return
        m.root.clipboard_clear()
        m.root.clipboard_append(cmdline)
        m.root.update()
        messagebox.showinfo("Copied", "SSH command copied to the clipboard.")
    except psutil.NoSuchProcess:
        messagebox.showerror("Error", "The selected process no longer exists.")
        m.refresh_tunnels()
    except Exception as exc:
        messagebox.showerror("Error", f"Failed to copy command: {exc}")


def update_status_bar(m):
    if m.headless or not hasattr(m, "status_bar_text"):
        return
    try:
        running = m.is_tunnel_running()
        m.connection_indicator.config(foreground="green" if running else "red")
        m.status_bar_text.config(text="Connected" if running else "Idle")
        if hasattr(m, "status_label"):
            m.status_label.config(
                text="Running" if running else "Idle",
                foreground=m.colors["success"] if running else m.colors["warning"],
            )
            m.status_detail.config(text="At least one SSH reverse tunnel is active" if running else "No active connections")
    except Exception as exc:
        logging.error("Error updating status bar: %s", exc)
