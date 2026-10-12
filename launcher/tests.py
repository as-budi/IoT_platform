"""Tes supervisor runall dengan proses Python kecil sebagai pengganti layanan sungguhan."""
import io
import os
import re
import socket
import sys
import time
import unittest
from unittest import mock

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import SimpleTestCase

from launcher.management.commands import runall
from launcher.supervisor import Service, Supervisor


def alive(pid):
    """True bila proses masih berjalan (zombie = sudah mati, tinggal menunggu dibereskan)."""
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    try:
        with open(f"/proc/{pid}/stat") as stat:
            return stat.read().split(")")[-1].split()[0] != "Z"
    except FileNotFoundError:
        return True


def python(code):
    return [sys.executable, "-c", code]


class SupervisorTests(SimpleTestCase):
    def test_output_is_prefixed_and_crashes_are_restarted_then_given_up(self):
        out = io.StringIO()
        service = Service("crashy", python("print('hello'); raise SystemExit(3)"))
        supervisor = Supervisor([service], out=out, backoff_start=0.05, max_failures=2, color=False)
        self.assertEqual(supervisor.run(poll=0.02), 1)
        text = out.getvalue()
        self.assertIn("crashy | hello", text)
        self.assertEqual(service.exit_codes, [3, 3, 3])  # 1 start + 2 restart
        self.assertIn("restarting in 0.05 s", text)
        self.assertIn("restarting in 0.1 s", text)       # jeda bertambah dua kali lipat
        self.assertIn("gave up after 2 quick restarts", text)

    def test_no_restart(self):
        out = io.StringIO()
        service = Service("once", python("raise SystemExit(0)"))
        supervisor = Supervisor([service], out=out, restart=False, color=False)
        self.assertEqual(supervisor.run(poll=0.02), 1)
        self.assertEqual(service.exit_codes, [0])

    @unittest.skipUnless(os.name == "posix", "process groups are POSIX only")
    def test_stop_all_stops_children_and_grandchildren(self):
        # Layanan yang punya anak sendiri, seperti runserver dengan auto-reload.
        code = (
            "import subprocess, sys, time\n"
            "child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'])\n"
            "print(child.pid, flush=True)\n"
            "time.sleep(60)\n"
        )
        out = io.StringIO()
        service = Service("parent", python(code))
        supervisor = Supervisor([service], out=out, stop_timeout=2, color=False)
        supervisor.start(service)
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline and not re.search(r"parent \| (\d+)$", out.getvalue(), re.M):
            time.sleep(0.05)
        grandchild = int(re.search(r"parent \| (\d+)$", out.getvalue(), re.M).group(1))
        supervisor.stop_all()
        self.assertFalse(service.running)
        time.sleep(0.2)
        self.assertFalse(alive(grandchild))


class RunallCommandTests(SimpleTestCase):
    def test_unknown_service(self):
        with self.assertRaisesMessage(CommandError, "Unknown service: nope"):
            call_command("runall", only="web,nope", stdout=io.StringIO())

    def test_busy_port_is_reported(self):
        with socket.socket() as busy:
            busy.bind(("127.0.0.1", 0))
            busy.listen()
            port = busy.getsockname()[1]
            with self.assertRaisesMessage(CommandError, f"Port {port} is already in use"):
                call_command("runall", f"127.0.0.1:{port}", only="web", stdout=io.StringIO())

    def test_missing_library_is_skipped_unless_asked_for(self):
        command = runall.Command(stdout=io.StringIO())
        with mock.patch.object(runall, "installed", return_value=False):
            names = command.preflight(["mqtt", "coap"], "127.0.0.1:0", explicit=set())
            self.assertEqual(names, [])
            self.assertIn("Skipping coap: aiocoap is not installed", command.stdout._out.getvalue())
            with self.assertRaisesMessage(CommandError, "Cannot run mqtt"):
                command.preflight(["mqtt"], "127.0.0.1:0", explicit={"mqtt"})

    def test_builds_one_process_per_service(self):
        captured = {}

        def fake_run(supervisor, poll=0.2):
            captured["argv"] = [s.argv[2:] for s in supervisor.services]
            return 0

        with mock.patch.object(runall, "installed", return_value=True), \
                mock.patch.object(Supervisor, "run", fake_run), \
                mock.patch.object(runall.Command, "single_instance_lock"):
            call_command("runall", "127.0.0.1:0", skip="rules", stdout=io.StringIO())
        self.assertEqual(captured["argv"], [["runserver", "127.0.0.1:0"], ["mqtt_bridge"], ["coap_server"]])
