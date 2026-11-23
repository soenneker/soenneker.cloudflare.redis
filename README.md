# Soenneker.Cloudflare.Redis

Run a single Redis 8.2 primary on Cloudflare Containers. API and background services use ordinary Redis commands over TLS. A Durable Object routes every connection to the same primary. Redis runs in memory and periodically saves its dataset to a private R2 bucket.

## Deploy

Use a Workers Paid account with Containers and a Spectrum-enabled zone that supports Worker-origin TCP applications. Install Docker, Node.js 24 or newer, and the dependencies with `npm ci`. Authenticate Wrangler to the account that owns the zone.

1. Create a private R2 bucket and bucket-scoped S3 credentials with object read and write access. Set these variables for the container seed command below and for the Worker. Use the R2 S3 endpoint directly.

   | Variable | Value |
   | --- | --- |
   | `R2_ENDPOINT` | `https://<account-id>.r2.cloudflarestorage.com` or the bucket's jurisdiction-specific S3 endpoint |
   | `R2_BUCKET` | Backup bucket name |
   | `R2_ACCESS_KEY_ID`, `R2_SECRET_ACCESS_KEY` | Bucket-scoped credentials |
   | `R2_KEY` | Snapshot object key, defaults to `redis/dump.rdb` |

2. Build the image and initialize the backup **once**. The following command creates an empty RDB only if the object is absent. Supply the four required variables above in a local, git-ignored `.env.r2` file. Include `R2_KEY` if using a different object key.

   ```sh
   docker build -t cloudflare-redis ./container
   docker run --rm --env-file .env.r2 cloudflare-redis seed
   ```

   For migration, use `import` **instead of** `seed`. Mount a compatible, standalone source RDB read-only. Import validates the file, loads it with this Redis version, and creates the initial R2 object. For a cutover, pause all source writers (including job workers) before taking the final export. Use a dedicated bucket or object key per deployment and keep the source available until the new endpoint is verified.

   ```sh
   docker run --rm --env-file .env.r2 -v "/absolute/path/dump.rdb:/seed.rdb:ro" cloudflare-redis import /seed.rdb
   ```

3. Put `R2_ENDPOINT` and `R2_BUCKET` in the Wrangler `vars` section. Set `R2_ACCESS_KEY_ID`, `R2_SECRET_ACCESS_KEY`, and `REDIS_PASSWORD` as Wrangler secrets. Set `TLS_CERT_PEM` to the full certificate chain for the Redis hostname and `TLS_KEY_PEM` to the matching private key. Clients must trust the issuer. Supply the PEM contents with their original line breaks. Configure secrets with `npx wrangler secret put <NAME>` or the Cloudflare dashboard. Keep the secrets and RDB out of Git.

4. Run `npm run deploy`. Configure a Spectrum TCP application for the Redis hostname and port with the deployed Worker as origin and TLS passthrough. The container terminates TLS. The private container control port (8080) is used only by the Durable Object. A five-minute cron keeps the primary active and checks for image changes. [Spectrum setup](https://developers.cloudflare.com/spectrum/how-to/route-tcp-traffic-to-a-worker/) · [Cloudflare image management](https://developers.cloudflare.com/containers/guides/image-management/)

## Connect the API and Engine

Use the same value for `Azure:Redis:ConnectionString` on both services. Supply the existing configuration key through your secret store. For example, with Spectrum listening on 6379 and a generated hexadecimal password:

```text
redis.example.com:6379,ssl=true,password=<password>,abortConnect=false
```

The endpoint supports StackExchange.Redis, Lua, TTLs, transactions, locks, lists, hashes, streams and pub/sub. The application user has data access. Server configuration, ACL management and shutdown are reserved for the local supervisor. A planned replacement disconnects clients and briefly interrupts requests. StackExchange.Redis reconnects using the same hostname. An in-flight command can still time out.

## Persistence and updates

- **Normal operation.** After a change, the supervisor runs `BGSAVE` on its next backup interval and waits for completion. It uploads the complete file to the configured R2 object with a checksum and a conditional write against the last observed ETag. A failed upload leaves the previous committed snapshot available. The next interval retries. The default interval is 60 seconds after the previous attempt completes. This is a single latest recovery point. Use a separate archival policy if you need historical backups.
- **Startup.** The supervisor downloads the object, verifies its size and SHA-256, checks the RDB, then loads Redis. A missing, unreadable, or invalid backup prevents startup. Creating an empty database takes the explicit seed command.
- **Image updates.** On the next cron tick after deploying a changed Redis image, the Durable Object asks the running container to prepare. Redis processes `SHUTDOWN SAVE`, closing client connections and producing a final dump. The old container uploads it and stays closed to writes. Only after it confirms the upload does the Durable Object replace it. If the upload fails, the old instance reloads its final local dump and resumes service. The cron retries later. A compatible new image restores the confirmed dump before serving clients.
- **Host stops and crashes.** On `SIGTERM`, the supervisor attempts a final save and retries the upload until the platform stops it. After a forced kill or machine loss, Redis recovers to the last successful R2 snapshot. More recent writes can disappear and completed jobs can reappear. This single-primary design provides neither synchronous write durability nor automatic replica failover. [Cloudflare lifecycle](https://developers.cloudflare.com/containers/concepts/architecture/)
- **Sizing.** The default instance has 4 GiB of RAM. Redis limits data memory to 1,536 MiB with `noeviction`, leaving room for Redis overhead and copy-on-write during snapshots. Adjust `REDIS_MAXMEMORY_MIB`, `BACKUP_INTERVAL_SECONDS`, and the Worker instance size together. Measure upload, restore and reconnect times with your dataset. Each backup uses one R2 PUT and must fit the single-upload size limit. A configuration-only change takes effect the next time the container starts. [R2 limits](https://developers.cloudflare.com/r2/platform/limits/)

## Verify locally

With Docker, Python 3, and the .NET 11 SDK available, run `python test/persistence.py`. It creates a temporary S3-compatible server, Redis containers with fresh filesystems, and a local TLS certificate. It exercises the same StackExchange.Redis, Flywheel and data-protection libraries used by the API and Engine, plus recovery after a crash, failed and successful updates, and graceful shutdown. Run `npx wrangler deploy --dry-run` to validate the Cloudflare bundle and Docker build.
