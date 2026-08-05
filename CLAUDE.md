# CLAUDE.md — 给 AI 协作者的项目手册

## 项目一句话

web-marisa:「白丝魔理沙」网页聊天机器人,一个**可教化的关键词检索式聊天机器人**(无 AI 模型)。用户用 `关键词`回答` 格式教学,机器人分词匹配后回复。

## 关键约定(改代码前必读)

1. **API 契约不可破坏**:全部接口 `POST + form-urlencoded`,返回 `{code, data}`;HTTP 状态恒 200,业务码在 JSON 里
   - `POST /Add`(ip/keyword/answer/category,教学;category 默认 auto)、`POST /Reply`(**ip**/keyword,提问)、`POST /Forget`(answer)、`POST /Status`、`POST /Categories`(分类统计)、`POST /Hint`、`GET/POST /`(探活)
   - 业务码:`200` 成功、`400` 参数不合法/黑名单/违禁词、`429` 限流(教学每 IP 10 次/分、**回复每 IP 30 次/分**)、`10001` 未命中
2. **未命中兜底话术是产品逻辑**:`唔嗯...不懂你在说什么呢...教教我吧~`(前后端都有引用,别乱改)
3. **深夜催睡(3:50-6:00)**:`service.py` 里 `in_sleep_window()` 判断,凌晨 3:50 ~ 6:00 之间 Reply 不查库直接返回固定话术 `SLEEP_ANSWER`,Add 教学也拒绝(返回 code 400 + SLEEP_ANSWER)。文案/时间边界是 `service.py` 顶部常量(SLEEP_START/SLEEP_END/SLEEP_ANSWER)可调。改这块先看常量注释
4. **AI 内容审核(先审后玩)**:教学内容 Add 后只进**待审队列**(`review_status=pending`),**不立即生效**(不进内存索引,Reply 答不出、Status 不计数);每小时后台任务(`main.py` `_review_loop`,间隔 `REVIEW_INTERVAL`、每批 `REVIEW_BATCH_LIMIT` 条)调 DeepSeek 批量审核,**通过才进索引生效,被拒的回答原文进黑名单**(`blacklist` 表,再次教学同回答直接 400 拒)。**违禁词前处理**:`server-py/banned_words.txt` 每行一个正则,教学时回答命中违禁词直接拒绝+拉黑,不进待审队列(`service.match_banned`)。审核 API key 从环境变量 `DEEPSEEK_API_KEY` 读(见 `review.py`,含 .env 兜底)。`POST /Review`(secret=`REVIEW_SECRET`)可手动触发审核,`POST /Reload`(同 secret)可从库重建索引。改审核逻辑看 `review.py` + `service.review_pending`
5. **教学格式 `关键词`回答`(反引号)是核心玩法**,前端 core/index.ts 和后端都依赖它
6. **teach 分类教学(恢复 2010 原版指令)**:`teach` / `teach word|sentence|syntax|logic|greeting|auto` / `teach word 关键词`(分类+关键词同行)。分类存 `memorise.category` 列,`auto` 由 `service.detect_category` 按内置问候词表自动判定(greeting/word 二选一,其余分类只能手指定)。**精确匹配多条时 greeting 类优先返回**(`reply()` 里 max 排序键)。`POST /Categories` 返回分类统计(旧数据 NULL 计 unclassified)。分类常量/问候词表在 `service.py` 顶部 `TEACH_CATEGORIES`/`GREETING_WORDS`。改这块先看常量注释
7. **hint 指令(提示线索)**:输入 hint 随机展示一条已审核通过的记忆(后端 `POST /Hint` -> `service.hint()`,前端 `marisaHint`)。application 指令已删除(原为废弃占位,无实现)
8. **前端消息渲染用 v-text 纯文本**(安全),永远不要改回 v-html(存储型 XSS)
9. **git 历史**:baseline commit 是原项目 zip 快照,后续是重构历史,不要改写历史、不要强推

## 技术栈

- 后端 `server-py/`:Python 3.11 · FastAPI · SQLAlchemy 2 · jieba 分词
- 前端 `client/`:Vue 3.5(Composition API)· Vite 8 · TypeScript 5 · Sass
- 数据库:SQLite(默认,`server-py/webmarisa.db`,零依赖零内存);可选 MySQL/MariaDB(`DB_TYPE=mysql`)
- `server/`:旧 Go 版,保留参考,**已弃用,不要改**

## 后端结构(server-py/)

```
main.py        FastAPI 入口 + 5 个路由(Add/Reply/Forget/Status/)
config.py      配置:.env > 环境变量 > 默认值(SQLite;DB_TYPE=mysql 切 MySQL)
database.py    SQLAlchemy 引擎 + 启动自动建表(SQLite create_all / MySQL ALTER)
models.py      Memorise ORM(memoryId 主键,ip/keyword/answer/raw_keyword/hit_count/created_at/updated_at)
service.py     核心业务:分词/匹配/子集合并/限流/倒排索引/未命中记录
test_api.py    接口测试(29 项,urllib 连 127.0.0.1:3000 + SQLAlchemy 读 SQLite 校验)
webmarisa.db   SQLite 数据文件(不入库,由程序生成)
```

### 核心业务逻辑(service.py)

- **教学 Add**:限流(每 IP 每分钟 10 次)→ 校验(关键词 1-50 字、回答 1-500 字)→ jieba 分词 → **子集合并**(新分词是已有词条分词的子集才合并,否则新增)→ 入库 + 更新内存索引
- **回复 Reply**:① raw_keyword 精确匹配优先 → ② jieba 分词查内存倒排索引 → ③ 重合度(已有词条中出现在输入里的词占比)≥ 40% 命中 → ④ 命中多条随机选一条,hit_count+1
- **内存倒排索引**(MemoryIndex):启动从库全量重建,Add/Delete 时同步维护;数据量小,单进程,带锁即可
- **限流**:内存 dict + 时间戳队列,重启清零
- **未命中记录**:recent_misses(deque,50 条,仅内存),给未来"待学习清单"用

### 后端常用命令

```bash
# 启动(端口 3000 是正式端口;config 默认 3100,靠 .env 或命令行覆盖)
cd server-py && .venv/Scripts/python -m uvicorn main:app --host 127.0.0.1 --port 3000

# 测试(需后端在跑,且连 127.0.0.1:3000)
cd server-py && .venv/Scripts/python test_api.py

# 注意:本机 python 无 pip,装依赖用 uv(.venv 已存在则无需重装)
cd server-py && uv pip install fastapi uvicorn sqlalchemy jieba
```

## 前端结构(client/)

```
src/views/chatroom.vue   聊天室界面(教学/忘记/状态指令、立绘、指令面板)
src/core/index.ts        speak/reply/teach/forget/status 业务逻辑(IP 获取有 3s 超时回退)
src/api/index.ts         fetch + URLSearchParams 封装,baseURL=/api(dev 走 vite proxy)
src/assets/css/          Sass 样式(_variables.scss 是变量,白底 #ffffff,无像素字体)
vite.config.ts           dev 8888 端口,/api 代理到 127.0.0.1:3000 并去前缀
```

### 前端常用命令

```bash
cd client
npm install            # registry 已配 npmmirror
npm run dev            # dev server :8888(自动热更新)
npm run build          # 产物 client/dist/
npm run type-check     # vue-tsc 类型检查(改完代码必须跑)
```

## 视觉铁律(改前端别破坏)

- 背景纯白 `#ffffff`,无像素字体(marisa-cmd 用 sans-serif 13px bold)
- 主窗口 `#cccc99` 米色,边框 `#022c60`,面板 `#f5f7ea`,标题栏 `#d1d9c1`,You 消息 `#4876ff`
- 立绘 marisa.jpg:`background-size: 333px; position: 58% 2%`
- 712×512 左右布局:左对话区 + 右立绘/指令面板

## 预置数据

- `seed_qa.py`:39 条魔理沙人设问答对(问候/自我介绍/东方角色/设定/互动),走 /Add 接口插入(随机 IP 绕限流)
- 本地库里已有 44 条(含 seed 与历史数据),迁移/重置时用 seed 脚本重建基础数据

## 部署注意

- 生产后端用 gunicorn + uvicorn worker;内存索引是进程内态,多进程下各进程独立,写入要固定节点(简单场景单进程即可)
- 前端 build 后 nginx 托管,`/api` 反代到后端;生产记得改数据库密码(别用 root 空密码)
- 详细见 README.md「生产部署建议」
