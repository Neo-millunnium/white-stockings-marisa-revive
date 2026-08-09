# web-marisa 萌娘回路(Moec Core)能力 · 实现方案与落地记录

> 本文档最初是"未实现能力"的设计方案(2026-08 初,FastAPI 时代),如今 **P0-P6 已全部落地**
> (2026-08,统一由 `FEATURE_*` 功能开关控制,默认 off),仅剩 **P7 生产部署实测**待做。
> 期间后端经历两轮重构:FastAPI -> Litestar(契约不变)-> 全异步(aiohttp + SQLAlchemy
> asyncio + aiosqlite)+ WebMarisaServer(conf.yaml)。本文已按现状校准:引用一律精确到
> 文件与函数,不再使用会腐烂的行号;各节保留设计动机("为什么这么设计"),作为功能档案。
> 产品逻辑铁律不变:未命中兜底话术、深夜催睡、AI 先审后玩、反注入限流、`关键词`回答` 反引号格式。

---

## 0. 公共基础设施:匿名身份层(uid)—— ✅ 已实现

特性 3/4/5/6 都依赖"按用户维度记录状态"。接口只带 `ip` 时,NAT/移动网络下会串号,
所以落地了**匿名 cookie UUID + IP 双标识**的基础层:

- **前端** `client/src/core/identity.ts`:`getUid()` 读 `localStorage['marisa_uid']`,无则
  `crypto.randomUUID()` 生成并回写(浏览器原生,零依赖);`getIp()` 保持为第二标识。
  所有请求(Add/Reply/Active/Favor/Misses/Block...)统一附带 `uid`。
- **后端**:`svc.add/reply` 等签名带 `uid=""`(缺省兼容老客户端,回退用 `ip` 做 key);
  按用户状态(`_context`、favorability、blocked)以 `uid` 为主键、`ip` 为兜底。
- **权限铁律**:`uid` 是客户端自报的匿名身份,只用于"区分",不作可信权限——调教师
  权限只信服务端配置 `MASTER_UID` 与 `teacher` 表(见 P5),绝不信任客户端自声明身份。

---

## 1. 【P0】待学习清单 —— 展示"没教会的话"并引导教学 —— ✅ 已实现

### 设计动机
把"别人问了但魔理沙答不出"的关键词展示给用户,引导 teach,补全"教-答-不会-教"闭环。

### 落地情况(2026-08-06)
- **表** `miss_keyword`(models.py):keyword 唯一、miss_count、first/last_seen、resolved_at;
  MySQL 迁移分支 `database.run_migrations` 同步建表。
- **业务**(service.py):`_miss_upsert(kw)` 在 reply 未命中时归一化落库聚合;
  `_resolve_misses(row)` 在**审核通过**时按"精确相等或分词子集"标记已解决
  (关键:不能 add 时 resolve——pending 未生效,若被拒该词其实仍没学会);
  `misses()` 查 `resolved_at IS NULL AND miss_count >= 2`,按次数降序取 `MISS_LIST_MAX=20`。
- **接口** `POST /Misses`(复用回复限流防刷);`recent_misses` 内存 deque 保留为瞬时缓冲。
- **前端** `miss` 指令逐条引导教学;指令面板加一行。

---

## 2. 【P1】资讯工具 —— 时间/计算器/天气等正则触发 —— ✅ 已实现

### 设计动机
"现在几点""1+2"这类即时资讯直接给动态答案,不再回兜底话术。
优先级设计(至今不变):**精确匹配(用户显式教过,权重最高)→ 资讯工具 → 分词重合匹配**。
理由:用户教过的"现在几点"应赢过工具;没教过时工具比随机重合更实用。深夜催睡在
`reply()` 最顶部拦截,工具不会在催睡时段触发。

### 落地情况(2026-08-06,后续有增强)
- **tools.py 注册表**:`TOOL = (name, compiled_regex, async handler)`,`match_tool` 遍历返回
  第一个非空结果(name, text);全异步(aiohttp 共享会话,懒加载 + shutdown 回收)。
- **离线工具(零依赖,默认启用)**:时间/日期;计算器(二元运算,`safe_eval` 白名单求值,
  除零/超大数回落)。
- **可插拔在线工具(默认关,配 key 启用)**:天气(高德 `AMAP_KEY`,IP 定位城市 -> adcode
  -> 实时天气,定位不到回退 `AMAP_ADCODE` 默认城市)、汇率(`EXCHANGE_API`)、百科
  (萌娘百科 extracts,`DICT_API`,自动提取词条名,取摘要前 120 字);失败静默降级到普通回复。
- **人设话术**:`wrap_tool_talk` 给工具结果套魔理沙语气(天才/だぜ/借书梗)。
- **配置**:`conf.yaml`/`.env`/env 的 `AMAP_KEY/AMAP_ADCODE/EXCHANGE_API/DICT_API`(默认空=禁用)。

---

## 3. 【P2】好感度系统(F-I) —— 按用户记录好感,影响回复倾向 —— ✅ 已实现(`FEATURE_FAVOR`)

### 设计动机
每个用户有自己的好感值,对话/教学/在线时长加分,status 可见,高好感解锁专属回答倾向
(由 P4 的 `favor:*` flag 消费)。不直接改 answer 文本,而是为打分选择器提供好感维度。

### 落地情况
- **表** `favorability`(uid 主键,score/talk_count/teach_count/active_seconds/level/last_active_at)。
- **常量**(service.py 顶部):`FAVOR_SCORE_TALK=1 / FAVOR_SCORE_TEACH=3 / FAVOR_SCORE_ACTIVE=1(每60s)`、
  `FAVOR_PRAISE_WORDS`(夸奖词命中 bonus+2)、`FAVOR_LEVELS`(冷淡/普通/好感/亲密/恋慕)。
- **业务**:`_touch_favor`(主键 upsert,level 由 score 映射回写),调用点为 reply 命中(talk=1)、
  add 成功(teach=1)、/Active 心跳;`_favor_score/_favor_level` 供选择器与 flag 查询。
- **接口** `POST /Favor`、`POST /Active`(每 uid 每分钟限 1 次,防挂机刷分)。
- **前端**:心跳 `setInterval(Core.active, 60s)`;status 显示好感等级与分数。
- 已接受的取舍:uid 是 cookie 可清(匿名系统本质);好感只是统计,失败不影响主流程。

---

## 4. 【P3】话题感知(theme) —— 多句对话判断话题,话题内精准回答 —— ✅ 已实现(`FEATURE_THEME`)

### 设计动机
不再对每次输入孤立地"全局随机":结合最近几轮主题词,话题内优先回相关记忆。
核心思路:把"随机选择"升级为**打分选择器**,同一处逻辑被 theme/favor/flag 复用,
避免三个特性各自改匹配逻辑互相打架。

### 落地情况
- **内存态** `self._context`(uid/ip -> 最近输入分词 deque,`maxlen=8`;键数 >1 万时丢最旧,
  防内存膨胀);重启丢失可接受(话题是瞬时上下文)。
- **主题词提取** `_topic_tokens`:词频 >=2 或 top5,复用 `SYNTAX_STOPWORDS` 剔虚词。
- **打分选择器** `_pick_candidate(candidates, topic_tokens, favor_score)`:
  `主题契合*0.5 + min(favor,10)/10*0.3`,全 0 时回退 `random.choice`(与旧版行为一致);
  `FEATURE_THEME` 关闭时同样直接随机,零差异。

---

## 5. 【P4】对象判断(flag) —— 相同的话,对不同人/好感/时间给不同回答 —— ✅ 已实现(`FEATURE_FLAG`)

### 设计动机
教学时给记忆挂条件:只对某个人/某时段/某好感阶段回——"私密台词"和"分时段台词"。

### 落地情况
- **列** `memorise.flag`(`all` 默认 / `user:<uid>` / `favor:high|medium|low` / `time:dawn|day|dusk|night`,v1 单条件);
  `MemoryEntry.flag` 同步进索引;旧数据 NULL 视为 all。
- **教学格式扩为三段反引号**:`关键词`回答`flag`(@ 前缀兼容);两段旧格式行为完全不变。
- **判定** `_flag_matches`(异步,favor 判定查库):all 恒 True;user 精确匹配 uid;
  favor 按 level 映射(low<=1/medium=2/high>=3);time 按 `FLAG_TIME_WINDOWS`(night/dawn 跨午夜特殊处理)。
- **候选分组**:flagged 有命中则从中选,否则回落 unflagged(普通回答不受影响);
  全部候选带条件且不满足时按未命中处理(不漏别人的私密台词)。深夜催睡优先级最高。
- 风险已记录:uid 可自报,`user:<uid>` 只是"匿名定向",不是权限。

---

## 6. 【P5】多人协作教学(maid)+ 调教师权限 —— ✅ 已实现(`FEATURE_MAID`)

### 设计动机
多用户共同教同一只魔理沙(同关键词多回答天然多候选随机,协作已隐含在匹配里);
新增"调教师"能删任意条目、屏蔽恶意教学者,半开放管理,不做复杂 RBAC。

### 落地情况
- **列/表**:`memorise.uid`(教学者留痕);`teacher`、`blocked_user` 两张权限表;
  `MASTER_UID` 配置首个调教师(**权限只信服务端配置,不信客户端自报**)。
- **业务**:`_is_master`(env 或 teacher 表)、`_is_blocked`;`add()` 限流后查屏蔽
  (`400 你被禁止教学了`);`forget(answer, requester_uid)` 增强——非调教师只能删自己教的行,
  历史 NULL 数据仅调教师可删。
- **接口** `POST /Block`(block/unblock)、`POST /Admin/Delete`。
- 未做(原方案标可选,至今无需求):`POST /Grant` 二次授权接口、操作日志。

---

## 7. 【P6】代码执行支持 —— teach logic 里写简单脚本(AST 白名单)—— ✅ 已实现(`FEATURE_CODE`)

### 设计动机
logic 分类回答里嵌 `{1+2*3}` 这类表达式,回复时动态求值(条件式回答),同时严格防注入。
**全项目安全敏感度最高的点**:白名单必须保守。

### 落地情况
- **safe_eval.py**:`ast.parse(mode="eval")` + 节点白名单(常量/算术/比较/布尔/IfExp/受限 Name),
  硬禁 Call/Attribute/Subscript/Import/Assign/循环/lambda/推导式;命名空间清空 builtins,
  只放 `SAFE_NAMES`;返回限标量,异常返回 None;受限 AST 天然有界,无需超时。
  后续加固:**拒绝字符串参与算术**(封 `'a'*10**9` 内存炸弹)。
- **渲染** `service._render_template`:仅 `category == 'logic'` 时把 `{表达式}` 替换为求值结果,
  单段失败保留原文;模板文本照常过违禁词 + AI 审核。
- 纯 stdlib `ast`,零新依赖。

---

## 8. 【P7】生产部署实测 —— MySQL 真机 + gunicorn 多进程一致性 —— ⬜ 待实施

### 目标与用户价值
把"能跑"变成"能部署":MySQL 模式真机验证、多进程下内存索引一致性方案文档化,消除上线拦路石。

### 数据模型/接口改动
无业务改动。这是运维与验证方案。

### MySQL 模式实测步骤
1. 依赖:`cd server-py && uv pip install asyncmy pymysql`(业务会话走 asyncmy;启动迁移的同步引擎走 pymysql)。
2. `conf.yaml`(或 `.env`)配置:`DB_TYPE=mysql`、`DB_HOST/DB_PORT/DB_USER/DB_PASSWORD/DB_NAME`(参考 `config.py`)。
3. 启动:`main.py` 的 `on_startup` 钩子会走 `database.run_migrations()` 自动建表/补列(含 blacklist 与新表)。
4. 验证清单:
   - 中文写入/读出无乱码(`charset=utf8`);
   - 跑 `server-py/test_api.py`(BASE 指向 MySQL 实例)34 项全绿;
   - 并发写(多 IP 教学)确认 `pool_pre_ping=True` 生效;
   - 重启后数据保留、索引重建正常(`svc.reload()`)。
5. 已知差异:`_ADD_COLUMNS` 是 MySQL ALTER 幂等,SQLite 老库补列走同一 dict,双库保持一致。

### gunicorn 多进程内存索引一致性方案
现状:内存倒排索引是**进程内态**(`MemoryIndex`,service.py),多进程各持一份,写不互通。

| 方案 | 做法 | 一致性 | 依赖 |
|------|------|--------|------|
| **A 单进程(默认推荐)** | `gunicorn -w 1 -k uvicorn.workers.UvicornWorker`(或直接 uvicorn / `python server.py`) | 强一致 | 无 |
| **B 多进程写路由** | `--workers=N`,但只有 1 个 worker 处理 `/Add`(环境变量 `IS_WRITER=1` 或启动参数),其余只读;写入 worker 更新自己索引后,reader 定时调 `POST /Reload`(secret=`REVIEW_SECRET`)重建索引(如 60s 一次) | 最终一致(秒级延迟) | 无 |
| **C 加 Redis 广播(可选)** | 写操作发布 pub/sub,worker 订阅刷新,近实时 | 近实时 | Redis(违背零依赖,仅大流量才选) |

推荐:**默认方案 A**(SQLite 也最配单进程);需要吞吐时用 **方案 B** 并配合:
- nginx:`/api` 反代到 gunicorn,`/Add` 可单独路由到 writer 节点;
- `/Status`、`/Categories` 走任一 reader,延迟期间数据略旧可接受;
- 写入热点只在 writer,单点故障靠 supervisor 重启兜底。

### 压力/回归清单
- 批量 seed(39 条,`seed_qa.py`)在 MySQL 上成功;
- 100 并发 Reply 无 5xx、限流正常(每 IP 30/min);
- 深夜催睡、违禁词、审核(pending→approved)在双库行为一致;
- gunicorn 方案 B 下,Add 后 ≤60s reader 可答出(Reload 周期内)。

### 依赖与风险
- MySQL 需装 asyncmy(异步业务会话)+ pymysql(同步迁移);gunicorn 是 Linux 部署(Windows 本机 dev 仍用 uvicorn)。风险:方案 B 的写路由实现要避免多 writer 同时写(必须只有一个 writer);Reload 全量重建在数据量增大后耗时上升(当前量级无压力,预留阈值告警即可)。

---

## 9. 实施顺序(历史记录)

P0-P6 已按此顺序在 2026-08 完成(P0/P1 于 2026-08-06;P2-P6 于 feature switches 大提交,
统一 `FEATURE_*` 开关默认 off);P7 压轴未做,但 MySQL 启动/迁移验证可随时提前跑,不阻塞开发。

| 顺序 | 特性 | 前置依赖 | 状态 |
|------|------|----------|------|
| 1 | **P0 待学习清单** | 无 | ✅ 2026-08-06 |
| 2 | **P1 资讯工具(离线)** | 无 | ✅ 2026-08-06(天气/词典/汇率/人设话术后续增强) |
| 3 | **P2 身份层 + 好感度** | 身份层(第 0 节) | ✅ feature switches 提交 |
| 4 | **P3 话题感知** | 身份层 | ✅ 同上 |
| 5 | **P4 对象判断 flag** | 身份层 + 好感度(level) | ✅ 同上 |
| 6 | **P5 多人协作 + 权限** | 身份层 | ✅ 同上(Grant 接口未做) |
| 7 | **P6 代码执行** | 无 | ✅ 同上(safe_eval 后续安全加固) |
| 8 | **P7 部署实测** | 全部 | ⬜ 待实施 |

**核心思路(事后看依然成立)**:
1. 先做无依赖、低成本、可见价值高的(P0/P1),快速建立信心;
2. 身份层先行,它是一切"按用户维度"特性的地基;
3. 同一个"打分选择器"承载 theme/favor/flag,避免各自改匹配逻辑互相打架;
4. 安全敏感/权限敏感的放后(代码执行、调教师权限),核心逻辑冻结后再收尾;
5. 部署实测压轴。

---

## 附:落地情况一览(按文件,as-built)

| 文件 | 现状 |
|------|------|
| `server-py/models.py` | 表 `Memorise`(含 `flag`/`uid` 列)/`Blacklist`/`MissKeyword`/`Favorability`/`Teacher`/`BlockedUser` |
| `server-py/database.py` | 异步引擎(`AsyncSessionLocal`,aiosqlite)+ 同步引擎(启动迁移);`_ADD_COLUMNS` 幂等补列;MySQL 分支建全部新表 |
| `server-py/config.py` | `_DEFAULTS` 含 `EXCHANGE_API`/`DICT_API`/`AMAP_KEY`/`AMAP_ADCODE`/`MASTER_UID`/`FEATURE_*`;`load_yaml`(conf.yaml)与异步连接串 |
| `server-py/service.py` | 全部公开方法协程;uid/flag 贯通;miss 落库与 resolve;`_pick_candidate` 打分选择器;`_touch_favor`/`_is_master`/`_flag_matches`;`_render_template` |
| `server-py/safe_eval.py` | AST 白名单求值器(禁 eval/exec/字符串算术) |
| `server-py/tools.py` | 资讯工具注册表(全异步 aiohttp;离线时间/计算器 + 可插拔天气/汇率/词典)+ 人设话术 |
| `server-py/review.py` | DeepSeek 审核客户端(aiohttp 异步共享会话) |
| `server-py/main.py` | 15 个路由(含 `Misses`/`Favor`/`Active`/`Block`/`Admin/Delete`),全协程;`create_app()` 装配 |
| `server-py/server.py` | `WebMarisaServer`(conf.yaml + 装配 + run)统一入口 |
| `server-py/conf.example.yaml` | 配置模板(`conf.yaml` 本地外部配置,gitignore) |
| `client/src/core/identity.ts` | cookie UUID 生成/读取 |
| `client/src/core/index.ts` | 请求带 `uid`;`misses/favor/active/block/adminDelete`;三段反引号 teach |
| `client/src/api/index.ts` | 对应新接口与字段 |
| `client/src/views/chatroom.vue` | `miss` 指令、status 好感展示、心跳、flag 教学三段、指令面板 |
