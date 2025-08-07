# SSH Tunnel Manager

A professional, resource-optimized SSH reverse tunnel manager for Windows with GUI and system tray support.

![SSH Tunnel Manager](https://img.shields.io/badge/Platform-Windows-blue)
![Python](https://img.shields.io/badge/Python-3.7+-green)
![License](https://img.shields.io/badge/License-MIT-yellow)

## Features

### 🚀 **Core Functionality**
- **Persistent Tunnel Management** - Save, edit, and manage multiple SSH tunnel configurations
- **One-Click Operations** - Start, stop, restart tunnels with a single click
- **Real-time Status Monitoring** - Live status indicators and process monitoring
- **External Tunnel Detection** - Automatically detect and manage existing SSH tunnels
- **Resource Optimized** - Efficient process scanning with intelligent caching

### 🎨 **User Interface**
- **Modern GUI** - Clean, professional interface built with tkinter
- **System Tray Integration** - Minimize to system tray for background operation
- **Tabbed Interface** - Organized tabs for tunnels, connection settings, and logs
- **Real-time Updates** - Live tunnel status and process information

### ⚙️ **Advanced Features**
- **Windows Startup Integration** - Auto-start with Windows
- **Headless Mode** - Run in background with system tray only
- **Configuration Persistence** - Tunnels and settings saved across restarts
- **Detailed Logging** - Comprehensive logging with debug support
- **Process Management** - Graceful shutdown and cleanup

## Installation

### Option 1: Download Executable (Recommended)
1. Download the latest `SSH_Tunnel_Manager.exe` from the [Releases](../../releases) page
2. Run the executable - no installation required!

### Option 2: Run from Source
1. Clone the repository:
   ```bash
   git clone https://github.com/yourusername/ssh-tunnel-manager.git
   cd ssh-tunnel-manager
   ```

2. Install dependencies:
   ```bash
   pip install -r requirements.txt
   ```

3. Run the application:
   ```bash
   python tunnel_manager_enhanced.py
   ```

### Option 3: Build Your Own Executable

**Using the build script (recommended):**
```bash
python build_exe.py
```

**Using PyInstaller directly:**
```bash
pyinstaller SSH_Tunnel_Manager.spec
```

The executable will be created in the `dist/` directory.

## Usage

### GUI Mode (Default)
```bash
SSH_Tunnel_Manager.exe
```

### Headless Mode (System Tray Only)
```bash
SSH_Tunnel_Manager.exe --headless
```

### Debug Mode
```bash
SSH_Tunnel_Manager.exe --debug
```

## Quick Start Guide

### 1. **Add a Tunnel Configuration**
- Click the **"➕ Add Tunnel"** button
- Fill in your server details:
  - **Name**: A descriptive name for your tunnel
  - **Username**: SSH username
  - **Host**: Server IP or hostname
  - **Ports**: Port mappings (e.g., `8080:8080` or `8080:8080,3000:3000`)
  - **Description**: Optional description
- Click **"💾 Save"**

### 2. **Start a Tunnel**
- Select a tunnel from the list
- Click **"▶️ Start"**
- Monitor the status in real-time

### 3. **Manage Tunnels**
- **▶️ Start** - Start the selected tunnel
- **⏹️ Stop** - Stop the selected tunnel
- **🔄 Restart** - Restart the selected tunnel
- **✏️ Edit** - Modify tunnel configuration
- **🗑️ Delete** - Remove tunnel configuration
- **📊 Details** - View detailed tunnel information

## Configuration

### Tunnel Configuration Format
```ini
[Tunnel_MyTunnel]
user = root
host = 192.168.1.100
ports = 8080:8080,3000:3000
description = My development server tunnel
last_pid = 12345
last_started = 1704649200
```

### Settings
- **Auto-start tunnel on app launch**
- **Minimize to system tray**
- **Start with Windows**
- **Debug logging**

## System Requirements

- **Operating System**: Windows 10/11
- **Python**: 3.7+ (if running from source)
- **SSH Client**: OpenSSH (included in Windows 10/11)
- **Memory**: ~20MB RAM
- **Disk Space**: ~50MB

## Dependencies

```
pillow>=9.0.0
pystray>=0.19.0
psutil>=5.8.0
pyinstaller>=5.0.0
```

## Architecture

### Resource Optimization
- **Process Caching**: SSH processes cached for 60 seconds to reduce CPU usage
- **Manual Refresh**: Updates triggered by user actions, not automatic timers
- **Efficient Scanning**: Optimized process detection and monitoring

### File Structure
```
ssh-tunnel-manager/
├── tunnel_manager_enhanced.py    # Main application
├── build_exe.py                  # Build script for executable
├── requirements.txt              # Python dependencies
├── tunnel_icon.ico               # Application icon
├── tunnel_manager.ini            # Configuration file (auto-generated)
├── tunnel_manager.log            # Log file (auto-generated)
└── README.md                     # This file
```

## Troubleshooting

### Common Issues

**Q: Tunnel fails to start with "Connection refused"**
A: Check your SSH credentials and ensure the target server is accessible.

**Q: "Permission denied" error**
A: Ensure your SSH key is properly configured or use password authentication.

**Q: App doesn't minimize to system tray**
A: Enable "Minimize to system tray" in Settings tab.

**Q: Tunnels don't persist after restart**
A: Check that the configuration file `tunnel_manager.ini` is being created and saved.

### Debug Mode
Run with `--debug` flag to enable detailed logging:
```bash
SSH_Tunnel_Manager.exe --debug
```

Check the `tunnel_manager.log` file for detailed error information.

## Contributing

1. Fork the repository
2. Create a feature branch (`git checkout -b feature/amazing-feature`)
3. Commit your changes (`git commit -m 'Add amazing feature'`)
4. Push to the branch (`git push origin feature/amazing-feature`)
5. Open a Pull Request

## License

This project is licensed under the MIT License - see the [LICENSE](LICENSE) file for details.

## Changelog

### v1.0.0
- ✅ Complete tunnel lifecycle management (add, edit, start, stop, delete)
- ✅ Resource optimization with process caching
- ✅ Modern GUI with system tray integration
- ✅ Configuration persistence across restarts
- ✅ External tunnel detection and management
- ✅ Windows startup integration
- ✅ Headless mode support
- ✅ Graceful shutdown handling
- ✅ Comprehensive logging and debugging

## Support

If you find this project helpful, please consider:
- ⭐ Starring the repository
- 🐛 Reporting issues
- 💡 Suggesting new features
- 🤝 Contributing code

## Author

Created with ❤️ for the SSH tunneling community.

---

**Note**: This application is designed for legitimate network administration and development purposes. Please ensure you have proper authorization before creating SSH tunnels to any server.
