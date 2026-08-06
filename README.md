# marisa-revive · 白丝魔理沙怀旧服

东方 Project 角色「白丝魔理沙」的网页聊天机器人。一个可教化的"调教型"关键词检索机器人:
用户用 `关键词`回答` 的格式教它说话,它记住后,别人提问命中关键词就返回对应回答。

> 🍄 这个项目的机制最早可以追溯到 2011 年的 QQ 调教 bot(原始版本已失传)。
> 本仓库是 2018-2019 年的网页版复刻,2026 年经本地现代化重构后重新开源。

## 参考项目

本仓库基于以下项目重构:

- **[TohoOutsiders/web-marisa](https://github.com/TohoOutsiders/web-marisa)** —— 原作者 gutrse3321(Tomonori)于 2018-2019 年发布的网页版"白丝魔理沙"聊天机器人(Go + iris/gin 后端、Vue 2 前端,线上已停服)。本仓库的前后端均以其为蓝本:
  - 后端:由 Go(iris → gin)重写为 Python FastAPI,业务逻辑(教学/回复/匹配阈值/合并规则)与 API 契约保持一致
  - 前端:由 Vue 2 + webpack 重写为 Vue 3 + Vite,界面布局、指令系统、视觉元素(立绘/配色)还原原版
  - 原项目的 `server/`(Go 版)目录在本仓库中保留作参考,已弃用
- **2010 年的原始调教 bot**(作者孙鸭子)—— 本项目的 `teach`/`forget`/`status` 指令体系和"用户教学驱动"玩法源自那个年代的 QQ 调教 bot 传统,原始实现已失传,机制经原网页版传承至今

若需查看原始实现,可访问原仓库(注意:原仓库 master/gin/iris 分支内容与本仓库差异较大)。

---

## 它是什么

- **不是 AI** —— 没有模型、没有生成式回复。核心是一个关键词匹配的检索式聊天机器人
- **知识来自用户** —— 你教它什么,它就会什么。教得越多越"聪明"
- **指令系统**(仿 2011 年 QQ 调教 bot):
  - `teach` 进入教学模式,然后输入 `关键词`回答`(反引号分隔)
  - `forget` 忘记最后教的回答
  - `status` 查看当前掌握的知识条数

## 工作原理

```
教学:  用户输入 "你好`你好呀,我是白丝魔理沙!"(前端拆分后 POST /Add)
       └─ 后端 jieba 分词 -> 去重合并 -> 存 MySQL(memorise 表)

提问:  用户输入 "你好"(POST /Reply)
       └─ 1. raw_keyword 精确匹配优先
          ├─ 2. jieba 分词,查内存倒排索引
          ├─ 3. 与已有词条算分词重合度,>= 40% 命中
          └─ 4. 命中多条随机选一条;全不中返回兜底话术
```

详细流程见 `server-py/service.py`(代码注释详尽)。

## 技术栈

| 层 | 技术 |
|----|------|
| 后端 | Python 3.11 · FastAPI · SQLAlchemy 2 · jieba 分词 |
| 前端 | Vue 3.5 · Vite 8 · TypeScript 5 · Sass |
| 数据库 | SQLite(默认,零依赖零内存);可选 MySQL/MariaDB(`DB_TYPE=mysql`) |

## 目录结构

```
web-marisa/
├── server-py/          # Python 后端(FastAPI)
│   ├── main.py         # 入口:FastAPI + 路由
│   ├── service.py      # 业务逻辑:分词/匹配/合并/限流
│   ├── database.py     # SQLAlchemy 引擎 + 启动迁移
│   ├── models.py       # ORM 模型
│   ├── config.py       # 配置(.env / 环境变量 / 默认值)
│   └── test_api.py     # 接口测试(29 项)
├── client/             # 前端(Vue 3 + Vite)
│   └── src/            # 聊天室界面、API 封装、样式
├── server/             # 旧 Go 版后端(保留参考,已弃用)
└── seed_qa.py          # 预置魔理沙人设问答对(39 条)
```

## 快速开始(本地开发)

依赖:Python 3.11+、Node 18+。数据库默认 SQLite,**无需安装数据库软件**。

```bash
# 1. 后端(首次启动自动建表 server-py/webmarisa.db)
cd server-py
uv venv && uv pip install fastapi uvicorn sqlalchemy jieba
uv run python -m uvicorn main:app --host 127.0.0.1 --port 3000

# 2. 前端
cd ../client
npm install
npm run dev        # http://localhost:8888

# 3. (可选)预置魔理沙人设问答
cd ..
python seed_qa.py  # 插入 39 条预设问答对
```

浏览器打开 http://localhost:8888 即可聊天。

> 想用 MySQL/MariaDB?改 `server-py/.env` 里 `DB_TYPE=mysql` 并填 `DB_HOST/DB_PORT/DB_USER/DB_PASSWORD/DB_NAME`,依赖加上 pymysql,建库后启动自动建表/迁移。

## API 文档

全部接口:POST + form-urlencoded,返回 JSON `{code, data}`(HTTP 状态恒为 200,业务码在 JSON 里):

| 接口 | 参数 | 说明 | 返回 |
|------|------|------|------|
| `POST /Add` | `ip`, `keyword`, `answer` | 教学(分词入库,子集合并) | `{code:200, data:{ip, keyword, answer}}` |
| `POST /Reply` | `ip`, `keyword` | 提问(精确匹配优先 → 分词命中 ≥40% 随机选) | 命中 `{code:200, data:{answer}}` / 未命中 `{code:10001, data:{answer:兜底话术}}` |
| `POST /Forget` | `answer` | 按回答删除 | `{code:200, data:"success"}` |
| `POST /Status` | — | 知识条数 | `{code:200, data:整数}` |
| `POST /Hint` | — | 随机一条提示线索(已审核内容) | `{code:200, data:{keyword, answer}}` |
| `GET/POST /` | — | 探活 | `{code:200, message:"hello Marisa~"}` |

业务码:`200` 成功、`400` 参数不合法/黑名单/违禁词、`429` 限流(教学每 IP 每分钟 10 次,回复每 IP 每分钟 30 次)、`10001` 未命中。

## 特性

- 内存倒排索引(分词 → 记忆),回复不走全表扫描
- 输入校验(关键词 ≤50 字、回答 ≤500 字,非空)
- 每 IP 教学限流(每分钟 10 次)
- 命中计数 `hit_count`、最近未命中关键词记录(内存,重启清空)
- 存储型 XSS 防护(前端消息纯文本渲染,`v-text`)
- **深夜催睡**:凌晨 3:50 ~ 6:00 之间不回复,只输出固定催睡话术(文案在 `server-py/service.py` 的 `SLEEP_ANSWER`)
- **AI 内容审核(先审后玩)**:教学内容先进入待审队列,不立即生效;每小时由 DeepSeek 批量审核(每批最多 10 条),通过才生效,被拒的回答进黑名单(同回答再次教学直接拒绝)
- **违禁词前处理**:`server-py/banned_words.txt` 配置正则词库,教学回答命中违禁词直接拒绝+拉黑,不进待审队列(可自行增删正则)
## TODO / Roadmap

- [x] **待学习清单**:展示"被问过但没答上"的关键词(服务端 `miss_keyword` 表聚合,被问 >=2 次且未学会),教学审核通过后自动隐藏 —— 已实现(2026-08-06)
- [x] **资讯工具**:时间 / 日期 / 计算器(离线,默认启用),天气 / 汇率 / 百科(可插拔,`WEATHER_API`/`EXCHANGE_API`/`DICT_API` 配置后启用);精确匹配(用户教学)优先于工具 —— 已实现(2026-08-06)
- [ ] **话题感知(theme)**:对多句对话整体判断话题,在特定话题内精准回答,而不是全局随机
- [ ] **好感度系统(F-I)**:按用户维度记录好感(对话次数/时长/内容),影响回复倾向
- [ ] **对象判断(flag)**:相同的话,对不同人 / 不同好感阶段 / 不同时间给出不同回复
- [ ] **多人协作教学 + 调教师权限**:多用户共同完善同一知识条目,支持屏蔽管理
- [ ] **代码执行支持**:教学时可编写简单脚本逻辑(需 AST 白名单防注入;`safe_eval.py` 已就绪)
- [ ] **生产部署实测**:MySQL 模式真机验证、gunicorn 多进程写入固定节点方案文档化

> 各项的详细实现方案(数据模型/接口/业务逻辑/前端改动点)见 `IMPLEMENTATION_PLAN.md`。

## 生产部署建议

- **存储**:默认 SQLite 单文件,低配服务器(512M 内存)也能跑,无需装数据库;数据文件就是 `server-py/webmarisa.db`,备份=拷走一个文件。多人并发写入场景再考虑 MySQL(`DB_TYPE=mysql`)
- 后端:`uvicorn` 建议用 `gunicorn -k uvicorn.workers.UvicornWorker` 多进程(注意:内存倒排索引为进程内态,多进程下各自维护,写入节点需固定;简单场景单进程即可,SQLite 也更适合单进程)
- 前端:`npm run build` 后由 nginx 托管,`/api` 反向代理到后端(见 `client/vite.config.ts` 的 proxy 配置)

## 致谢

- 原始网页版作者:gutrse3321(Tomonori)2018-2019
- 2010 年白丝魔理沙作者:孙鸭子,感谢那个年代的所有调教 bot 玩家
- 东方 Project © ZUN / 上海爱丽丝幻乐团

## License

MIT(见各子目录 LICENSE 说明;原始网页版未声明许可证,仅保留学习交流用途)
