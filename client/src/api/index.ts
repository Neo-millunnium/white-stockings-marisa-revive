// 后端 API 封装。
//
// 后端契约(不能改):全部 POST + form-urlencoded,返回 JSON { code, data }。
//
// 请求地址:
// - 开发环境:走 Vite 代理,请求 /api/xxx 会被转发到 http://127.0.0.1:3000/xxx 并去掉 /api 前缀,无跨域问题;
// - 生产环境:默认也是相对路径 /api,由反向代理(nginx 等)或后端路由组把 /api 转发到后端;
//   也可以用环境变量 VITE_API_BASE_URL 覆盖为后端绝对地址(此时后端需要开启 CORS)。
const BASE_URL: string = import.meta.env.VITE_API_BASE_URL || '/api'

/** 后端统一返回结构 */
export interface ApiResponse<T = unknown> {
  code: number
  data: T
  message?: string
}

/**
 * 统一请求:POST + form-urlencoded(用 URLSearchParams,不要用 JSON body)。
 */
async function request<T = unknown>(
  path: string,
  data?: Record<string, string>,
): Promise<ApiResponse<T>> {
  const body = new URLSearchParams()
  if (data) {
    for (const key of Object.keys(data)) {
      body.append(key, data[key])
    }
  }

  const resp = await fetch(`${BASE_URL}${path}`, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/x-www-form-urlencoded',
    },
    body: body.toString(),
  })

  if (!resp.ok) {
    throw new Error(`HTTP ${resp.status}`)
  }
  return (await resp.json()) as ApiResponse<T>
}

export const api = {
  /** 教学:POST /Add,form: ip, keyword, answer, category(默认 auto 自动判定) */
  add: (data: { ip: string; keyword: string; answer: string; category?: string }) =>
    request<{ ip: string; keyword: string; answer: string; category: string }>('/Add', data),

  /** 提问:POST /Reply,form: ip, keyword。命中 code=200;未命中 code=10001(data.answer 为兜底话术);限流 code=429 */
  reply: (data: { ip: string; keyword: string }) => request<{ answer: string }>('/Reply', data),

  /** 忘记:POST /Forget,form: answer,成功返回 data: "success" */
  forget: (data: { answer: string }) => request<string>('/Forget', data),

  /** 状态:POST /Status,无参数,data 为知识条数 */
  status: () => request<number>('/Status'),

  /** 分类统计:POST /Categories,data 为 {word, sentence, syntax, logic, greeting, unclassified} */
  categories: () =>
    request<Record<string, number>>('/Categories'),

  /** 开场白:POST /Greeting,data 为 {keyword, answer},随机一条 greeting 分类记忆 */
  greeting: () => request<{ keyword: string; answer: string }>('/Greeting'),

  /** 提示线索:POST /Hint,无参数,data 为 {keyword, answer} 随机一条 */
  hint: () => request<{ keyword: string; answer: string }>('/Hint'),
}
