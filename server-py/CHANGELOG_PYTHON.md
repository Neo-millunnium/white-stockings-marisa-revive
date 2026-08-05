# web-marisa 后端 Python 重写 CHANGELOG

重写日期:2026-08-05
范围:`server-py/`(新建,Python 版后端),前端 `client/` 仅改 1 处 XSS 修复;旧 Go 版 `server/` **原样保留未动**。

## 一、技术栈变更

| 项目 | Go 版(server/) | Python 版(server-py/) |
| ---- | --------------- | --------------------- |
| 语言 | Go 1.21 | Python 3.11(本机 Python311) |
| Web 框架 | gin-gonic/gin | FastAPI + uvicorn |
| ORM | jinzhu/gorm v1.9.16 | SQLAlchemy 2.0 |
| 数据库驱动 | go-sql-driver/mysql | PyMySQL |
| 分词 | huichen/sego(自定义词典) | jieba(默认词典,加载时 `jieba.initialize()`) |
| 依赖管理 | go modules | uv(本机没有 pip,一律 uv) |

## 二、目录结构(server-py/)

```
server-py/
├── main.py            # FastAPI 入口:路由 + 启动钩子(迁移 -> 重建索引)
├── config.py          # 配置读取(.env / 环境变量,默认值 root@127.0.0.1:3306/webmarisa)
├── models.py          # SQLAlchemy ORM 模型 Memorise(表名单数 memorise)
├── database.py        # 引擎/会话工厂 + 启动时自动迁移(ALTER TABLE 补列)
├── service.py         # 业务逻辑:教学/回复/忘记/状态 + 内存倒排索引 + 限流 + 未命中记录
├── .env               # 本地配置(端口 3100、数据库连接)
├── test_api.py        # 接口验证脚本(29 项)
└── CHANGELOG_PYTHON.md  # 本文档
```

## 三、API 契约(与 Go 版完全兼容,前端无需改动请求方式)

全部 POST + form-urlencoded,HTTP 状态恒为 200,业务码在 JSON `code` 字段:

- `GET/POST /` → `{"code":200,"message":"hello Marisa~"}`
- `POST /Add`(form: ip, keyword, answer)→ 成功 `{"code":200,"data":{"ip":...,"keyword":...,"answer":...}}`
  - 参数不合法 → `{"code":400,"data":"参数不合法:..."}`
  - 教学太频繁 → `{"code":429,"data":"教学太频繁了,休息一下吧~"}`
- `POST /Reply`(form: keyword)→ 命中 `{"code":200,"data":{"answer":"..."}}`;未命中 `{"code":10001,"data":{"answer":"唔嗯...不懂你在说什么呢...教教我吧~"}}`
- `POST /Forget`(form: answer)→ `{"code":200,"data":"success"}`
- `POST /Status` → `{"code":200,"data":<记忆条数>}`

## 四、数据库迁移(启动时自动执行,不修改已有数据)

保留原表 `memorise(memoryId, ip, keyword, answer)`,启动时用 `information_schema` 检测并 `ALTER TABLE` 补齐新增列(幂等,已存在则跳过):

- `raw_keyword TEXT` —— 用户教学时的原始关键词(未分词),用于精确匹配优先
- `hit_count INT NOT NULL DEFAULT 0` —— 被命中次数
- `created_at DATETIME`、`updated_at DATETIME`

原有 4 条数据的 `keyword/answer` 值保持不变;旧数据的 `raw_keyword` 为 NULL(不猜测原始输入),`hit_count` 默认 0。

## 五、业务逻辑(与 Go 版对比)

### 教学 Add
1. jieba 分词(有序去重)。
2. **输入校验(新增)**:keyword/answer 均非空;keyword 去首尾空白后长度 1-50;answer 长度 1-500;超限返回业务码 400 并说明原因。
3. **合并逻辑改进(修复原 bug)**:Go 版是"新词与已有词条重合度 >= 60% 就合并",太激进。改为**只有新分词集合完全包含于某条已有记忆的分词集合(子集)时才合并**,合并后的 keyword 为"已有词条 + 新词"的有序去重;否则新增一条记忆。
4. `raw_keyword` 存原始输入、`hit_count=0`、时间戳写当前时间;教学后增量更新内存索引。

### 回复 Reply
1. **精确匹配优先(新增)**:输入与某条记忆的 `raw_keyword` 完全相等时直接返回该条(权重最高)。
2. 否则 jieba 分词,查**内存倒排索引**得到候选记忆,计算重合度 `>= 40%` 的命中集合(与 Go 版阈值一致)。
3. **命中多条时随机选一条(新增)**,Go 版固定返回第一条;命中后该条 `hit_count +1`。
4. 未命中返回固定兜底话术;并把该输入记入内存"最近未命中"(最多 50 条,带时间,仅注释说明,不落库、不暴露接口)。

### Forget / Status
- Forget 按 answer 精确删除(可删多条),返回 success。
- Status 返回当前记忆总条数。

### 性能与防滥用(新增)
- **内存倒排索引**:启动时全表加载成 `分词 -> 记忆ID集合`;Add/Delete 时增量维护;Reply 直接查索引而非全表扫描。单进程、数据量小,索引一致性仅用简单互斥锁保证。
- **每 IP 限流**:内存 dict + 时间戳,每 IP 每分钟最多 10 次 Add,超限返回业务码 429。

## 六、前端修改(唯一允许的一处)

- `client/src/views/chatroom.vue` 第 15 行:`v-html="item.content"` → `v-text="item.content"`。消息内容来自用户教学输入,`v-html` 是存储型 XSS 漏洞;改为纯文本渲染。其余前端文件一律未动。

## 七、与 Go 版的差异小结

| 维度 | 差异 |
| ---- | ---- |
| 分词器 | sego → jieba,切词结果可能不同(旧数据 keyword 是 sego 的切词,新数据用 jieba) |
| 合并规则 | 60% 重合合并 → 子集才合并(更保守,避免语义不同的句子被合并) |
| 精确匹配 | Go 版没有 raw_keyword 精确匹配,Python 版新增(最高权重) |
| 命中随机 | Go 版固定第一条,Python 版随机选一条 |
| 输入校验 | Go 版无,Python 版新增(400) |
| 限流 | Go 版无,Python 版新增(429) |
| 索引 | Go 版每次全表扫描,Python 版内存倒排索引 |
| 未命中记录 | Go 版无,Python 版内存保留最近 50 条 |

注意:sego → jieba 后,旧的 `keyword` 字段是 sego 切词结果(如 `今天,天气,今天天气,怎么,样,怎么样` 六段),用 jieba 提问时重合度分母较大,部分旧词条可能难以达到 40% 命中阈值,属分词器差异的固有效应,不在本次改动范围内(不能改旧数据)。

## 八、运行与验证

启动(注意用 3100 端口,3000 仍被旧 Go 版占用):

```bash
cd D:\projects\web-marisa\server-py
.venv\Scripts\python -m uvicorn main:app --host 127.0.0.1 --port 3100
```

验证(另开终端,依赖本机 MariaDB 客户端用于校验数据库;测试会自动清理产生的数据):

```bash
cd D:\projects\web-marisa\server-py
.venv\Scripts\python test_api.py
```

验证结果:uvicorn 在 127.0.0.1:3100 正常启动,日志无错误;`test_api.py` 覆盖探活、Status、Add 校验(空/超长)、Reply 精确匹配与未命中、子集合并 vs 非子集新增、Forget、Status 回归、限流(11 次 Add 第 11 次 429)、命中随机性(20 次回复出现不同答案)、数据库数据保留,共 **29 项全部通过**,且可连续重复运行(每次运行使用独立 IP,不受限流窗口影响)。

数据库现有 4 条数据原样保留(条数 4,`keyword/answer` 值未变)。

## 九、备注

- 环境:本机没有 pip,依赖全部通过 `uv venv` + `uv pip install fastapi uvicorn sqlalchemy pymysql jieba python-multipart` 安装。
- 限流、未命中记录均为内存态,重启后清空;限流窗口 60 秒。
- 若在服务运行期间直接对数据库做 SQL 增删,内存索引不会自动同步,重启后端即可重建(测试脚本通过 API 增删,保持同步)。
- 旧 Go 版 `server/` 未做任何改动,由用户决定其去留。
