"""Exercise the shipped image against a private, disposable S3-compatible server."""

import os
from pathlib import Path
import subprocess
import time
import uuid

ROOT = Path(__file__).resolve().parents[1]
PREFIX = f"redis-test-{uuid.uuid4()}"
NETWORK = PREFIX
STORE = PREFIX + "-s3"
SERVER = PREFIX + "-redis"
IMAGE = "soenneker-cloudflare-redis:verify"
ENV = {
    "R2_ENDPOINT": f"http://{STORE}:5000", "R2_BUCKET": "test-backups",
    "R2_ACCESS_KEY_ID": "test", "R2_SECRET_ACCESS_KEY": "test",
    "REDIS_PASSWORD": "fixture-password", "BACKUP_INTERVAL_SECONDS": "1",
}
PY = "/opt/runtime/bin/python"


def docker(*args, check=True, env=None):
    result = subprocess.run(["docker", *args], cwd=ROOT, env=env, capture_output=True, text=True)
    if check and result.returncode:
        raise RuntimeError(result.stdout + result.stderr)
    return result


def run_image(*args, options=(), check=True, env=None):
    env = ENV if env is None else env
    flags = [item for key in env for item in ("-e", key)]
    return docker("run", "--network", NETWORK, *flags, *options, IMAGE, *args,
                  check=check, env={**os.environ, **env})


def storage(code):
    preamble = "import sys;sys.path.insert(0,'/opt/service');from snapshots import Snapshots;"
    return run_image("-c", preamble + "s=Snapshots(); " + code,
                     options=("--rm", "--entrypoint", PY)).stdout.strip()


def boot(interval):
    run_image(options=("-d", "--tmpfs", "/run", "--name", SERVER, "-p", "127.0.0.1::6379"),
              env={**ENV, "BACKUP_INTERVAL_SECONDS": str(interval)})


def redis(*args, check=True):
    return docker("exec", "-e", "REDISCLI_AUTH=" + ENV["REDIS_PASSWORD"], SERVER,
                  "redis-cli", "--tls", "--cacert", "/opt/redis-runtime/tls.pem",
                  "--sni", "localhost", "-e", "--raw", *args, check=check)


def until(predicate, message, timeout=45):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if predicate():
            return
        time.sleep(0.3)
    raise AssertionError(message)


def ready():
    until(lambda: redis("PING", check=False).stdout.strip() == "PONG", "Redis did not load")


def control():
    code = ("import http.client;c=http.client.HTTPConnection('localhost',8080,timeout=60);"
            "c.request('POST','/prepare-update');print(c.getresponse().status)")
    return docker("exec", SERVER, PY, "-c", code).stdout.strip()


def remove_server():
    docker("rm", "-fv", SERVER, check=False)


def check_client(cert, *args):
    port = docker("port", SERVER, "6379/tcp").stdout.strip().split(":")[-1]
    subprocess.run(["dotnet", "run", "--file", "test/RedisClient.cs", "--", *args], cwd=ROOT, check=True,
                   env={**os.environ, "TEST_REDIS_PORT": port, "TEST_TLS_CERT": cert,
                        "TEST_REDIS_PASSWORD": ENV["REDIS_PASSWORD"]})


def stopped_with_error():
    until(lambda: docker("inspect", "-f", "{{.State.Status}}", SERVER).stdout.strip() == "exited",
          "Redis unexpectedly remained running")
    assert docker("inspect", "-f", "{{.State.ExitCode}}", SERVER).stdout.strip() != "0"


def main():
    docker("build", "-t", IMAGE, "container")
    docker("network", "create", NETWORK)
    try:
        docker("run", "-d", "--name", STORE, "--network", NETWORK, "motoserver/moto:5.1.17")
        until(lambda: docker("exec", STORE, "python", "-c",
                             "import socket; socket.create_connection(('localhost',5000),1).close()",
                             check=False).returncode == 0, "Object store did not start")
        storage("s.s3.create_bucket(Bucket=s.bucket,CreateBucketConfiguration={'LocationConstraint':'auto'})")
        pem = docker("run", "--rm", "--entrypoint", "sh", IMAGE, "-ec",
                     "openssl req -x509 -newkey rsa:2048 -nodes -days 1 -subj /CN=localhost "
                     "-addext subjectAltName=DNS:localhost -keyout /tmp/key -out /tmp/cert 2>/dev/null;"
                     "cat /tmp/cert /tmp/key").stdout
        cert, key = pem.split("-----BEGIN PRIVATE KEY-----")
        ENV.update(TLS_CERT_PEM=cert, TLS_KEY_PEM="-----BEGIN PRIVATE KEY-----" + key)

        boot(60)
        stopped_with_error()  # A missing backup never implicitly creates an empty Redis.
        remove_server()
        run_image("seed", options=("--rm",))
        assert run_image("seed", options=("--rm",), check=False).returncode != 0

        boot(1)
        ready()
        initial = storage("print(s.s3.head_object(Bucket=s.bucket,Key=s.key)['ETag'])")
        assert redis("SET", "periodic", "captured").stdout.strip() == "OK"
        assert redis("SHUTDOWN", "NOSAVE", check=False).returncode != 0
        until(lambda: storage("print(s.s3.head_object(Bucket=s.bucket,Key=s.key)['ETag'])") != initial,
              "Periodic snapshot did not reach object storage")
        # Read the source dataset after an abrupt loss on a fresh filesystem.
        docker("kill", SERVER)
        remove_server()
        boot(600)
        ready()
        assert redis("GET", "periodic").stdout.strip() == "captured"

        check_client(cert)

        # Simulate conflicting R2 ownership. Final save must fail and the old
        # Redis must reopen its local final snapshot, including recent writes.
        redis("SET", "last", "before-update")
        storage("s.s3.copy_object(Bucket=s.bucket,Key='original',CopySource={'Bucket':s.bucket,'Key':s.key});"
                "s.s3.put_object(Bucket=s.bucket,Key=s.key,Body=b'conflict')")
        assert control() == "503"
        assert redis("GET", "last").stdout.strip() == "before-update"
        redis("SET", "after-failed-update", "present")
        storage("s.s3.copy_object(Bucket=s.bucket,Key=s.key,CopySource={'Bucket':s.bucket,'Key':'original'})")
        assert control() == "200"
        assert control() == "200"  # Replay after a lost HTTP reply.
        assert redis("SET", "late", "write", check=False).returncode != 0
        remove_server()
        boot(600)
        ready()
        assert redis("GET", "last").stdout.strip() == "before-update"
        assert redis("GET", "after-failed-update").stdout.strip() == "present"
        assert redis("GET", "late").stdout.strip() == ""
        assert redis("GET", "dotnet").stdout.strip() == "persisted"
        check_client(cert, "restored")
        redis("SET", "shutdown", "graceful")
        docker("stop", "--time", "60", SERVER)
        assert docker("inspect", "-f", "{{.State.ExitCode}}", SERVER).stdout.strip() == "0"
        remove_server()
        boot(600)
        ready()
        assert redis("GET", "shutdown").stdout.strip() == "graceful"
        remove_server()

        storage("s.s3.put_object(Bucket=s.bucket,Key=s.key,Body=b'corrupt')")
        boot(60)
        stopped_with_error()
        print("Verified TLS clients, periodic save, crash restore, failed and successful updates, SIGTERM, and invalid backups.")
    except BaseException:
        for name in (STORE, SERVER):
            logs = docker("logs", "--tail", "12", name, check=False)
            print(logs.stdout, logs.stderr)
        raise
    finally:
        remove_server()
        docker("rm", "-fv", STORE, check=False)
        docker("network", "rm", NETWORK, check=False)


if __name__ == "__main__":
    main()
