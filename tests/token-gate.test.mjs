// 前端令牌闸单元测试：过期请求不得覆盖新草稿。
// 运行：node --test tests/
import test from "node:test";
import assert from "node:assert/strict";
import { createTokenGate } from "../app/static/token-gate.js";

test("新草稿的响应有效、旧草稿的响应过期", () => {
  const gate = createTokenGate();
  const t1 = gate.issue();
  const t2 = gate.issue(); // 用户又提交了新草稿
  assert.equal(gate.isCurrent(t1), false, "旧请求必须失效");
  assert.equal(gate.isCurrent(t2), true, "最新请求有效");
});

test("invalidate 后全部在途响应作废", () => {
  const gate = createTokenGate();
  const t = gate.issue();
  gate.invalidate();
  assert.equal(gate.isCurrent(t), false);
  const t2 = gate.issue();
  assert.equal(gate.isCurrent(t2), true);
});

test("模拟乱序返回：先返回的新结果与后返回的旧请求", async () => {
  const gate = createTokenGate();
  let rendered = null;

  function submit(label, delayMs) {
    const token = gate.issue();
    return new Promise((resolve) => {
      setTimeout(() => {
        if (gate.isCurrent(token)) rendered = label; // 只有未过期才允许渲染
        resolve();
      }, delayMs);
    });
  }

  // 旧请求慢、新请求快
  await Promise.all([submit("旧结论", 30), submit("新结论", 5)]);
  assert.equal(rendered, "新结论", "过期的慢响应不得覆盖新草稿结论");
});

test("令牌严格单调递增", () => {
  const gate = createTokenGate();
  const seq = [gate.issue(), gate.issue(), gate.issue()];
  assert.deepEqual(seq, [1, 2, 3]);
});
