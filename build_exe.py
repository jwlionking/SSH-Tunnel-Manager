"""Build script to compile the SSH Tunnel Manager to a Windows executable."""
import os
import subprocess
import sys


def install_requirements():
    print("Installing required packages...")
    subprocess.check_call([sys.executable, "-m", "pip", "install", "-r", "requirements-build.txt"])


def build_exe():
    print("Building executable...")
    cmd = [
        sys.executable,
        "-m",
        "PyInstaller",
        "--noconfirm",
        "--onefile",
        "--windowed",
        "--name=SSH_Tunnel_Manager",
        "--hidden-import=pystray._win32",
        "--hidden-import=PIL._tkinter_finder",
        "tunnel_manager.py",
    ]
    if os.path.exists("tunnel_icon.ico"):
        cmd.insert(-1, "--icon=tunnel_icon.ico")

    try:
        subprocess.check_call(cmd)
        print("\nBuild completed successfully!")
        print("Executable created: dist/SSH_Tunnel_Manager.exe")
        print("\nGUI:     SSH_Tunnel_Manager.exe")
        print("Tray:    SSH_Tunnel_Manager.exe --headless")
        print("Debug:   SSH_Tunnel_Manager.exe --debug")
        return True
    except subprocess.CalledProcessError as exc:
        print(f"Build failed: {exc}")
        return False


def create_simple_icon():
    try:
        from PIL import Image, ImageDraw

        img = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
        draw = ImageDraw.Draw(img)
        draw.rounded_rectangle([6, 6, 58, 58], radius=12, fill=(46, 134, 171, 255))
        draw.rectangle([16, 28, 48, 36], fill=(255, 255, 255, 255))
        img.save("tunnel_icon.ico", format="ICO")
        print("Created simple icon: tunnel_icon.ico")
    except Exception as exc:
        print(f"Could not create icon: {exc}")


if __name__ == "__main__":
    print("SSH Tunnel Manager - Build Script")
    print("=" * 40)
    install_requirements()
    if not os.path.exists("tunnel_icon.ico"):
        create_simple_icon()
    if build_exe():
        print("\nBuild process completed successfully!")
    else:
        print("\nBuild process failed. Check the error messages above.")
        sys.exit(1)
