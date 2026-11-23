import { DurableObject } from "cloudflare:workers";
import { replaceIfNeeded } from "./image-update.js";

const REDIS_PORT = 6379;
const CONTROL_PORT = 8080;
const IDLE_MS = 30 * 60 * 1000;
const socketOptions = { allowHalfOpen: true, secureTransport: "off" };

async function forward(client, upstream) {
    try {
        await upstream.opened;
        await Promise.all([
            client.readable.pipeTo(upstream.writable),
            upstream.readable.pipeTo(client.writable),
        ]);
    } finally {
        await Promise.allSettled([client.close(), upstream.close()]);
    }
}

export class RedisHost extends DurableObject {
    start() {
        const container = this.ctx.container;
        if (!container.running) {
            const env = {};
            for (const key of ["R2_ENDPOINT", "R2_BUCKET", "R2_KEY", "R2_ACCESS_KEY_ID",
                "R2_SECRET_ACCESS_KEY", "REDIS_PASSWORD", "TLS_CERT_PEM", "TLS_KEY_PEM", "BACKUP_INTERVAL_SECONDS", "REDIS_MAXMEMORY_MIB"]) {
                if (this.env[key]) env[key] = this.env[key];
            }
            container.start({ image: container.images.redis, instance: "standard-1", enableInternet: true, env });
        }
        return container.setInactivityTimeout(IDLE_MS);
    }

    async ready() {
        const port = this.ctx.container.getTcpPort(CONTROL_PORT);
        for (let attempt = 0; attempt < 240; attempt++) {
            try {
                const response = await port.fetch("http://container/ready");
                if (response.ok) return;
            } catch {
                // The control port opens only after the verified RDB has loaded.
            }
            if (!this.ctx.container.running) break;
            await scheduler.wait(500);
        }
        throw new Error("Redis did not become ready");
    }

    async connect(socket) {
        try {
            if (this.updating) await this.updating;
            await this.start();
            await this.ready();
            const upstream = this.ctx.container.getTcpPort(REDIS_PORT).connect("10.0.0.1:6379", socketOptions);
            await forward(socket, upstream);
        } catch (error) {
            await socket.close().catch(() => {});
            throw error;
        }
    }

    maintain() {
        this.updating ??= this.reconcileImage().finally(() => { this.updating = undefined; });
        return this.updating;
    }

    async reconcileImage() {
        const container = this.ctx.container;
        await replaceIfNeeded(container);
        await this.start();
        await this.ready();
    }
}

export default {
    async connect(socket, env) {
        const host = env.REDIS_HOST.getByName("primary");
        await forward(socket, host.connect("redis:6379", socketOptions));
    },
    async scheduled(_event, env) {
        await env.REDIS_HOST.getByName("primary").maintain();
    },
    fetch() {
        return new Response("Use the Redis TLS endpoint.", { status: 404 });
    },
};
