"""Exercise the cache image with a native TLS client and an empty runtime mount."""

import os
from pathlib import Path
import subprocess
from tempfile import TemporaryDirectory
import time
import uuid

ROOT = Path(__file__).resolve().parents[1]
IMAGE = "soenneker-cloudflare-redis:cache-verify"
SERVER = f"redis-cache-test-{uuid.uuid4()}"
PASSWORD = 'cache fixture "with" \\ punctuation!'


def docker(*args, env=None, check=True):
    result = subprocess.run(["docker", *args], cwd=ROOT, env=env, capture_output=True, text=True)
    if check and result.returncode:
        raise RuntimeError(result.stdout + result.stderr)
    return result


def redis(*args, check=True):
    return docker("exec", "-e", "REDISCLI_AUTH", SERVER, "redis-cli", "--tls",
                  "--cacert", "/opt/redis-runtime/tls.pem", "--sni", "localhost", "-e", "--raw",
                  *args, check=check, env={**os.environ, "REDISCLI_AUTH": PASSWORD})


def wait_ready():
    end = time.monotonic() + 30
    while time.monotonic() < end:
        if redis("PING", check=False).stdout.strip() == "PONG":
            return
        time.sleep(0.2)
    raise RuntimeError("Redis never answered PING")


def main():
    docker("build", "-t", IMAGE, "container")
    with TemporaryDirectory() as temp:
        cert, key = Path(temp) / "cert.pem", Path(temp) / "key.pem"
        subprocess.run(["openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-days", "1",
                        "-subj", "/CN=localhost", "-addext", "subjectAltName=DNS:localhost",
                        "-out", str(cert), "-keyout", str(key)], capture_output=True, check=True)
        env = {**os.environ, "TLS_CERT_PEM": cert.read_text(), "TLS_KEY_PEM": key.read_text(),
               "REDIS_PASSWORD": PASSWORD}
        try:
            docker("run", "-d", "--name", SERVER, "-p", "127.0.0.1::6379", "--tmpfs", "/run",
                   "-e", "REDIS_PASSWORD", "-e", "TLS_CERT_PEM", "-e", "TLS_KEY_PEM", IMAGE, env=env)
            wait_ready()
            port = docker("port", SERVER, "6379/tcp").stdout.strip().split(":")[-1]
            subprocess.run(["dotnet", "run", "--file", "test/RedisClient.cs"], cwd=ROOT, check=True,
                           env={**os.environ, "TEST_REDIS_PORT": port, "TEST_TLS_CERT": env["TLS_CERT_PEM"],
                                "TEST_REDIS_PASSWORD": PASSWORD})
            for command in ("SAVE", "BGSAVE"):
                result = redis(command, check=False)
                assert result.returncode and "NOPERM" in result.stdout + result.stderr

            for stop in ("stop", "kill"):
                assert redis("SET", "before-restart", "present").stdout.strip() == "OK"
                docker(stop, SERVER)
                # Reuse the same writable filesystem to catch accidental local persistence.
                docker("start", SERVER)
                wait_ready()
                assert redis("DBSIZE").stdout.strip() == "0"
            print("Verified TLS cache operations and empty restarts after SIGTERM and SIGKILL.")
        finally:
            docker("rm", "-fv", SERVER, check=False)


if __name__ == "__main__":
    main()
