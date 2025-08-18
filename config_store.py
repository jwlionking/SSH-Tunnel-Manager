import configparser
import logging
import os
from typing import Dict, Any


def encrypt_password(password: str) -> str:
    """Simple password obfuscation (base64). Not secure; for UI convenience only."""
    try:
        import base64
        encoded = base64.b64encode(password.encode('utf-8')).decode('utf-8')
        return f"enc:{encoded}"
    except Exception as e:
        logging.error(f"Error encrypting password: {e}")
        return password


def decrypt_password(encrypted_password: str) -> str:
    """Reverse of encrypt_password."""
    try:
        if encrypted_password.startswith('enc:'):
            import base64
            encoded = encrypted_password[4:]
            return base64.b64decode(encoded.encode('utf-8')).decode('utf-8')
        return encrypted_password
    except Exception as e:
        logging.error(f"Error decrypting password: {e}")
        return encrypted_password


def save_config(config: configparser.ConfigParser, config_file: str) -> None:
    os.makedirs(os.path.dirname(config_file), exist_ok=True)
    with open(config_file, 'w') as f:
        config.write(f)


def load_saved_tunnels(config: configparser.ConfigParser, config_file: str) -> Dict[str, Dict[str, Any]]:
    """Load saved tunnel configurations from an INI file into a dict keyed by tunnel name."""
    try:
        tunnels: Dict[str, Dict[str, Any]] = {}
        if not os.path.exists(config_file):
            logging.info(f"Config file not found, creating new: {config_file}")
            with open(config_file, 'w'):
                pass
        config.read(config_file)

        for section_name in config.sections():
            if not section_name.startswith('Tunnel_'):
                continue
            section = config[section_name]
            tunnel_name = section.get('name', section_name[7:])
            tunnels[tunnel_name] = {
                'name': tunnel_name,
                'user': section.get('user', ''),
                'host': section.get('host', ''),
                'ports': section.get('ports', ''),
                'description': section.get('description', ''),
                'auth_method': section.get('auth_method', 'key'),
                'password': decrypt_password(section.get('password', '')) if section.get('password') else ''
            }
        logging.info(f"Loaded {len(tunnels)} saved tunnel configurations")
        return tunnels
    except Exception as e:
        logging.error(f"Error loading saved tunnels: {e}")
        return {}


def save_tunnel_config(config: configparser.ConfigParser, config_file: str, tunnel_config: Dict[str, Any]) -> None:
    """Persist a tunnel configuration to the INI file."""
    try:
        tunnel_name = tunnel_config['name']
        section_name = f"Tunnel_{tunnel_name}"
        if section_name in config:
            config.remove_section(section_name)
        config.add_section(section_name)
        section = config[section_name]
        section['name'] = tunnel_config['name']
        section['user'] = tunnel_config['user']
        section['host'] = tunnel_config['host']
        section['ports'] = tunnel_config['ports']
        section['description'] = tunnel_config.get('description', '')
        section['auth_method'] = tunnel_config.get('auth_method', 'key')
        if tunnel_config.get('auth_method') == 'password' and tunnel_config.get('password'):
            section['password'] = encrypt_password(tunnel_config['password'])
        save_config(config, config_file)
        logging.info(f"Saved tunnel configuration: {tunnel_name}")
    except Exception as e:
        logging.error(f"Error saving tunnel configuration: {e}")
        raise


def delete_tunnel_config(config: configparser.ConfigParser, config_file: str, tunnel_name: str) -> None:
    try:
        section_name = f"Tunnel_{tunnel_name}"
        if section_name in config:
            config.remove_section(section_name)
            save_config(config, config_file)
        logging.info(f"Deleted tunnel configuration: {tunnel_name}")
    except Exception as e:
        logging.error(f"Error deleting tunnel configuration: {e}")
        raise


def save_tunnel_pid(config: configparser.ConfigParser, config_file: str, tunnel_name: str, pid: int) -> None:
    try:
        section_name = f"Tunnel_{tunnel_name}"
        if section_name in config:
            config[section_name]['pid'] = str(pid)
            save_config(config, config_file)
    except Exception as e:
        logging.error(f"Error saving tunnel PID: {e}")


def clear_tunnel_pid(config: configparser.ConfigParser, config_file: str, tunnel_name: str) -> None:
    try:
        section_name = f"Tunnel_{tunnel_name}"
        if section_name in config and 'pid' in config[section_name]:
            del config[section_name]['pid']
            save_config(config, config_file)
    except Exception as e:
        logging.error(f"Error clearing tunnel PID: {e}")
