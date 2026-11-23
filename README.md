# Soenneker.Cloudflare.Redis

Run a single in-memory Redis cache on Cloudflare Containers. A Durable Object routes TLS connections to the same instance. Clients repopulate the cache from their source of truth after a restart, host loss, or image update. Use a dedicated connection string for rebuildable data. Queued jobs, shared encryption keys, and coordination state belong on a separate service that meets their durability requirements.

## Deploy

1. Use a Workers Paid account with Containers and a Spectrum-enabled zone that supports Worker-origin TCP. Install Docker and Node.js 24 or newer, run `npm ci`, and authenticate Wrangler to the zone's account.
2. Set the Worker secrets `REDIS_PASSWORD`, `TLS_CERT_PEM`, and `TLS_KEY_PEM` with `npx wrangler secret put <NAME>`. Supply the full certificate chain for the Redis hostname and the matching private key with their original line breaks.
3. Run `npm run deploy`. Configure a Spectrum TCP application with the Worker as origin and TLS passthrough. Redis terminates TLS on port 6379. [Spectrum setup](https://developers.cloudflare.com/spectrum/how-to/route-tcp-traffic-to-a-worker/)

## Connect

Configure the client with the Redis hostname and password. The client must trust the certificate issuer. For StackExchange.Redis, use a connection string like this one.

```text
redis.example.com:6379,ssl=true,password=<password>,abortConnect=false
```

A five-minute cron keeps the instance warm and reconciles its image. When the image changes, existing connections close and the replacement starts empty. Clients can reconnect using the same hostname. Applications should handle timeouts and fetch from their backing store while the cache is unavailable. [Cloudflare container lifecycle](https://developers.cloudflare.com/containers/concepts/architecture/)

Redis uses approximate least-recently-used eviction across all keys. The default Redis memory limit is 1,536 MiB on a 4 GiB instance. Set `REDIS_MAXMEMORY_MIB` as a Worker variable to change the limit on the next start. Redis snapshotting and append-only logging are disabled.

## Verify locally

With Docker, Python 3, OpenSSL, and the .NET 11 SDK installed, run `python test/cache.py` to check TLS access, ordinary cache operations, and cold starts after both graceful shutdown and a forced kill. Run `node --test test/image-update.test.js` to check image replacement and `npx wrangler deploy --dry-run` to validate the deployment bundle.
