FROM redis:8.4-bookworm@sha256:c22af04bb576503bf16b3e34a1fd2fd82de0f765afd866d2e380145e0af30d78
RUN install -d -m 0700 -o redis -g redis /opt/redis-runtime
COPY start-redis.sh /usr/local/bin/start-redis.sh
USER redis
WORKDIR /opt/redis-runtime
EXPOSE 6379
ENTRYPOINT ["/bin/sh", "/usr/local/bin/start-redis.sh"]
