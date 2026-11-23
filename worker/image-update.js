// A sealed instance has already uploaded its final RDB and cannot accept writes.
export async function replaceIfNeeded(container) {
    if (!container.running) return;
    const current = await container.inspect();
    if (!current?.image) return;

    const control = container.getTcpPort(8080);
    if (current.image === container.images.redis) {
        // A rollout may be rolled back after sealing but before destruction.
        const status = await control.fetch("http://container/ready");
        if (status.status !== 409) return;
    }

    const result = await control.fetch("http://container/prepare-update", { method: "POST" });
    if (!result.ok) throw new Error("Redis deferred the image update");
    await container.destroy();
}
