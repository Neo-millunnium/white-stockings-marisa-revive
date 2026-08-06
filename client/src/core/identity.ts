// 匿名身份层:localStorage 里的 UUID,作为"按用户维度记录状态"的主标识(uid)。
//
// 设计要点(见 IMPLEMENTATION_PLAN 第 0 节):
// - uid 是客户端自报的匿名身份,只用于"区分用户",不当作可信权限;
//   调教师等权限判定只信服务端 env 配置(MASTER_UID),绝不信任客户端自报身份。
// - 后端所有按用户状态(好感 / 话题上下文 / flag 定向 / 教学留痕)以 uid 为主键、ip 为兜底;
//   老客户端不传 uid 时,后端按 ip 兜底照常工作。
// - 零新依赖:localStorage + crypto.randomUUID 均为主流浏览器原生能力。

const UID_KEY = 'marisa_uid'

/**
 * 读取(必要时生成)当前用户的匿名 uid。
 * localStorage 不可用(隐私模式/被禁用)时返回空串,此时后端按 ip 兜底。
 */
export function getUid(): string {
  try {
    let uid = localStorage.getItem(UID_KEY)
    if (!uid) {
      uid = crypto.randomUUID()
      localStorage.setItem(UID_KEY, uid)
    }
    return uid
  } catch {
    return ''
  }
}
