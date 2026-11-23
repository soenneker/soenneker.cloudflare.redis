import assert from "node:assert/strict";
import { test } from "node:test";
import { replaceIfNeeded } from "../worker/image-update.js";

function container(current, desired, responses) {
    const calls = [];
    return {
        calls, running: true, images: { redis: desired },
        async inspect() { return { image: current }; },
        getTcpPort(port) {
            assert.equal(port, 8080);
            return { async fetch(url, options) {
                calls.push([new URL(url).pathname, options?.method ?? "GET"]);
                const status = responses.shift();
                if (status instanceof Error) throw status;
                return new Response(null, { status });
            } };
        },
        async destroy() { calls.push("destroy"); },
    };
}

test("replace only after a confirmed final upload", async () => {
    const old = container("old", "new", [200]);
    await replaceIfNeeded(old);
    assert.deepEqual(old.calls, [["/prepare-update", "POST"], "destroy"]);
});

test("retain old container after an upload failure or lost response", async () => {
    for (const failure of [503, new Error("disconnected")]) {
        const old = container("old", "new", [failure]);
        await assert.rejects(replaceIfNeeded(old));
        assert.deepEqual(old.calls, [["/prepare-update", "POST"]]);
    }
});

test("retain healthy current image and recover a sealed rollback", async () => {
    const current = container("same", "same", [200]);
    await replaceIfNeeded(current);
    assert.deepEqual(current.calls, [["/ready", "GET"]]);
    const sealed = container("same", "same", [409, 200]);
    await replaceIfNeeded(sealed);
    assert.deepEqual(sealed.calls, [["/ready", "GET"], ["/prepare-update", "POST"], "destroy"]);
});
