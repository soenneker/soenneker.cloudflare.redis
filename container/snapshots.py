import base64
import hashlib
import os
import subprocess
import uuid
from contextlib import closing
from pathlib import Path

import boto3
from botocore.config import Config

DUMP = Path("/data/dump.rdb")


class Snapshots:
    def __init__(self):
        self.s3 = boto3.client(
            "s3", endpoint_url=os.environ["R2_ENDPOINT"], region_name="auto",
            aws_access_key_id=os.environ["R2_ACCESS_KEY_ID"],
            aws_secret_access_key=os.environ["R2_SECRET_ACCESS_KEY"],
            config=Config(connect_timeout=8, read_timeout=60,
                          retries={"mode": "standard", "total_max_attempts": 3},
                          request_checksum_calculation="when_required",
                          response_checksum_validation="when_required",
                          s3={"addressing_style": "path"}))
        self.bucket = os.environ["R2_BUCKET"]
        self.key = os.environ.get("R2_KEY", "redis/dump.rdb")
        self.etag = None
        self.pending_id = None

    def restore(self):
        response = self.s3.get_object(Bucket=self.bucket, Key=self.key)
        temporary = DUMP.with_suffix(".download")
        digest = hashlib.sha256()
        with closing(response["Body"]) as body, temporary.open("wb") as dest:
            for chunk in body.iter_chunks(chunk_size=1024 * 1024):
                digest.update(chunk)
                dest.write(chunk)
        if (temporary.stat().st_size != response["ContentLength"] or
                digest.hexdigest() != response["Metadata"].get("sha256")):
            raise ValueError("R2 snapshot size or SHA-256 mismatch")
        subprocess.run(["redis-check-rdb", str(temporary)], check=True, stdout=subprocess.DEVNULL)
        temporary.replace(DUMP)
        self.etag = response["ETag"]

    def upload(self):
        # R2 makes the complete single PUT visible atomically. If a response is
        # lost, the unique ID allows us to recognize our own successful write.
        if self.pending_id:
            # A response and the immediate verification can both be lost.
            # Only adopt an ETag when it belongs to our own previous PUT.
            self.confirm_pending()
        upload_id = str(uuid.uuid4())
        with DUMP.open("rb") as source:
            digest = hashlib.sha256()
            md5 = hashlib.md5(usedforsecurity=False)
            for chunk in iter(lambda: source.read(1024 * 1024), b""):
                digest.update(chunk)
                md5.update(chunk)
            condition = {"IfMatch": self.etag} if self.etag else {"IfNoneMatch": "*"}
            source.seek(0)
            self.pending_id = upload_id
            try:
                result = self.s3.put_object(Bucket=self.bucket, Key=self.key, Body=source,
                                            ContentMD5=base64.b64encode(md5.digest()).decode(),
                                            Metadata={"sha256": digest.hexdigest(), "upload-id": upload_id},
                                            **condition)
            except Exception:
                if not self.confirm_pending():
                    raise
            else:
                self.etag = result["ETag"]
                self.pending_id = None

    def confirm_pending(self):
        head = self.s3.head_object(Bucket=self.bucket, Key=self.key)
        committed = head["Metadata"].get("upload-id") == self.pending_id
        if committed:
            self.etag = head["ETag"]
        self.pending_id = None
        return committed
