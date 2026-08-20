# Tunnel Manager Notes

Working desktop app for Windows SSH tunnels (reverse, local, and dynamic SOCKS).

## Recently fixed
- Duplicate methods were shadowing the real save/load path, so Settings never persisted
- GUI dialogs had been redirected to the log only
- External-tunnel matching failed because `8080:8080` was compared to `8080:127.0.0.1:8080`
- Password auth wrote the secret into a temp `.bat` file
- `-vvv` and `StrictHostKeyChecking=no` were hardcoded
- README pointed at a file that does not exist
- Local-forward (`-L`) and dynamic SOCKS (`-D`) profiles

## Still optional
- [ ] Test the compiled exe for tray + Windows startup
