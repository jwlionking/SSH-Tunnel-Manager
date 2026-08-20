import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config_store import decrypt_password, encrypt_password, load_saved_tunnels, save_tunnel_config
import configparser
from ssh_utils import (
    build_forward_flags,
    build_r_flags,
    build_ssh_command,
    extract_forward_spec,
    normalize_tunnel_type,
    parse_port_pairs,
    ports_match,
    redact_command,
    validate_ports,
)


class PortParsingTests(unittest.TestCase):
    def test_simple_pairs(self):
        self.assertEqual(parse_port_pairs("8080:80, 3000:3000"), [(8080, 80), (3000, 3000)])

    def test_ssh_style_mapping(self):
        self.assertEqual(parse_port_pairs("8080:127.0.0.1:80"), [(8080, 80)])

    def test_match_saved_against_process(self):
        self.assertTrue(ports_match("8080:8080,3000:3000", "8080:127.0.0.1:8080, 3000:127.0.0.1:3000"))
        self.assertFalse(ports_match("8080:8080", "9000:127.0.0.1:9000"))

    def test_invalid_ports(self):
        with self.assertRaises(ValueError):
            validate_ports("not-a-port")

    def test_dynamic_port_ok(self):
        validate_ports("1080", "dynamic")
        validate_ports("127.0.0.1:1080", "dynamic")

    def test_dynamic_port_rejects_list(self):
        with self.assertRaises(ValueError):
            validate_ports("1080,1081", "dynamic")

    def test_local_requires_colon(self):
        with self.assertRaises(ValueError):
            validate_ports("8080", "local")


class SshCommandTests(unittest.TestCase):
    def test_reverse_flags(self):
        self.assertEqual(build_r_flags("8080:80"), ["-R", "8080:127.0.0.1:80"])
        self.assertEqual(build_forward_flags("8080:80", "reverse"), ["-R", "8080:127.0.0.1:80"])

    def test_local_flags(self):
        self.assertEqual(build_forward_flags("4000:80", "local"), ["-L", "4000:127.0.0.1:80"])

    def test_dynamic_flags(self):
        self.assertEqual(build_forward_flags("1080", "dynamic"), ["-D", "1080"])

    def test_key_command_uses_batch_mode(self):
        cmd = build_ssh_command("root", "example.com", "8080:8080")
        self.assertIn("BatchMode=yes", cmd)
        self.assertIn("root@example.com", cmd)
        self.assertNotIn("-vvv", cmd)

    def test_debug_and_custom_port(self):
        cmd = build_ssh_command("admin", "host", "22:22", ssh_port=2222, debug=True, identity_file="C:/keys/id_ed25519")
        self.assertIn("-vvv", cmd)
        self.assertIn("-p", cmd)
        self.assertIn("2222", cmd)
        self.assertIn("-i", cmd)

    def test_test_command_has_no_forward(self):
        cmd = build_ssh_command("root", "host", test=True)
        self.assertNotIn("-R", cmd)
        self.assertNotIn("-L", cmd)
        self.assertNotIn("-D", cmd)
        self.assertIn("echo", cmd)

    def test_local_command(self):
        cmd = build_ssh_command("root", "home.example", "4000:4000", tunnel_type="local")
        self.assertIn("-L", cmd)
        self.assertIn("4000:127.0.0.1:4000", cmd)
        self.assertNotIn("-R", cmd)

    def test_dynamic_command(self):
        cmd = build_ssh_command("root", "home.example", "1080", tunnel_type="socks")
        self.assertIn("-D", cmd)
        self.assertIn("1080", cmd)
        self.assertNotIn("-R", cmd)
        self.assertNotIn("-L", cmd)

    def test_missing_fields(self):
        with self.assertRaises(ValueError):
            build_ssh_command("", "host", "8080:8080")
        with self.assertRaises(ValueError):
            build_ssh_command("root", "host", "")
        with self.assertRaises(ValueError):
            build_ssh_command("root", "host", "", tunnel_type="dynamic")

    def test_redact_is_join(self):
        self.assertEqual(redact_command(["ssh", "-N", "a@b"]), "ssh -N a@b")

    def test_normalize_aliases(self):
        self.assertEqual(normalize_tunnel_type("SOCKS"), "dynamic")
        self.assertEqual(normalize_tunnel_type("-L"), "local")
        self.assertEqual(normalize_tunnel_type(""), "reverse")


class ProcessMatchTests(unittest.TestCase):
    def test_extract_local_and_dynamic(self):
        self.assertEqual(
            extract_forward_spec("ssh -L 4000:127.0.0.1:4000 -N root@home"),
            ("local", "4000:127.0.0.1:4000"),
        )
        self.assertEqual(extract_forward_spec("ssh -D 1080 -N root@home"), ("dynamic", "1080"))
        self.assertEqual(
            extract_forward_spec("ssh -R 8080:127.0.0.1:8080 -N root@vps"),
            ("reverse", "8080:127.0.0.1:8080"),
        )

    def test_type_and_ports_distinguish_processes(self):
        local_cmd = "ssh -L 4000:127.0.0.1:4000 -N root@home"
        reverse_cmd = "ssh -R 4000:127.0.0.1:4000 -N root@home"
        self.assertEqual(extract_forward_spec(local_cmd)[0], "local")
        self.assertEqual(extract_forward_spec(reverse_cmd)[0], "reverse")
        self.assertTrue(ports_match("4000:4000", extract_forward_spec(local_cmd)[1]))
        self.assertTrue(ports_match("4000:4000", extract_forward_spec(reverse_cmd)[1]))
        self.assertNotEqual(extract_forward_spec(local_cmd)[0], extract_forward_spec(reverse_cmd)[0])


class ConfigStoreTests(unittest.TestCase):
    def test_password_roundtrip_obfuscation(self):
        token = encrypt_password("s3cret!")
        self.assertTrue(token.startswith("enc:") or token.startswith("dpapi:"))
        self.assertEqual(decrypt_password(token), "s3cret!")

    def test_save_and_load_tunnel_keeps_pid(self):
        handle, path = tempfile.mkstemp(suffix=".ini")
        os.close(handle)
        try:
            config = configparser.ConfigParser()
            save_tunnel_config(
                config,
                path,
                {
                    "name": "dev",
                    "user": "root",
                    "host": "10.0.0.5",
                    "ports": "8080:8080",
                    "description": "box",
                    "auth_method": "key",
                    "auto_start": True,
                    "ssh_port": "2222",
                    "identity_file": "",
                },
            )
            config.set("Tunnel_dev", "last_pid", "4242")
            from config_store import save_config

            save_config(config, path)
            save_tunnel_config(
                config,
                path,
                {
                    "name": "dev",
                    "user": "root",
                    "host": "10.0.0.5",
                    "ports": "8080:80",
                    "description": "updated",
                    "auth_method": "key",
                    "auto_start": True,
                    "ssh_port": "2222",
                    "identity_file": "",
                },
            )
            loaded = load_saved_tunnels(configparser.ConfigParser(), path)
            self.assertEqual(loaded["dev"]["ports"], "8080:80")
            self.assertEqual(loaded["dev"]["last_pid"], "4242")
            self.assertTrue(loaded["dev"]["auto_start"])
            self.assertEqual(loaded["dev"]["ssh_port"], "2222")
            self.assertEqual(loaded["dev"]["tunnel_type"], "reverse")
        finally:
            os.unlink(path)

    def test_save_and_load_local_type(self):
        handle, path = tempfile.mkstemp(suffix=".ini")
        os.close(handle)
        try:
            config = configparser.ConfigParser()
            save_tunnel_config(
                config,
                path,
                {
                    "name": "teslamate",
                    "user": "jeremy",
                    "host": "home.lan",
                    "ports": "4000:4000",
                    "tunnel_type": "local",
                    "description": "TeslaMate",
                    "auth_method": "key",
                    "auto_start": False,
                    "ssh_port": "22",
                    "identity_file": "",
                },
            )
            loaded = load_saved_tunnels(configparser.ConfigParser(), path)
            self.assertEqual(loaded["teslamate"]["tunnel_type"], "local")
            self.assertEqual(loaded["teslamate"]["ports"], "4000:4000")
        finally:
            os.unlink(path)


if __name__ == "__main__":
    unittest.main()
