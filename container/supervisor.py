import hashlib
import http.server
import os
from pathlib import Path
import secrets
import shutil
import signal
import subprocess
import sys
import threading
import time

from snapshots import DUMP, Snapshots

RUNTIME = Path("/run/redis-service")
SOCKET = str(RUNTIME / "redis.sock")
stop = threading.Event()


class RedisService:
    def __init__(self, seed=False):
        self.snapshots = Snapshots()
        self.interval = int(os.environ.get("BACKUP_INTERVAL_SECONDS", "60"))
        if self.interval < 1:
            raise ValueError("BACKUP_INTERVAL_SECONDS must be positive")
        self.lock = threading.Lock()
        self.process = None
        self.ready = False
        self.sealed = False
        self.final_file_ready = False
        self.upload_pending = False
        self.admin = secrets.token_hex(32)
        config = [
            "dir /data", "dbfilename dump.rdb", 'save ""', "appendonly no",
            "port 0", "daemonize no", "maxmemory-policy noeviction",
            f"maxmemory {int(os.environ.get('REDIS_MAXMEMORY_MIB', '1536'))}mb",
            f"unixsocket {SOCKET}", "unixsocketperm 600",
            "user supervisor on #" + self.hash(self.admin) + " ~* &* +@all",
        ]
        if seed:
            config.append("user default off")
        else:
            password = os.environ["REDIS_PASSWORD"]
            if not password:
                raise ValueError("A Redis password is required")
            (RUNTIME / "tls.pem").write_text(os.environ["TLS_CERT_PEM"])
            (RUNTIME / "tls.key").write_text(os.environ["TLS_KEY_PEM"])
            config.extend([
                "bind 0.0.0.0", "tls-port 6379", "tls-auth-clients no",
                f"tls-cert-file {RUNTIME}/tls.pem", f"tls-key-file {RUNTIME}/tls.key",
                # The app can use scripts, streams, pub/sub, and normal Redis data commands.
                "user default reset on #" + self.hash(password) +
                " ~* &* +@all -@admin -@dangerous +info +client|setname +client|setinfo"
                " +client|id +client|list +cluster|nodes +cluster|slots +keys +scan +eval +evalsha +script|load",
            ])
        self.config = RUNTIME / "redis.conf"
        self.config.write_text("\n".join(config) + "\n")

    @staticmethod
    def hash(value):
        return hashlib.sha256(value.encode()).hexdigest()

    def command(self, *args):
        return subprocess.check_output(
            ["redis-cli", "-e", "--raw", "-s", SOCKET, "--user", "supervisor", *args],
            env={"PATH": os.environ["PATH"], "REDISCLI_AUTH": self.admin},
            text=True).strip()

    def start(self):
        self.process = subprocess.Popen(["redis-server", str(self.config)], env={"PATH": os.environ["PATH"]})
        while self.process.poll() is None:
            if Path(SOCKET).exists():
                try:
                    if self.command("PING") == "PONG":
                        return
                except subprocess.CalledProcessError:
                    pass  # Redis can still be loading the RDB.
            time.sleep(0.2)
        raise RuntimeError(f"Redis failed to start (exit {self.process.returncode})")

    def save_and_stop(self):
        self.command("SHUTDOWN", "SAVE")
        if self.process.wait(timeout=30) != 0:
            raise RuntimeError("Redis exited unsuccessfully during final save")

    def persistence_info(self):
        return dict(line.split(":", 1) for line in self.command("INFO", "persistence").splitlines()
                    if ":" in line)

    def periodic_backup(self):
        with self.lock:
            if self.sealed or stop.is_set() or not self.ready:
                return
            if not self.upload_pending and self.persistence_info()["rdb_changes_since_last_save"] == "0":
                return
            self.upload_pending = True
            self.command("BGSAVE")
            while True:
                info = self.persistence_info()
                if info["rdb_bgsave_in_progress"] == "0":
                    if info["rdb_last_bgsave_status"] != "ok":
                        raise RuntimeError("Redis background save failed")
                    break
                time.sleep(0.2)
            self.snapshots.upload()
            self.upload_pending = False
            print("Periodic R2 snapshot committed", flush=True)

    def final_backup(self, resume_on_failure):
        with self.lock:
            if self.sealed:
                return
            self.ready = False
            try:
                if self.process.poll() is None:
                    self.save_and_stop()
                elif not self.final_file_ready:
                    raise RuntimeError("Redis exited before a final save")
                self.final_file_ready = True
                self.snapshots.upload()
            except Exception:
                if resume_on_failure and not stop.is_set():
                    if self.process.poll() is not None and self.final_file_ready:
                        self.start()
                        self.final_file_ready = False
                        self.upload_pending = True
                    self.ready = self.process.poll() is None
                raise
            self.sealed = True
            print("Final R2 snapshot committed", flush=True)

    def serve(self):
        self.snapshots.restore()
        self.start()
        self.ready = True
        service = self

        class Control(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                if self.path != "/ready":
                    self.reply(404)
                elif service.sealed:
                    self.reply(409)
                else:
                    self.reply(200 if service.ready else 503)

            def reply(self, status):
                self.send_response(status)
                self.send_header("Content-Length", "0")
                self.end_headers()

            def do_POST(self):
                if self.path != "/prepare-update":
                    self.reply(404)
                    return
                try:
                    service.final_backup(resume_on_failure=True)
                except Exception as error:
                    print(f"Image update deferred: {error}", file=sys.stderr)
                    self.reply(503)
                    return
                self.reply(200)

            def log_message(self, *args):
                pass

        server = http.server.ThreadingHTTPServer(("0.0.0.0", 8080), Control)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        next_save = time.monotonic() + self.interval
        while not stop.wait(0.5):
            with self.lock:
                if not self.sealed and self.process.poll() is not None:
                    raise RuntimeError("Redis exited unexpectedly")
            if time.monotonic() >= next_save:
                try:
                    self.periodic_backup()
                except Exception as error:
                    print(f"Periodic R2 backup failed: {error}", file=sys.stderr)
                next_save = time.monotonic() + self.interval
        # On platform SIGTERM keep retrying the same final file until the host's deadline.
        while True:
            try:
                self.final_backup(resume_on_failure=False)
                break
            except Exception as error:
                print(f"Retrying final backup: {error}", file=sys.stderr)
                time.sleep(2)
        server.server_close()


if __name__ == "__main__":
    os.umask(0o077)
    signal.signal(signal.SIGTERM, lambda *_: stop.set())
    signal.signal(signal.SIGINT, lambda *_: stop.set())
    mode = sys.argv[1]
    service = RedisService(seed=mode in ("seed", "import"))
    if mode in ("seed", "import"):
        if DUMP.exists():
            raise RuntimeError("Seed requires an empty local data directory")
        if mode == "import":
            subprocess.run(["redis-check-rdb", sys.argv[2]], check=True)
            shutil.copyfile(sys.argv[2], DUMP)
        service.start()
        service.save_and_stop()
        service.snapshots.upload()
        print("Created initial R2 snapshot")
    elif mode == "serve":
        service.serve()
    else:
        raise SystemExit("Expected serve, seed, or import <RDB file>")
