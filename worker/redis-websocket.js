const encoder = new TextEncoder();
const maxQueuedBytes = 4 * 1024 * 1024;

export async function upgradeRedis(request, env, ctx) {
    const url = new URL(request.url);
    if (url.pathname !== "/redis" || !env.REDIS_RELAY_TOKEN) return new Response(null, { status: 404 });
    if (url.protocol !== "https:") return new Response(null, { status: 400 });
    const supplied = encoder.encode(request.headers.get("Authorization") ?? "");
    const expected = encoder.encode(`Bearer ${env.REDIS_RELAY_TOKEN}`);
    if (supplied.length !== expected.length || !crypto.subtle.timingSafeEqual(supplied, expected)) {
        return new Response(null, { status: 401, headers: { "WWW-Authenticate": "Bearer" } });
    }
    if (request.method !== "GET" || request.headers.get("Upgrade")?.toLowerCase() !== "websocket") {
        return new Response(null, { status: 426, headers: { Upgrade: "websocket" } });
    }

    const host = env.REDIS_HOST.getByName("primary");
    const socket = host.connect("redis:6379", { allowHalfOpen: true, secureTransport: "off" });
    try {
        await socket.opened;
    } catch {
        await socket.close().catch(() => {});
        return new Response(null, { status: 503 });
    }

    const [client, server] = Object.values(new WebSocketPair());
    server.binaryType = "arraybuffer";
    server.accept();
    const writer = socket.writable.getWriter();
    let queued = 0;
    let closed = false;
    function close(code, reason) {
        if (closed) return;
        closed = true;
        try {
            if (server.readyState === WebSocket.OPEN) server.close(code, reason);
        } finally {
            ctx.waitUntil(socket.close().catch(() => {}));
        }
    }

    server.addEventListener("message", ({ data }) => {
        if (closed) return;
        if (!(data instanceof ArrayBuffer) || !data.byteLength) {
            close(1003, "Expected binary data");
            return;
        }
        queued += data.byteLength;
        if (queued > maxQueuedBytes) {
            close(1009, "Write queue full");
            return;
        }
        // The stream writer serializes writes in arrival order.
        writer.write(new Uint8Array(data)).then(
            () => { queued -= data.byteLength; },
            () => close(1011, "Redis connection closed"),
        );
    });
    server.addEventListener("close", () => close(1000, "Client disconnected"));
    server.addEventListener("error", () => close(1011, "Client connection failed"));
    ctx.waitUntil((async () => {
        try {
            for await (const bytes of socket.readable) server.send(bytes);
            close(1000, "Redis disconnected");
        } catch {
            close(1011, "Redis connection closed");
        }
    })());
    return new Response(null, { status: 101, webSocket: client });
}
