# Soenneker.Cloudflare.Redis

Run a shared, in-memory Redis cache on Cloudflare Containers. The default deployment works without Spectrum. A loopback relay lets native Redis clients use the Worker's authenticated WebSocket endpoint. A Durable Object routes every connection to the same Redis instance.

## Deploy the cache

1. Enable Containers on a Workers Paid account and enable its `workers.dev` subdomain. Install Docker, Node.js 24 or newer, and the repo dependencies with `npm ci`. Authenticate Wrangler to that account.
2. Set these four Worker secrets with `npx wrangler secret put <NAME>`. Enter PEMs with their original line breaks.

   | Secret | Purpose |
   | --- | --- |
   | `REDIS_PASSWORD` | Password used by the Redis client. |
   | `TLS_CERT_PEM` | Full certificate chain Redis presents to its clients. Choose a certificate name such as `redis.internal.example.com` and trust its issuer on each client. |
   | `TLS_KEY_PEM` | Private key matching the Redis certificate. |
   | `REDIS_RELAY_TOKEN` | Independent, high-entropy bearer token shared by the Worker and authorized relays. Generate one with `openssl rand -hex 32`. |

3. Run `npm run deploy`. Take the HTTPS Worker URL printed by Wrangler, change the scheme to `wss`, and append `/redis`. For example, `wss://soenneker-cloudflare-redis.<account-subdomain>.workers.dev/redis`.

## Connect without Spectrum

Run the supplied relay in the same network namespace as each application that needs the cache. For separate API and Engine containers, run one relay alongside each process. All relays connect to the same cache. The Worker authenticates the relay token on each WebSocket upgrade. Redis authenticates the client on a separate TLS connection carried through the relay.

1. Set `REDIS_RELAY_URL` to the full `wss://…/redis` URL and inject `REDIS_RELAY_TOKEN` into the relay's environment from your secret store.
2. From the repo root, run `dotnet run --file relay/RedisRelay.cs` with the .NET 10 or newer SDK. To package it for a Linux container, run `dotnet publish relay/RedisRelay.cs -c Release -r linux-x64 --self-contained true -o ./out/relay`, copy the output into the application image, and start `RedisRelay` alongside the application. The relay listens on `127.0.0.1:6380`. Set `REDIS_RELAY_PORT` to choose another port.
3. Give StackExchange.Redis the loopback endpoint and the name on the **Redis certificate**. The example certificate name is used for TLS validation even though the TCP connection goes to loopback.

```text
127.0.0.1:6380,ssl=true,sslHost=redis.internal.example.com,password=<redis-password>,abortConnect=false,connectTimeout=30000
```

The example allows 30 seconds for a cold connection. An existing StackExchange.Redis multiplexer can reconnect through the same loopback port after a relay restart. Commands in flight during a disconnect can fail.

## Optional direct TCP access

Use Spectrum if clients need to connect directly to a public Redis hostname. Configure a Spectrum TCP application on an eligible zone with the Worker as origin and TLS passthrough. Use a certificate valid for the public Redis hostname. Direct clients can then connect with `redis.example.com:6379,ssl=true,password=<redis-password>,abortConnect=false`. For a direct-TCP-only deployment, set `workers_dev` to `false` and omit the relay token. [Cloudflare Spectrum setup](https://developers.cloudflare.com/spectrum/how-to/route-tcp-traffic-to-a-worker/)

## Cache lifecycle

A five-minute cron keeps the instance warm and checks for image changes. Restarts and image replacements disconnect clients and start with an empty dataset. Use this endpoint for data the application can rebuild on a miss. Keep queued jobs, shared encryption keys, and coordination state on a service with the durability they require. [Cloudflare container lifecycle](https://developers.cloudflare.com/containers/concepts/architecture/)

Redis evicts least-recently-used keys approximately across the whole cache. The default Redis memory limit is 1,536 MiB on a 4 GiB instance. Set the Worker variable `REDIS_MAXMEMORY_MIB` to change the limit on the next start.

## Docker Hub releases

Set the GitHub Actions repository secret `DOCKERHUB_TOKEN` to a Docker Hub access token for `soenneker` with write access to `soenneker/cloudflare-redis`. Publish a GitHub release with a Docker-compatible tag such as `v1.2.3`. After the tests pass, the workflow pushes the tested Linux AMD64 image to Docker Hub and records its immutable digest in the job summary. The current stable GitHub release also updates the `latest` tag. Pull requests and manual build checks only build and test.

```sh
docker pull soenneker/cloudflare-redis:v1.2.3
```

Cloudflare deployment is managed by the consuming account. To use a published release with Durable Object scheduling, pull the Docker Hub image by its release digest, tag it locally, and use `wrangler containers push` from the consuming account. Set the named image in that account's Wrangler configuration to its Cloudflare registry digest. The existing `npm run deploy` path also supports building directly from this repository's Dockerfile. [Cloudflare image management](https://developers.cloudflare.com/containers/guides/image-management/#use-an-external-image)

## Verify locally

With Docker, Python 3, OpenSSL, and the .NET 11 SDK installed, run `python test/cache.py` to check native Redis TLS access and cold starts after graceful shutdown and forced termination. Run `node --test test/image-update.test.js` to check image replacement and `npx wrangler deploy --dry-run` to validate the deployment bundle.
