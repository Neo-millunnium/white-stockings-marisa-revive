// 魔理沙核心逻辑:说话格式、回复、教学、忘记、状态。
import { api } from '../api'

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
      const res = await api.reply({ keyword: content })
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
   * 教学:content 形如 关键词`回答(注意是反引号分隔,产品逻辑必须保留)。
   * 拆开后在参数里分别传给后端。返回是否学习成功。
   */
  async teach(content: string): Promise<boolean> {
    const parts = content.split('`')
    const keyword = parts[0] || ''
    const answer = parts[1] || ''
    const ip = await getIp()
    try {
      const res = await api.add({ ip, keyword, answer })
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
      const res = await api.forget({ answer })
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
}
