# web-marisa 前端重构任务(Vue3 + Vite)

## 项目背景
web-marisa 是 2019 年的东方 Project"白丝魔理沙"聊天机器人。后端已重构为 Go + gin(在 D:\projects\web-marisa\server\,跑在 127.0.0.1:3000,不要动)。本次只重构前端 client/ 目录。

旧前端:Vue 2.6 + vue-cli(webpack3) + TypeScript 3.9 + stylus,单页聊天室,在 Node 26 上编译需要 --openssl-legacy-provider hack,依赖全部过老。目标:**重写为 Vue 3 + Vite + TypeScript 现代栈**,功能与视觉风格保持不变。

## 核心功能(必须全部保留)

1. 聊天室界面:上方对话区(显示 You 和 白絲魔理沙 的消息),下方输入框 + 发送按钮
2. 教学指令:输入 `teach` 进入教学模式,再输入 `关键词`回答` 格式(注意是反引号分隔)教会魔理沙
3. 忘记指令:输入 `forget` 忘记最后教的回答
4. 状态指令:输入 `status` 查看当前知识条数
5. 系统指令说明面板(在页面右侧,展示 teach/forget/status/application 的说明文字)
6. 对话滚动到底部

## API 契约(后端不能改,前端必须按这个调)

后端在 http://127.0.0.1:3000/,所有接口 POST + form-urlencoded,返回 JSON {code, data}:
- POST /Add   form: ip, keyword, answer   -> 教学,成功返回 {code:200, data:{ip,keyword,answer}}
- POST /Reply form: keyword               -> 提问,{code:200,data:{answer}} 命中;{code:10001,data:{answer}} 未命中
- POST /Forget form: answer               -> 忘记,{code:200,data:"success"}
- POST /Status (无参数)                   -> {code:200, data:<条数>}

前端请求时必须用 form-urlencoded(不要用 JSON body),可以用 URLSearchParams 或 FormData。

## 视觉风格(必须保持"白丝魔理沙"气质)

参考旧版 src/assets/css/ 和 src/views/chatroom.vue:
- 深色背景,黑白色调,像素感(旧版用了 PressStart2P 像素字体,资源在 src/assets/fonts/PressStart2P.ttf)
- 角色立绘 src/views/img/marisa.jpg 要保留使用
- 对话气泡区分 You(你自己)和 白絲魔理沙
- 整体是"复古像素网页"的感觉,不要做成现代扁平风

## 技术约束

- Vue 3(Composition API)+ Vite + TypeScript
- 状态管理简单即可(Pinia 或纯 ref,选你觉得合适的)
- stylus 样式可保留(旧版用 stylus,你有权选择 stylus 或 scss,但视觉必须还原)
- vite.config.ts 里配 dev server 端口 8888,proxy 把 /api 代理到 127.0.0.1:3000(如果这样做的话注意旧版是直接写死 http://127.0.0.1:3000/ 的 baseURL,你可以保留这个方式更简单,但要处理 CORS:如果 dev 用 proxy 就没 CORS 问题,如果用直连 baseURL 后端需要允许 CORS——**优先用 dev proxy 方案**,生产 build 用相对路径 /api 并说明后端需要怎么配)
- 后端 gin 需要加 CORS 中间件的话,告诉用户需要改哪里(可以在任务报告里说明,不要改后端代码)
- npm registry 已配置 npmmirror
- 依赖版本选当前稳定的(Vue 3.4+, Vite 5+, TypeScript 5+)

## 完成标准

1. npm install + npm run build 成功,dist 产出
2. 你写一个说明(README_FRONTEND.md)记录:怎么起 dev server、怎么 build、后端 CORS 怎么配
3. 用 python urllib 验证后端 4 个接口可用(如果后端没跑,说明里写清楚怎么起)
4. 报告:改了哪些文件、新旧对比、视觉还原度说明

## 重要提示

- 旧代码在 client/src/ 下,可以读参考,但这是重写不是修补
- 不要动 server/ 目录任何文件
- 旧版 src/api/index.ts 里有 baseURL 写死 http://127.0.0.1:3000/ 的逻辑,重写时参考但用更好的方案
- 教学格式是 `关键词`回答`(反引号),这是产品逻辑,必须保留
- 所有代码注释用中文
