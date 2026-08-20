# SSH Tunnel Manager

A Windows desktop app for saving, starting, and watching SSH tunnels. Reverse (`ssh -R`), local (`ssh -L`), and dynamic SOCKS (`ssh -D`) profiles are supported. It has a tabbed UI and can live in the system tray.

![Platform](https://img.shields.io/badge/Platform-Windows-blue)
![Python](https://img.shields.io/badge/Python-3.9+-green)
![License](https://img.shields.io/badge/License-MIT-yellow)

## Features

- Save multiple tunnel profiles, grouped by `user@host`
- Tunnel types: **reverse** (`-R`), **local** (`-L`), **dynamic SOCKS** (`-D`)
- Start / stop / restart one tunnel or everything under a connection
- Detect already-running `ssh` forwards and attach them as **External**
- Optional identity file, non-default SSH port, and per-tunnel auto-start
- System tray, Windows startup, and `--headless` background mode
- Host-key policy (`accept-new` by default — not `StrictHostKeyChecking=no`)
- Activity log with rotation

## Requirements

- Windows 10/11
- Python 3.9+ if running from source
- OpenSSH client (`ssh`) — included with current Windows

## Run from source

```bash
git clone https://github.com/jwlionking/SSH-Tunnel-Manager.git
cd SSH-Tunnel-Manager
python -m pip install -r requirements.txt
python tunnel_manager.py
```

Useful flags:

```bash
python tunnel_manager.py --headless
python tunnel_manager.py --debug
```

## Build an .exe

```bash
python build_exe.py
```

Or:

```bash
python -m pip install -r requirements-build.txt
pyinstaller SSH_Tunnel_Manager.spec
```

The binary lands in `dist/SSH_Tunnel_Manager.exe`.

## Usage

1. Open the **Tunnels** tab and click **Add Tunnel**.
2. Pick a type:
   - **reverse** — expose a port on this PC to the remote host (`8080:8080` = remote 8080 → local 8080)
   - **local** — bring a remote port here (`4000:4000` = local 4000 → remote 4000)
   - **dynamic** — SOCKS5 proxy on this PC (port `1080`)
3. Enter name, username, host, and mappings.
4. Optionally pick an identity file, SSH port, or mark the tunnel to auto-start.
5. Save, select the row, and click **Start**.

The **Connection** tab is a quick-start form for reverse tunnels. **Create & Start Tunnel** writes a saved profile named `QuickStart`.

### Settings

- Auto-start saved tunnels on launch (tunnels with the per-tunnel checkbox, or all of them if none are marked)
- Start with Windows (tray / headless)
- Minimize to tray instead of quitting
- Stop tunnels when the app exits
- Host key policy

## Configuration

Settings and tunnels are stored next to the app in `tunnel_manager.ini`.

```ini
[Settings]
auto_start = True
minimize_to_tray = True
start_with_windows = False
stop_tunnels_on_exit = True
host_key_policy = accept-new

[Tunnel_MyTunnel]
name = MyTunnel
user = root
host = 192.168.1.100
ports = 8080:8080,3000:3000
tunnel_type = reverse
description = Dev box
auth_method = key
identity_file =
ssh_port = 22
auto_start = true

[Tunnel_TeslaMate]
name = TeslaMate
user = jeremy
host = home.lan
ports = 4000:4000
tunnel_type = local
description = TeslaMate UI
auth_method = key
ssh_port = 22
auto_start = true

[Tunnel_Socks]
name = Socks
user = jeremy
host = home.lan
ports = 1080
tunnel_type = dynamic
description = SOCKS5 via home
auth_method = key
ssh_port = 22
auto_start = false
```

Password auth is supported but SSH keys are the better path. On Windows, stored passwords are protected with DPAPI. They are never written into a `.bat` file or placed on the `ssh` command line.

## Project layout

```
SSH-Tunnel-Manager/
├── tunnel_manager.py        # App entry point
├── tunnel_ui.py             # Tkinter UI
├── config_store.py          # INI persistence
├── process_utils.py         # ssh process scan / stop
├── ssh_utils.py             # Command builder and port matching
├── logger_utils.py          # Rotating file log
├── build_exe.py             # PyInstaller helper
├── SSH_Tunnel_Manager.spec
├── requirements.txt
├── requirements-build.txt
└── tests/
```

## Tests

```bash
python -m unittest discover -s tests -v
```

## Troubleshooting

**Tunnel exits immediately**  
Open the Activity Log. Typical causes: host key prompt, wrong user, missing key in the agent, or the remote/local port already in use (`ExitOnForwardFailure=yes`).

**Permission denied**  
Confirm the username and that `ssh-agent` has the right key. You can point a tunnel at a specific identity file.

**App disappears on close**  
That is minimize-to-tray. Use the tray icon or turn the setting off.

**Password auth does nothing**  
Install `sshpass` or use keys. The app will not echo a password through a batch file.

## License

MIT. See [LICENSE](LICENSE).
