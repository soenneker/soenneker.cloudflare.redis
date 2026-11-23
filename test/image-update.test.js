import assert from "node:assert/strict";
import { test } from "node:test";
import { replaceIfNeeded } from "../worker/image-update.js";

test("only a running container with a superseded image is replaced", async () => {
    for (const [running, image, replacements] of [
        [false, "old", 0], [true, "current", 0], [true, undefined, 0], [true, "old", 1],
    ]) {
        let destroyed = 0;
        await replaceIfNeeded({
            running,
            images: { redis: "current" },
            async inspect() { assert.ok(running); return { image }; },
            async destroy() { destroyed++; },
        });
        assert.equal(destroyed, replacements);
    }
});
