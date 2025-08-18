"""
Build script to compile the SSH Tunnel Manager to an executable
"""
import subprocess
import sys
import os

def install_requirements():
    """Install required packages"""
    print("Installing required packages...")
    subprocess.check_call([sys.executable, "-m", "pip", "install", "-r", "requirements.txt"])

def build_exe():
    """Build the executable using PyInstaller"""
    print("Building executable...")
    
    # PyInstaller command (invoke via the running Python to avoid PATH issues)
    cmd = [
        sys.executable, "-m", "PyInstaller",
        "--onefile",                    # Single executable file
        "--windowed",                   # No console window
        "--name=SSH_Tunnel_Manager",    # Executable name
        "--icon=tunnel_icon.ico",       # Icon file (optional)
        "--add-data=tunnel_manager.ini;.",  # Include config file if it exists
        "--hidden-import=pystray._win32",   # Required for system tray
        "--hidden-import=PIL._tkinter_finder",  # Required for PIL
        "tunnel_manager.py"
    ]
    
    # Remove icon parameter if icon file doesn't exist
    if not os.path.exists("tunnel_icon.ico"):
        cmd = [arg for arg in cmd if not arg.startswith("--icon")]
    
    # Remove config file if it doesn't exist
    if not os.path.exists("tunnel_manager.ini"):
        cmd = [arg for arg in cmd if not arg.startswith("--add-data")]
    
    try:
        subprocess.check_call(cmd)
        print("\nBuild completed successfully!")
        print("Executable created: dist/SSH_Tunnel_Manager.exe")
        print("\nTo run in GUI mode: SSH_Tunnel_Manager.exe")
        print("To run in headless mode: SSH_Tunnel_Manager.exe --headless")
    except subprocess.CalledProcessError as e:
        print(f"Build failed: {e}")
        return False
    
    return True

def create_simple_icon():
    """Create a simple icon file"""
    try:
        from PIL import Image, ImageDraw
        
        # Create a simple 32x32 icon
        img = Image.new('RGBA', (32, 32), (0, 0, 0, 0))
        draw = ImageDraw.Draw(img)
        
        # Draw a simple tunnel/connection icon
        draw.rectangle([4, 12, 28, 20], fill=(0, 100, 200, 255))
        draw.ellipse([2, 10, 10, 22], fill=(0, 100, 200, 255))
        draw.ellipse([22, 10, 30, 22], fill=(0, 100, 200, 255))
        
        img.save("tunnel_icon.ico", format='ICO')
        print("Created simple icon: tunnel_icon.ico")
    except Exception as e:
        print(f"Could not create icon: {e}")

if __name__ == "__main__":
    print("SSH Tunnel Manager - Build Script")
    print("=" * 40)
    
    # Install requirements
    install_requirements()
    
    # Create icon if it doesn't exist
    if not os.path.exists("tunnel_icon.ico"):
        create_simple_icon()
    
    # Build executable
    if build_exe():
        print("\nBuild process completed successfully!")
        print("\nNext steps:")
        print("1. Test the executable: dist/SSH_Tunnel_Manager.exe")
        print("2. For startup integration, run with GUI and enable 'Start with Windows'")
        print("3. The app will run in system tray when started with --headless")
    else:
        print("\nBuild process failed. Check the error messages above.")
