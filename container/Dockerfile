FROM redis:8.2-bookworm@sha256:b0b01167ff47bc3868e56a967895a26be4230891a1f42a78ab61cdd227accc8d
RUN apt-get update && apt-get install -y --no-install-recommends python3-venv ca-certificates \
    && rm -rf /var/lib/apt/lists/* \
    && python3 -m venv /opt/runtime \
    && /opt/runtime/bin/pip install --no-cache-dir boto3==1.44.10 \
    && mkdir -p /data /run/redis-service && chown redis:redis /data /run/redis-service
COPY supervisor.py snapshots.py /opt/service/
USER redis
WORKDIR /data
EXPOSE 6379
ENTRYPOINT ["/opt/runtime/bin/python", "-u", "/opt/service/supervisor.py"]
CMD ["serve"]
