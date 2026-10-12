"""
Supervisor kecil: menjalankan beberapa perintah sebagai PROSES ANAK, menggabungkan
log-nya ke satu terminal, menyalakan ulang yang mati, dan mematikan semuanya
dengan rapi saat Ctrl+C.

    runall (supervisor)
      ├── web    python manage.py runserver      (HTTP + WebSocket)
      ├── mqtt   python manage.py mqtt_bridge
      ├── coap   python manage.py coap_server
      └── rules  python manage.py rule_engine

Mengapa tetap proses terpisah, bukan digabung menjadi satu program?
- Satu layanan yang macet atau crash tidak ikut menjatuhkan yang lain.
- Setiap layanan punya model kerja sendiri (Daphne/asyncio, thread paho-mqtt,
  asyncio aiocoap, loop worker) yang tidak saling menunggu.
- Sama persis dengan cara menjalankannya di server sungguhan (systemd,
  supervisord, Docker Compose), hanya "pengasuhnya" yang berbeda.

File ini tidak bergantung pada Django supaya mudah diuji dan dibaca.
"""
import os
import signal
import subprocess
import sys
import threading
import time
from dataclasses import dataclass, field

COLORS = ["36", "35", "33", "32", "34", "31"]  # cyan, magenta, kuning, hijau, biru, merah


@dataclass
class Service:
    name: str
    argv: list                      # perintah lengkap, mis. [python, manage.py, "runserver"]
    color: str = ""
    process: subprocess.Popen = None
    started_at: float = 0.0
    failures: int = 0               # crash berturut-turut yang terjadi cepat
    next_start: float = None        # waktu (monotonic) untuk start berikutnya
    gave_up: bool = False
    exit_codes: list = field(default_factory=list)

    @property
    def running(self):
        return self.process is not None and self.process.poll() is None


class Supervisor:
    def __init__(
        self,
        services,
        out=None,
        restart=True,
        backoff_start=2.0,
        backoff_max=60.0,
        max_failures=5,
        stable_after=30.0,
        stop_timeout=10.0,
        color=None,
        env=None,
    ):
        self.services = services
        self.out = out or sys.stdout
        self.restart = restart
        self.backoff_start = backoff_start
        self.backoff_max = backoff_max
        self.max_failures = max_failures      # menyerah setelah sekian crash cepat berturut-turut
        self.stable_after = stable_after      # berjalan selama ini = dianggap sehat lagi
        self.stop_timeout = stop_timeout
        self.stopping = False
        self.lock = threading.Lock()
        self.width = max(len(s.name) for s in services)
        if color is None:
            color = hasattr(self.out, "isatty") and self.out.isatty() and not os.environ.get("NO_COLOR")
        for index, service in enumerate(services):
            service.color = COLORS[index % len(COLORS)] if color else ""
        # PYTHONUNBUFFERED: anak langsung menulis setiap baris, tidak ditahan di buffer.
        self.env = {**os.environ, **(env or {}), "PYTHONUNBUFFERED": "1"}

    # --- tampilan -----------------------------------------------------------------

    def label(self, service):
        name = service.name.ljust(self.width)
        return f"\033[{service.color}m{name} |\033[0m" if service.color else f"{name} |"

    def write(self, service, text):
        with self.lock:
            prefix = self.label(service) if service else " " * self.width + " |"
            for line in text.rstrip("\n").split("\n"):
                self.out.write(f"{prefix} {line}\n")
            self.out.flush()

    def note(self, service, text):
        self.write(service, f"[runall] {text}")

    # --- proses ---------------------------------------------------------------------

    def start(self, service):
        kwargs = {}
        if os.name == "posix":
            # Grup proses sendiri: Ctrl+C di terminal tidak langsung sampai ke anak;
            # supervisor yang meneruskannya ke SELURUH grup (runserver punya anak
            # sendiri untuk auto-reload, yang juga harus ikut berhenti).
            kwargs["start_new_session"] = True
        service.process = subprocess.Popen(
            service.argv,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            stdin=subprocess.DEVNULL,
            text=True,
            bufsize=1,
            env=self.env,
            **kwargs,
        )
        service.started_at = time.monotonic()
        service.next_start = None
        threading.Thread(target=self.pump, args=(service, service.process), daemon=True).start()
        self.note(service, f"started (pid {service.process.pid})")

    def pump(self, service, process):
        """Teruskan keluaran anak, baris demi baris, dengan awalan nama layanan."""
        for line in process.stdout:
            self.write(service, line)

    def handle_exit(self, service, now):
        code = service.process.returncode
        service.exit_codes.append(code)
        service.process = None
        if self.stopping:
            return
        uptime = now - service.started_at
        if uptime >= self.stable_after:
            service.failures = 0  # sempat sehat; hitung ulang dari nol
        service.failures += 1
        if not self.restart:
            service.gave_up = True
            self.note(service, f"exited with code {code}")
            return
        if service.failures > self.max_failures:
            service.gave_up = True
            self.note(service, f"exited with code {code}; gave up after {self.max_failures} quick restarts. "
                               "Fix the error above, then restart runall.")
            return
        delay = min(self.backoff_start * 2 ** (service.failures - 1), self.backoff_max)
        service.next_start = now + delay
        self.note(service, f"exited with code {code}; restarting in {delay:g} s")

    def signal_group(self, service, sig):
        process = service.process
        if process is None or process.poll() is not None:
            return
        try:
            if os.name == "posix":
                os.killpg(process.pid, sig)
            else:  # Windows: tidak ada grup proses POSIX
                process.terminate()
        except (ProcessLookupError, PermissionError):
            pass

    def stop_all(self):
        """Ctrl+C sopan dulu (SIGINT), lalu SIGTERM, terakhir SIGKILL."""
        self.stopping = True
        steps = [(signal.SIGINT, self.stop_timeout), (signal.SIGTERM, 3.0)]
        if hasattr(signal, "SIGKILL"):
            steps.append((signal.SIGKILL, 2.0))
        for sig, wait in steps:
            alive = [s for s in self.services if s.running]
            if not alive:
                break
            for service in alive:
                self.signal_group(service, sig)
            deadline = time.monotonic() + wait
            while time.monotonic() < deadline and any(s.running for s in alive):
                time.sleep(0.1)
        for service in self.services:
            if service.process is not None:
                service.process.poll()
                service.exit_codes.append(service.process.returncode)
                service.process = None

    # --- loop utama ------------------------------------------------------------------

    def run(self, poll=0.2):
        """Jalankan sampai Ctrl+C, atau sampai semua layanan berhenti/menyerah."""
        previous = signal.getsignal(signal.SIGTERM)

        def on_sigterm(signum, frame):
            raise KeyboardInterrupt

        if threading.current_thread() is threading.main_thread():
            signal.signal(signal.SIGTERM, on_sigterm)
        try:
            for service in self.services:
                self.start(service)
            while True:
                now = time.monotonic()
                for service in self.services:
                    if service.process is not None and service.process.poll() is not None:
                        self.handle_exit(service, now)
                    if service.next_start is not None and now >= service.next_start:
                        self.start(service)
                if all(s.gave_up for s in self.services):
                    self.note(None, "every service has stopped.")
                    return 1
                time.sleep(poll)
        except KeyboardInterrupt:
            self.note(None, "stopping all services…")
            self.stop_all()
            self.note(None, "stopped.")
            return 0
        finally:
            if threading.current_thread() is threading.main_thread():
                signal.signal(signal.SIGTERM, previous)
