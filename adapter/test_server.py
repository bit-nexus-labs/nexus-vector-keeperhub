import unittest
from unittest.mock import patch

from adapter import server


class AdapterPolicyTests(unittest.TestCase):
    def test_loopback_only(self):
        self.assertEqual(server.HOST, "127.0.0.1")

    def test_static_allowlist_is_explicit(self):
        self.assertEqual(set(server.STATIC_FILES), {"/api-client.js", "/hackathon-demo.html"})

    def test_broadcast_always_has_testnet_gate(self):
        command = server._runner_command("broadcast", "anna", "BROADCAST-ANNA-test")
        self.assertIn("-ApproveTestnetWrite", command)
        self.assertIn("-Effect", command)
        self.assertIn("anna", command)

    def test_mutating_actions_require_effect(self):
        with self.assertRaises(ValueError):
            server._runner_command("broadcast", None, "BROADCAST-x")

    def test_simulate_requires_approval(self):
        with self.assertRaises(ValueError):
            server._runner_command("simulate", "anna", None)

    def test_run_ref_is_constrained(self):
        with self.assertRaises(ValueError):
            server._validate_run_ref("../../main")
        self.assertEqual(server._validate_run_ref("anna-mark-leo-video-20260809-v1"), "anna-mark-leo-video-20260809-v1")

    @patch.object(server.subprocess, "run")
    def test_timeout_is_ambiguous_and_must_not_be_retried(self, run):
        run.side_effect = server.subprocess.TimeoutExpired(cmd=["powershell.exe"], timeout=server.COMMAND_TIMEOUT_SECONDS)
        status, payload = server._run_command("verify", "anna", None)
        self.assertEqual(status, 504)
        self.assertEqual(payload["outcome"], "UNKNOWN")
        self.assertIn("Do not retry", payload["note"])

    def test_runner_uses_shell_false(self):
        fake = type("Proc", (), {"returncode": 0, "stdout": '{"status":"PASS"}\n', "stderr": ""})()
        with patch.object(server.subprocess, "run", return_value=fake) as run:
            status, payload = server._run_command("status", None, None)
        self.assertEqual(status, 200)
        self.assertEqual(payload["status"], "PASS")
        self.assertFalse(run.call_args.kwargs["shell"])


if __name__ == "__main__":
    unittest.main()
