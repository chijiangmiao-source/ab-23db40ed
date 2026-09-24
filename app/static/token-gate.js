// 单调请求令牌闸：每个新草稿提交领取新令牌；
// 仅当响应抵达时仍是最新令牌才允许渲染，过期响应一律丢弃，
// 从根本上杜绝慢返回的旧请求覆盖新草稿结论。
export function createTokenGate() {
  let latest = 0;
  return {
    /** 产生并登记一个新令牌（草稿提交时调用）。 */
    issue() {
      latest += 1;
      return latest;
    },
    /** 该令牌对应的响应是否仍然有效。 */
    isCurrent(token) {
      return token === latest;
    },
    /** 作废全部在途响应（草稿再次编辑/新提交时调用）。 */
    invalidate() {
      latest += 1;
    },
    get current() {
      return latest;
    },
  };
}
