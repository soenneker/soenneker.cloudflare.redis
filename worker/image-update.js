export async function replaceIfNeeded(container) {
    if (!container.running) return;
    const current = await container.inspect();
    if (current?.image && current.image !== container.images.redis) {
        await container.destroy();
    }
}
