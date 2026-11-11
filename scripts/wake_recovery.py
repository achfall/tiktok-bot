"""Pemulihan sleep untuk proses hidup; tidak me-restart job/claim setelah shutdown."""
import socket
import threading
import time
import os
import signal


class WakeInterrupted(Exception):
    pass


class WakeMonitor:
    # Heartbeat terpisah membedakan suspend dari langkah upload yang lama.
    def __init__(self, clock=time.time, sleep=time.sleep, gap=15, supervisor_pid=None):
        self.clock = clock
        self.sleep = sleep
        self.gap = gap
        self.last = clock()
        self.generation = 0
        self.network_generation = 0
        self.armed = None
        self.lock = threading.Lock()
        self.stopped = threading.Event()
        self.supervisor_pid = supervisor_pid

    def sample(self):
        with self.lock:
            now = self.clock()
            if now - self.last > self.gap:
                self.generation += 1
            self.last = now
            return self.generation

    def start(self):
        def heartbeat():
            while not self.stopped.wait(1):
                if not self.check_parent():
                    return
                self.sample()
        threading.Thread(target=heartbeat, daemon=True).start()

    def check_parent(self):
        if self.supervisor_pid is not None and os.getppid()!=self.supervisor_pid:
            self.stopped.set()
            print('Supervisor berhenti; menghentikan bot dengan aman.',flush=True)
            os.kill(os.getpid(),signal.SIGINT)
            return False
        return True

    def check(self):
        generation = self.sample()
        if self.armed is not None and generation != self.armed:
            raise WakeInterrupted('Mac bangun dari sleep; koneksi upload perlu diperiksa ulang.')

    def wait_until(self, deadline):
        while self.clock() < deadline:
            self.sleep(min(1, max(0, deadline - self.clock())))
            self.sample()

    def wait_for_network(self):
        print('Mac bangun; menunggu koneksi internet sebelum melanjutkan.', flush=True)
        while True:
            try:
                with socket.create_connection(('www.tiktok.com', 443), timeout=5):
                    return
            except OSError:
                self.wait_until(self.clock() + 5)

    def ensure_network_after_wake(self):
        if self.sample() != self.network_generation:
            self.wait_for_network()
            self.network_generation = self.sample()


def run_with_wake_recovery(monitor, operation, before_submit, reset_browser, max_recoveries=2):
    """Ulang hanya persiapan sebelum begin_submission; claim/caption tetap sama."""
    recoveries=0
    while True:
        monitor.ensure_network_after_wake()
        generation = monitor.sample()
        monitor.armed = generation
        submitted = False

        def submit():
            nonlocal submitted
            monitor.check()
            submitted = True
            before_submit()

        try:
            result = operation(submit)
        except Exception:
            if monitor.sample() == generation or submitted:
                raise
            result = (False, 'Persiapan terputus saat sleep.')
        finally:
            monitor.armed = None
        # Hasil terverifikasi selalu dipertahankan, termasuk saat Mac baru bangun.
        if result[0] or monitor.sample() == generation or submitted:
            return result
        if recoveries>=max_recoveries:
            return False,'Persiapan berulang kali terputus; batas pemulihan tercapai sebelum submit.'
        recoveries+=1
        monitor.ensure_network_after_wake()
        print('Melanjutkan video yang sama dari awal persiapan; belum ada submit.', flush=True)
        reset_browser()
