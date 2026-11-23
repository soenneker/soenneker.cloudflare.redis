FROM redis:8.4-bookworm@sha256:c22af04bb576503bf16b3e34a1fd2fd82de0f765afd866d2e380145e0af30d78
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
