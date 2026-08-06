// 魔理沙核心逻辑:说话格式、回复、教学、忘记、状态。
import { api } from '../api'
import { getUid } from './identity'

/** 一条聊天记录 */
export interface TalkItem {
  name: string
  content: string
}

/**
 * 获取教学时上报的 ip 参数。
 * 优先尝试获取外网 IP(与旧版行为一致),失败则回退到本机地址,避免断网时教学不可用。
 */
async function getIp(): Promise<string> {
  try {
    const controller = new AbortController()
    const timer = setTimeout(() => controller.abort(), 3000)
    const resp = await fetch('https://ipv4.icanhazip.com/', {
      signal: controller.signal,
    })
    clearTimeout(timer)
    const text = (await resp.text()).replace(/\s+/g, '')
    if (text) {
      return text
    }
  } catch {
    // 网络不可用时忽略,走回退地址
  }
  return '127.0.0.1'
}

export const Core = {
  /** 生成一条聊天记录(You 或 白絲魔理沙 的发言) */
  speak(name: string, content: string): TalkItem {
    return { name, content }
  },

  /**
   * 回复:命中返回魔理沙的回答;未命中(业务码 10001)或出错返回 undefined,
   * 由界面显示兜底话术(与旧版前端行为一致)。
   */
  async reply(content: string): Promise<string | undefined> {
    try {
      const ip = await getIp()
      const res = await api.reply({ ip, keyword: content, uid: getUid() })
      if (res.code === 200) {
        return res.data?.answer
      }
      return undefined
    } catch (err) {
      console.log(`回复失败 ... ${err}`)
      return undefined
    }
  },

  /**
   * 教学:content 形如 "关键词`回答"(反引号分隔,产品逻辑必须保留),
   * 也支持三段反引号 "关键词`回答`flag"(P4 对象判断,如 @user:<uid> / @time:night,两段兼容)。
   * category 为教学分类(word/sentence/syntax/logic/greeting/auto,默认 auto)。返回是否学习成功。
   */
  async teach(content: string, category = 'auto'): Promise<boolean> {
    const parts = content.split('`')
    const keyword = parts[0] || ''
    const answer = parts[1] || ''
    const flag = parts[2] ? parts[2].trim() : 'all'
    const ip = await getIp()
    try {
      const res = await api.add({ ip, keyword, answer, category, uid: getUid(), flag })
      return res.code === 200
    } catch (err) {
      console.log(`无法学习 ... ${err}`)
      return false
    }
  },

  /**
   * 忘记:根据当前对话列表取"最后一次让魔理沙说出的回答"作为删除依据。
   * 逻辑与旧版一致:输入 forget 之前的那一条记录就是 last answer。
   */
  async forget(list: TalkItem[]): Promise<boolean> {
    const len = list.length
    const answer = len > 3 ? list[len - 2].content : list[1]?.content
    try {
      const res = await api.forget({ answer, uid: getUid() })
      return res.code === 200 && res.data === 'success'
    } catch (err) {
      console.log(`无法忘记 ... ${err}`)
      return false
    }
  },

  /** 状态:返回当前知识条数,失败返回 0 */
  async status(): Promise<number> {
    try {
      const res = await api.status()
      return typeof res.data === 'number' ? res.data : 0
    } catch (err) {
      console.log(`重量获取 ... ${err}`)
      return 0
    }
  },

  /** 分类统计:返回 {word, sentence, syntax, logic, greeting, unclassified},失败返回 null */
  async categories(): Promise<Record<string, number> | null> {
    try {
      const res = await api.categories()
      return res.code === 200 && res.data ? res.data : null
    } catch (err) {
      console.log(`分类统计 ... ${err}`)
      return null
    }
  },

  /**
   * 开场白:随机返回一条 greeting 分类记忆(用户访问网站时自动打招呼)。
   * 无 greeting 词条或失败返回 null,前端静默跳过。
   */
  async greeting(): Promise<{ keyword: string; answer: string } | null> {
    try {
      const res = await api.greeting()
      return res.code === 200 && res.data ? res.data : null
    } catch (err) {
      console.log(`开场白 ... ${err}`)
      return null
    }
  },

  /**
   * 提示线索:随机返回一条已审核通过的记忆(关键词 -> 回答)。
   * 对应界面的 hint 指令(查看其他人自定义的内容提示或小小线索)。
   * 失败返回 null。
   */
  async hint(): Promise<{ keyword: string; answer: string } | null> {
    try {
      const res = await api.hint()
      if (res.code === 200 && res.data?.answer) {
        return { keyword: res.data.keyword || '', answer: res.data.answer }
      }
      return null
    } catch (err) {
      console.log(`提示获取失败 ... ${err}`)
      return null
    }
  },

  /**
   * 待学习清单:返回被问过 >=2 次且尚未学会的未命中关键词列表。
   * 对应界面的 miss 指令(展示"别人问了但没答上"的词,引导教学)。
   * 失败返回空数组。
   */
  async misses(): Promise<Array<{ keyword: string; count: number; last_seen: string }>> {
    try {
      const ip = await getIp()
      const res = await api.misses({ ip })
      if (res.code === 200 && res.data?.list) {
        return res.data.list
      }
      return []
    } catch (err) {
      console.log(`待学习清单获取失败 ... ${err}`)
      return []
    }
  },

  /** 好感信息(P2,FEATURE_FAVOR):返回好感数据;功能未开启(400)或失败返回 null */
  async favor(uid: string): Promise<FavorInfo | null> {
    try {
      const res = await api.favor({ uid })
      if (res.code === 200 && res.data) {
        return res.data
      }
      return null
    } catch (err) {
      console.log(`好感获取失败 ... ${err}`)
      return null
    }
  },

  /** 心跳(P2,FEATURE_FAVOR):上报在线秒数累计好感,功能未开启时静默失败 */
  async active(uid: string, seconds: number): Promise<boolean> {
    try {
      const res = await api.active({ uid, seconds: String(seconds) })
      return res.code === 200
    } catch (err) {
      console.log(`心跳上报失败 ... ${err}`)
      return false
    }
  },

  /** 屏蔽/解除屏蔽(P5,FEATURE_MAID):仅调教师(uid == MASTER_UID)可用 */
  async block(uid: string, targetUid: string, action = 'block'): Promise<boolean> {
    try {
      const res = await api.block({ uid, target_uid: targetUid, action })
      return res.code === 200 && res.data === 'success'
    } catch (err) {
      console.log(`屏蔽操作失败 ... ${err}`)
      return false
    }
  },

  /** 调教师删除任意条目(P5,FEATURE_MAID):仅调教师可用 */
  async adminDelete(uid: string, answer: string): Promise<boolean> {
    try {
      const res = await api.adminDelete({ uid, answer })
      return res.code === 200 && res.data === 'success'
    } catch (err) {
      console.log(`调教师删除失败 ... ${err}`)
      return false
    }
  },
}

/** 好感信息结构(P2,FEATURE_FAVOR) */
export interface FavorInfo {
  uid: string
  score: number
  level: number
  level_name: string
  talk_count: number
  teach_count: number
  active_seconds: number
}
