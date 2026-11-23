import { DurableObject } from "cloudflare:workers";
import { replaceIfNeeded } from "./image-update.js";
import { upgradeRedis } from "./redis-websocket.js";

const REDIS_PORT = 6379;
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
            for (const key of ["REDIS_PASSWORD", "TLS_CERT_PEM", "TLS_KEY_PEM", "REDIS_MAXMEMORY_MIB"]) {
                if (this.env[key]) env[key] = this.env[key];
            }
            container.start({ image: container.images.redis, instance: "standard-1", enableInternet: false, env });
        }
        return container.setInactivityTimeout(IDLE_MS);
    }

    async openRedisSocket() {
        const container = this.ctx.container;
        const port = container.getTcpPort(REDIS_PORT);
        let lastError;
        for (let attempt = 0; attempt < 240 && container.running; attempt++) {
            let upstream;
            try {
                upstream = port.connect("10.0.0.1:6379", socketOptions);
                await upstream.opened;
                return upstream;
            } catch (error) {
                lastError = error;
                if (upstream) await upstream.close().catch(() => {});
                await scheduler.wait(500);
            }
        }
        throw new Error("Redis did not accept a connection", { cause: lastError });
    }

    async connect(socket) {
        try {
            if (this.updating) await this.updating;
            await this.start();
            const upstream = await this.openRedisSocket();
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
    fetch: upgradeRedis,
};
