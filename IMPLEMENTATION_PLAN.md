# web-marisa 萌娘回路(Moec Core)未实现能力 · 实现方案

> 本文档只做方案设计,不修改任何业务代码。所有改动点均引用现有代码的实际文件与行号,
> 并严格遵守现有 API 契约(POST + form-urlencoded,返回 `{code, data}`,HTTP 恒 200)。
> 产品逻辑铁律不变:未命中兜底话术、深夜催睡、AI 先审后玩、反注入限流、`关键词`回答` 反引号格式。

---

## 0. 公共基础设施:匿名身份层(uid)

特性 3/4/5/6 都依赖"按用户维度记录状态"。当前接口只带 `ip`(前端 `core/index.ts:14` `getIp()` 拿公网 IP,失败回退 `127.0.0.1`)。NAT/移动网络下 IP 会串号,不能可靠标识一个人,所以需要一个**匿名 cookie UUID + IP 双标识**的基础层,先于这些特性落地。

- **前端**:新增 `client/src/core/identity.ts`:
  - `getUid()`:读 `localStorage['marisa_uid']`,无则 `crypto.randomUUID()` 生成并回写(现代浏览器原生支持,零新依赖);`getIp()` 保持现状作为第二标识。
  - 所有请求(Add/Reply/Active/Favor/Misses/Block...)统一附带 `uid`;后端 `reply()`/`add()` 签名加 `uid: str = ""`(缺省空串,老客户端兼容,后端回退用 `ip` 做 key)。
- **后端**:所有按用户状态(`self._context`、favorability、blocked)以 `uid` 为主键、`ip` 为兜底。`uid` 是客户端自报的,只当"匿名身份",不当"可信权限"——**调教师权限必须用服务端 env 配置判定**(见特性 6),绝不信任客户端自声明的身份。

---

## 1. 【P0】待学习清单 —— 展示"没教会的话"并引导教学

### 目标与用户价值
把"别人问了但魔理沙答不出"的关键词展示给用户,引导 ta 去 teach,补全"教-答-不会-教"的核心玩法闭环。

### 现状
`service.py:272` `self.recent_misses = deque(maxlen=MISS_LOG_MAX)` 已记录最近 50 条 `(time, kw)`,在 `reply()` 未命中处 `service.py:527` `self.recent_misses.append((time.time(), kw))` 写入,**仅内存、不聚合、不落库、无接口**。

### 数据模型改动(models.py)
新增表 `miss_keyword`,把"最近未命中"升级为"聚合的待学习清单"(可跨重启、可标记已学会):

```python
class MissKeyword(Base):
    __tablename__ = "miss_keyword"
    id = Column("id", Integer, primary_key=True, autoincrement=True)
    keyword = Column("keyword", Text, nullable=False, unique=True)  # 未命中的原始输入
    miss_count = Column("miss_count", Integer, nullable=False, default=1)
    first_seen = Column("first_seen", DateTime, nullable=True)
    last_seen = Column("last_seen", DateTime, nullable=True)
    resolved_at = Column("resolved_at", DateTime, nullable=True)  # 学会时间;NULL = 待学
```

- SQLite:`database.py:57` `create_all` 自动建新表,无需改动。
- MySQL:`database.py:48` `run_migrations()` 的 MySQL 分支需仿照 blacklist(`database.py:72-76`)补一条 `CREATE TABLE IF NOT EXISTS miss_keyword (...)`。
- `service.recent_misses` 保留不动(兼容现有测试/语义),只作为"最近"的瞬时缓冲。

### 接口改动(main.py)
新增 1 个只读路由(契约:POST + form,返回 `{code, data}`):

```python
@app.post("/Misses")
def misses():
    """待学习清单:返回未学会(且被问过 >=2 次)的未命中关键词,按次数降序。"""
    return svc.misses()
```

返回示例:`{code:200, data:{list:[{keyword:"魔法使是怎么飞", count:3, last_seen:"..."}, ...]}}`。无需鉴权(它只是引导教学的提示,不是隐私数据);全局限流可复用 `_check_reply_rate` 防刷。

### 业务逻辑要点(service.py)
1. 新增 `_miss_upsert(kw)`:在 `reply()` 未命中分支(`service.py:527`)追加 `recent_misses` 的同时,对 `miss_keyword` 表做 upsert(count+1、更新 last_seen);内存里维护 `self._miss_stats` dict 兜底查询(表为准)。
2. 新增 `_resolve_misses(entry)`:在**审核通过**时调用——`review_pending()`(`service.py:629`)的 approved 分支(`service.py:668-675`)里,对每一条通过的记忆,把 `miss_keyword` 中满足以下任一条件的行置 `resolved_at=now`:
   - `miss.keyword == entry.raw_keyword`(精确);或
   - `set(cut_keyword(miss.keyword)) ⊆ set(entry.tokens)`(分词子集,与合并逻辑 `service.py:456` 同一哲学)。
   > 关键:必须在**审核通过**时 resolve,而不是 `add()` 时——pending 未生效,若审核被拒,该关键词其实仍没学会,不能从清单隐藏。
3. 新增 `misses()`:查表,过滤 `resolved_at IS NULL AND miss_count >= 2`,按 miss_count 降序返回前 N 条(常量 `MISS_LIST_MAX=20`)。

### 前端改动点(client/)
- `chatroom.vue:113` `marisaThinking` 的 switch 加 `case 'miss':` → 新增 `marisaMiss()`:调 `Core.misses()`,逐条展示,如 `有人问过「魔法使是怎么飞」3 次没答上,试试 teach 魔法使是怎么飞`回答`~`。
- `core/index.ts` 加 `misses(): Promise<{keyword,count,last_seen}[] | null>`;`api/index.ts:46` 的 `api` 对象加 `misses: () => request('/Misses')`。
- 右侧指令面板(`chatroom.vue:27-44`)加一行 `miss 查看待学习清单`。

### 依赖与风险
- 无新依赖。风险:中文关键词归一化(去空白/标点)后再存,避免"xx "和"xx"重复计数;resolve 的"分词子集"判定是启发式,可能误标(用户教的词不完全等同原输入)——可接受,清单本义是"提示方向"。

---

## 2. 【P1】资讯工具 —— 时间/计算器/天气等正则触发

### 目标与用户价值
用户问"现在几点""1+2"这类即时资讯,魔理沙直接给出动态答案,不再回兜底话术(参考 Moec Core 资讯层:天气/百科/词典/计算器/汇率)。

### 数据模型改动
无(纯函数,不落库)。

### 接口改动(main.py)
不新增路由,集成进现有 `/Reply`。可选加一个 `POST /Tools` 调试接口(返回工具清单与命中情况),非必需。

### 业务逻辑要点(service.py + 新文件 tools.py)
1. 新增 `server-py/tools.py`:`TOOL = (name, compiled_regex, handler(keyword) -> Optional[str])`,`match_tool(kw)` 遍历返回第一个非空结果。
2. **内置离线工具(零依赖,默认启用)**:
   - **时间/日期**:正则 `(现在|几点|什么时间|日期|几号|星期|年月日)`,handler 返回 `datetime.now()` 格式化(`现在是 2026-08-06 14:23 星期四`)。
   - **计算器**:正则 `^\s*(-?\d+(?:\.\d+)?)\s*([+\-*/])\s*(-?\d+(?:\.\d+)?)\s*$`,调用特性 7 的 `safe_eval.safe_eval`(同一白名单求值器,禁裸 eval);除法除零、超大数返回 None(落到普通回复)。
3. **可选在线工具(可插拔,默认关闭,规避 CERNET 外网不稳)**:
   - **天气** `(天气|气温|温度)` → 调 `WEATHER_API`(env 配置,如 wttr.in/和风),`urllib` + 5s 超时(仿 `review.py:72-82`),失败返回 None 落到普通回复。
   - **汇率** `(汇率|美元|欧元|日元)` → `EXCHANGE_API`;同样可插拔。
   - **百科/词典** `(什么是|意思|释义)` → `DICT_API`,默认给搜索链接兜底。
   - `config.py:11` `_DEFAULTS` 加 `WEATHER_API/EXCHANGE_API/DICT_API`(默认空=禁用)。
4. **挂载点**:`service.reply()`(`service.py:495`),在空输入校验(`service.py:503-506`)之后、精确匹配(`service.py:509`)之前插一段:
   ```python
   tool_ans = match_tool(kw)
   if tool_ans:
       return {"code": 200, "data": {"answer": tool_ans}}
   ```
   优先级设计:**精确匹配(用户显式教过,权重最高)→ 资讯工具(无人定制时的智能兜底)→ 分词重合匹配**。理由:用户教过的"现在几点"应该赢过工具;但没教过时工具比随机重合更实用。
   > 深夜催睡在 `reply()` 最顶部(`service.py:497-499`)已拦截,工具不会在催睡时段触发,符合产品逻辑。

### 前端改动点(client/)
- 无强制改动(工具只影响返回文本)。可选在指令面板(`chatroom.vue:27`)加一句"试试:现在几点 / 1+2"。
- `client/core/index.ts` 的 `reply()` 无需改,工具命中返回的就是 `data.answer`。

### 依赖与风险
- 离线工具零依赖;在线工具复用 `urllib`(不加 requests)。风险:在线工具在校园网超时/不稳——**默认关闭**,配置了才启用;计算器正则只支持二元运算,多目/括号表达式走特性 7 的模板求值,不在本特性范围。

---

## 3. 【P2】好感度系统(F-I) —— 按用户记录好感,影响回复倾向

### 目标与用户价值
每个用户(匿名 cookie UUID)有自己的好感值,对话/教学/在线时长都会加分,status 里能看到"好感:普通 128分",高好感解锁部分特殊回答倾向(由特性 5 flag 消费)。

### 数据模型改动(models.py)
新增表 `favorability`:

```python
class Favorability(Base):
    __tablename__ = "favorability"
    uid = Column("uid", String(64), primary_key=True)   # cookie UUID 主标识
    ip = Column("ip", String(15), nullable=True)        # 最近来源 IP(辅助/兜底)
    score = Column("score", Integer, nullable=False, default=0)
    talk_count = Column("talk_count", Integer, nullable=False, default=0)
    teach_count = Column("teach_count", Integer, nullable=False, default=0)
    active_seconds = Column("active_seconds", Integer, nullable=False, default=0)
    level = Column("level", Integer, nullable=False, default=0)  # 派生,由 score 映射
    last_active_at = Column("last_active_at", DateTime, nullable=True)
```

- MySQL:仿 blacklist(`database.py:72-76`)补 `CREATE TABLE IF NOT EXISTS favorability (...)`。

### 接口改动(main.py)
- `POST /Add`(`main.py:80`)与 `POST /Reply`(`main.py:89`)各加 `uid: str = Form("")`,透传给 service。
- 新增:
  - `POST /Favor`(uid, ip)→ `{code:200, data:{uid, score, level, level_name, talk_count, teach_count, active_seconds}}`。
  - `POST /Active`(uid, ip, seconds)→ 心跳上报在线秒数,累计并加分;**每 uid 每分钟限 1 次**(复用 `_check_rate_limit`,防挂机刷分)。

### 业务逻辑要点(service.py)
1. 常量(顶部,仿 `service.py:37-43` 反注入常量的写法):
   ```python
   FAVOR_SCORE_TALK = 1        # 每次成功对话 +1
   FAVOR_SCORE_TEACH = 3       # 每次教学成功 +3
   FAVOR_SCORE_ACTIVE = 1      # 每 60s 在线 +1(心跳合并计)
   FAVOR_PRAISE_WORDS = ("喜欢", "可爱", "厉害", "棒", "最好", "谢谢")  # 内容加分
   FAVOR_LEVELS = [(0, "冷淡"), (50, "普通"), (150, "好感"), (300, "亲密"), (500, "恋慕")]
   ```
2. 新方法 `_touch_favor(uid, ip, talk=0, teach=0, active_seconds=0, bonus=0)`:upsert(SQLite/MySQL 跨库简单做法:先 query,存在则 update,否则 add;数据量小无并发压力)。`level` 由 score 按 `FAVOR_LEVELS` 映射回写。
3. 调用点:
   - `reply()` 命中分支(`service.py:530-532`):`talk=1`;若 `kw` 命中 `FAVOR_PRAISE_WORDS` 任一子串则 `bonus=2`。
   - `add()` 成功返回前(`service.py:482`):`teach=1`。
   - `POST /Active`:`active_seconds += seconds`,并按 `seconds // 60 * FAVOR_SCORE_ACTIVE` 加分。
4. **影响回复倾向**:不直接改 answer 文本,而是为 `reply()` 的选择器提供 `favor` 打分维度(见特性 4 的"打分选择器"):高好感时优先选"高好感专属"词条(flag=favor:*),使回复倾向随好感变化。

### 前端改动点(client/)
- `identity.ts`(见第 0 节)提供 `getUid()`。
- `core/index.ts`:`reply/teach` 请求带 `uid`;加 `favor(uid)`、`active(uid, seconds)`。
- `api/index.ts`:对应字段与两个新接口。
- `chatroom.vue`:
  - `onMounted`(`chatroom.vue:271`)起 `setInterval(() => Core.active(uid, 60), 60_000)` 心跳。
  - `marisaStatus`(`chatroom.vue:221`)加调 `Core.favor(uid)`,在脑重量后显示 `（好感:普通 128 分）`。

### 依赖与风险
- 依赖身份层(第 0 节)。风险:uid 是 cookie,可清空重刷——接受(匿名系统本质);心跳刷分——靠 `/Active` 限流缓解;表行数随用户增长,SQLite 无压力(单表主键 upsert)。

---

## 4. 【P3】话题感知(theme) —— 多句对话判断话题,话题内精准回答

### 目标与用户价值
不再对每次输入孤立地"全局随机"选答案:结合最近几轮对话的主题词,在特定话题内优先回与主题相关的记忆,让连续对话更有上下文。

### 数据模型改动
无新表。内存态即可:`self._context = defaultdict(deque)`(`uid` → 最近输入分词队列,`maxlen=8`)。重启丢失可接受(话题本就是瞬时上下文)。

### 接口改动(main.py)
`POST /Reply` 加可选 `uid`(见第 0 节);无新路由。

### 业务逻辑要点(service.py)
1. `reply()` 入口拿 `key = uid or ip`,维护 `self._context[key]`(deque,`maxlen=8`),每次推入 `cut_keyword(kw)`。
2. **主题词提取**:把队列里所有输入分词做词频统计,取出现 >=2 次或 top-N(N=5)的词,构成 `topic_tokens`(复用 `SYNTAX_STOPWORDS`(`service.py:72`)剔除"的/吗"等虚词)。
3. **把"随机选择"升级为"打分选择器"**(为特性 5/6 复用,同一处逻辑):
   ```python
   def _pick_candidate(self, candidates, topic_tokens, favor_score):
       if not candidates:
           return None
       scored = []
       for e in candidates:
           s = 0.0
           s += overlap_ratio(e.tokens, topic_tokens) * 0.5   # 主题契合
           s += min(favor_score, 10) / 10 * 0.3               # 好感偏好
           scored.append((s, e))
       best = max(scored, key=lambda x: x[0])
       # 有主题/好感得分差异时取最高,否则(全 0)回退全局随机,保持现有"随机选一条"行为
       if best[0] > 0:
           return best[1]
       return random.choice(candidates)
   ```
   替换 `service.py:530` 的 `chosen = random.choice(candidates)`。
4. 同步改造 `service.py:514-524` 的候选收集段,把 `entry`、`topic_tokens`、`favor_score`(查 `favorability` 表)传入选择器。

### 前端改动点(client/)
- `reply()` 带 `uid`(第 0 节)。无其他强制改动。

### 依赖与风险
- 依赖身份层。风险:主题词提取是朴素词频,可能被闲聊轮次污染——用虚词过滤 + `maxlen=8` 缓解;`self._context` 的 key 数量会随用户增长,需按 `last_active` 定期清理(内存上限,如 >10k 时清最旧)。

---

## 5. 【P4】对象判断(flag) —— 相同的话,对不同人/好感/时间给不同回答

### 目标与用户价值
教学时给一条记忆挂条件:只对某个人回、或只在某时段回、或只对高好感用户回——实现"私密台词"和"分时段台词"(对应 Moec Core 的 flag 对象判断)。

### 数据模型改动(models.py)
`Memorise` 加一列:

```python
flag = Column("flag", String(32), nullable=True, default="all")
```

取值:`all`(默认)/ `user:<uid>` / `favor:high|medium|low` / `time:dawn|day|dusk|night`。v1 只支持单一条件。
- `database.py:26` `_ADD_COLUMNS` 加 `"flag": "ALTER TABLE memorise ADD COLUMN flag VARCHAR(32) NULL DEFAULT 'all'"`(SQLite 老库补列与 MySQL ALTER 都走这个 dict,见 `database.py:61-64`)。
- `MemoryEntry`(`service.py:123`)加 `flag` 字段;`_to_entry`(`service.py:156`)、`MemoryIndex.rebuild/add`(`service.py:143/165`)同步带上。

### 接口改动(main.py)
- `POST /Add` 加 `flag: str = Form("all")`。
- 教学格式扩展为**三段反引号**:`关键词`回答`flag`。前端 `core/index.ts:61` 已是 `content.split('`')`,天然支持三段,只需把 `parts[2]` 作为 flag 传入,两段时行为完全不变(兼容旧格式)。

### 业务逻辑要点(service.py)
1. `add()`(`service.py:381`):校验 `flag` 合法值(不合法返回 `400`,仿分类校验 `service.py:400-402`),存入行。
2. 新增 `_flag_matches(entry, uid, now)`:
   - `all` → True;
   - `user:<uid>` → `uid` 匹配才 True;
   - `favor:*` → 查 `favorability.level` 映射(low=0-1, medium=2, high>=3);
   - `time:*` → 按 `FLAG_TIME_WINDOWS = {'dawn':(5,0,8,0),'day':(8,0,18,0),'dusk':(18,0,22,0),'night':(22,0,5,0)}` 判断当前时间(注意 night/dawn 跨午夜)。
3. `reply()` 候选选择:把候选分成 `flagged` 与 `unflagged` 两组——先看 flagged 里有无 `_flag_matches` 命中的,有则从中选(过打分选择器);否则回落到 unflagged(保证带条件的词条不命中时,普通回答不受影响)。
4. 优先级:深夜催睡(`service.py:497-499`)是最高产品逻辑,flag 时间窗口在其之后判断,催睡时段不回任何 flag 内容。

### 前端改动点(client/)
- `core/index.ts` `teach()` 解析第三段反引号传 flag;`api/index.ts` `add()` 带 `flag`。
- 指令面板(`chatroom.vue:27-44`)提示:`关键词`回答`@time:night` 只在晚上回、`关键词`回答`@user:<uid>` 只对那个人回。

### 依赖与风险
- 依赖身份层 + 好感度(level 映射)。风险:uid 可自报,`user:<uid>` 只是"匿名定向",不是权限;教学格式三段扩展需保证两段旧格式零回归(测试用例覆盖)。

---

## 6. 【P5】多人协作教学(maid)+ 调教师权限 —— 共同完善 + 半开放管理

### 目标与用户价值
多用户共同教同一只魔理沙:同关键词不同回答天然多候选随机(协作已隐含在现有匹配里);新增"调教师"能删任意条目、屏蔽/解除屏蔽特定人,实现半开放管理。

### 数据模型改动(models.py)
`Memorise` 加教学者列:

```python
uid = Column("uid", String(64), nullable=True)  # 教学者 cookie UUID(留痕,用于权限)
```

新增两张权限表:

```python
class Teacher(Base):
    __tablename__ = "teacher"
    uid = Column("uid", String(64), primary_key=True)
    role = Column("role", String(16), nullable=False, default="master")
    created_at = Column("created_at", DateTime, nullable=True)

class BlockedUser(Base):
    __tablename__ = "blocked_user"
    uid = Column("uid", String(64), primary_key=True)
    blocked_by = Column("blocked_by", String(64), nullable=True)
    created_at = Column("created_at", DateTime, nullable=True)
```

- `database.py:26` `_ADD_COLUMNS` 加 `"uid": "ALTER TABLE memorise ADD COLUMN uid VARCHAR(64) NULL"`;MySQL 分支补建 `teacher`、`blocked_user` 表。
- `config.py:11` `_DEFAULTS` 加 `MASTER_UID: ""`(首个调教师 uid,env 配置;**权限只信这个配置,不信客户端自报**)。

### 接口改动(main.py)
- `POST /Add` 带 `uid`(记录教学者)。
- 新增:
  - `POST /Block`(uid, target_uid, action="block"|"unblock")→ 仅 `uid == MASTER_UID` 或 uid 在 `teacher` 表;把 target_uid 写入/删除 `blocked_user`。
  - `POST /Admin/Delete`(uid, answer)→ 仅调教师可删任意条目(调增强后的 `service.forget`)。
  - (可选)`POST /Grant`(uid, target_uid)→ 调教师授予对方 teacher。

### 业务逻辑要点(service.py)
1. `add()` 限流之后(`service.py:394` 之后)加 blocked 检查:uid 在 `blocked_user` 表 → 返回 `400 "你被禁止教学了"`(仿黑名单分支 `service.py:417-419`)。
2. 新增 `_is_master(uid)`:`uid == config.get("MASTER_UID") or uid in teacher 表`,结果缓存在内存。
3. `forget()`(`service.py:554`)加可选 `requester_uid`:非调教师只能删 `uid == requester_uid` 的行(兼容:历史数据 uid 为 NULL 时仅调教师可删);`/Forget` 路由(`main.py:98`)带 `uid`。
4. **协作编辑说明**:多人教同关键词 = 现有多候选随机(`service.py:530`)已天然成立;子集合并(`service.py:454-458`)维持。调教师的增强是删冗余/屏蔽恶意教学,不做复杂 RBAC(半开放)。

### 前端改动点(client/)
- teach/forget 请求带 `uid`;`core/index.ts` 加 `block()`、`adminDelete()`;`api/index.ts` 对应接口。
- 指令面板(`chatroom.vue:27-44`)加 `block <uid>` / `delete <answer>`(仅当本地 uid == MASTER_UID 时渲染,避免普通用户误用)。

### 依赖与风险
- 依赖身份层。风险:权限体系是"半开放"而非强认证,uid 可伪造——**master 判定只信服务端 env 配置**;`teacher` 表是二次授权,被 master 授予的 teacher 也可能乱删,靠 `/Block` 撤回 + 操作日志(可选)缓解。

---

## 7. 【P6】代码执行支持 —— teach logic 里写简单脚本(必须 AST 白名单)

### 目标与用户价值
教学 `logic` 分类时,回答里可嵌 `{1+2*3}`、`{now}` 这类表达式,回复时动态求值——实现"条件式回答",同时严格防注入(禁裸 eval/exec)。

### 数据模型改动
无新表/新列。脚本以模板文本形式存 `answer`(经 AI 审核,违规模板会被拒/拉黑)。

### 接口改动(main.py)
无新路由。`/Add`、`/Reply` 逻辑不变。

### 业务逻辑要点(新文件 safe_eval.py + service.py)
1. 新增 `server-py/safe_eval.py`,实现 `safe_eval(expr)`:
   - `tree = ast.parse(expr, mode="eval")`。
   - **节点白名单**:`ast.Expression, ast.Constant, ast.BinOp, ast.UnaryOp, ast.Compare, ast.BoolOp, ast.IfExp, ast.Name`,以及运算符子集(`Add/Sub/Mult/Div/FloorDiv/Mod`、`USub/UAdd/Not`、比较符)。
   - **硬性禁止**:`Call`、`Attribute`、`Subscript`、`Import`、`Assign`、循环、lambda、推导式、f-string——遍历 AST 校验,任一非白名单节点直接拒绝(返回 None)。
   - 求值命名空间:内置为空,只放 `SAFE_NAMES = {'now': lambda: datetime.now().strftime(...), 'hours': ..., 'pi': 3.14159}`;`eval(compile(tree, '<safe>', 'eval'), {'__builtins__': {}}, SAFE_NAMES)`。
   - 返回类型仅允许 `str/int/float/bool`;全程 try/except,异常返回 None。受限 AST 无循环/递归,天然有界,无需超时。
2. `service.reply()`:在 `chosen` 选出后(`service.py:530-532`)加 `_render_template(answer)`:
   - 正则 `\{([^{}]+)\}` 找占位符 → `safe_eval` 求值 → 替换;某段求值失败则整段保留原文(回落到字面答案)。
   - 仅当 `chosen.category == 'logic'` 时渲染,其他分类原样返回。
3. 教学侧无额外校验(模板文本走 `match_banned`(`service.py:115`)+ AI 审核,违规自然被拦)。

### 前端改动点(client/)
- 指令面板(`chatroom.vue:27-44`)加说明:"logic 分类回答里可用 {1+2} / {now}";无代码改动。

### 依赖与风险
- 纯 stdlib `ast`,零新依赖。**这是全项目安全敏感度最高的点**:白名单必须保守(禁一切 Call/Attribute/Import),命名空间清空 builtins,返回值限标量;`safe_eval` 必须独立成模块并被 `test_api.py` 式单测覆盖(测试:合法表达式、恶意代码 `__import__('os')`、`().__class__` 等必须被拒)。

---

## 8. 【P7】生产部署实测 —— MySQL 真机 + gunicorn 多进程一致性

### 目标与用户价值
把"能跑"变成"能部署":MySQL 模式真机验证、多进程下内存索引一致性方案文档化,消除上线拦路石。

### 数据模型/接口改动
无业务改动。这是运维与验证方案。

### MySQL 模式实测步骤
1. 依赖:`cd server-py && uv pip install pymysql`。
2. `.env` 配置:`DB_TYPE=mysql`、`DB_HOST/DB_PORT/DB_USER/DB_PASSWORD/DB_NAME`(参考 `config.py:11-21`)。
3. 启动:`main.py` 的 `lifespan`(`main.py:53-66`)会走 `database.run_migrations()`(`database.py:48`)自动建表/补列(含 blacklist 与新表)。
4. 验证清单:
   - 中文写入/读出无乱码(`charset=utf8`,`config.py:53`);
   - 跑 `server-py/test_api.py`(BASE 指向 MySQL 实例)29 项全绿;
   - 并发写(多 IP 教学)确认 `pool_pre_ping=True`(`database.py:17`)生效;
   - 重启后数据保留、索引重建正常(`svc.reload()` `service.py:276`)。
5. 已知差异:`_ADD_COLUMNS` 是 MySQL ALTER 幂等(`database.py:26-33`),SQLite 老库补列走同一 dict,双库保持一致。

### gunicorn 多进程内存索引一致性方案
现状:内存倒排索引是**进程内态**(`service.py:7-9` 注释 + `MemoryIndex` `service.py:135`),多进程各持一份,写不互通。

| 方案 | 做法 | 一致性 | 依赖 |
|------|------|--------|------|
| **A 单进程(默认推荐)** | `gunicorn -w 1 -k uvicorn.workers.UvicornWorker`(或直接 uvicorn) | 强一致 | 无 |
| **B 多进程写路由** | `--workers=N`,但只有 1 个 worker 处理 `/Add`(环境变量 `IS_WRITER=1` 或启动参数),其余只读;写入 worker 更新自己索引后,reader 定时调 `POST /Reload`(`main.py:142`,secret=`REVIEW_SECRET`)重建索引(如 60s 一次) | 最终一致(秒级延迟) | 无 |
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
- MySQL 需装 pymysql;gunicorn 是 Linux 部署(Windows 本机 dev 仍用 uvicorn)。风险:方案 B 的写路由实现要避免多 writer 同时写(必须只有一个 writer);Reload 全量重建在数据量增大后耗时上升(当前量级无压力,预留阈值告警即可)。

---

## 9. 建议实施顺序(总览与理由)

| 顺序 | 特性 | 用户价值/成本 | 前置依赖 | 理由 |
|------|------|---------------|----------|------|
| 1 | **P0 待学习清单** | 高/低 | 无 | 数据已部分在收集(`service.py:527`),只差聚合+接口+前端,一个版本内可完成,直接补全"教-答-不会-教"闭环 |
| 2 | **P1 资讯工具(离线)** | 高/低 | 无 | 时间/计算器零依赖,正则拦截即可用;天气/汇率/词典做成可插拔默认关闭,不阻塞上线 |
| 3 | **P2 身份层 + 好感度** | 中/中 | 身份层(第 0 节) | 身份层是 theme/favor/flag/maid 共同前置;好感先上给用户可见反馈(status 显示),并为 flag 提供维度 |
| 4 | **P3 话题感知** | 中高/中 | 身份层 | 把 `reply()` 的随机选择升级为打分选择器,同一处逻辑为 flag 铺路 |
| 5 | **P4 对象判断 flag** | 中/中高 | 身份层 + 好感度(level) | 依赖 level 映射;教学格式扩为三段反引号,需保证两段零回归 |
| 6 | **P5 多人协作 + 权限** | 中/高 | 身份层 | 权限体系敏感,UI 改动多,放核心玩法稳定后 |
| 7 | **P6 代码执行** | 低中/中 | 无 | 独立 `safe_eval` 模块,安全敏感需单测覆盖,放后期集中收尾 |
| 8 | **P7 部署实测** | 中/中 | 全部 | 最后做全量验证;MySQL 与多进程方案先文档化,可随时提前执行 |

**核心思路**:
1. **先做无依赖、低成本、可见价值高的**(1、2),快速建立信心与用户体验;
2. **第三条先落地身份层**,它是一切"按用户维度"特性的地基;
3. **同一个"打分选择器"承载 theme/favor/flag**,避免三个特性各自改匹配逻辑互相打架(改 `service.py:519-532` 一处即可);
4. **安全敏感/权限敏感的放后**(代码执行、调教师权限),等核心逻辑冻结后再收尾;
5. **部署实测压轴**,但文档化的 MySQL 启动/迁移验证可随时提前跑,不阻塞开发。

---

## 附:改动点一览(按文件)

| 文件 | 改动 |
|------|------|
| `server-py/models.py` | 新表 `MissKeyword`/`Favorability`/`Teacher`/`BlockedUser`;`Memorise` 加 `flag`、`uid` 列 |
| `server-py/database.py` | `_ADD_COLUMNS` 加 `flag`/`uid`;MySQL 分支补建 4 张新表(仿 blacklist `database.py:72-76`) |
| `server-py/config.py` | `_DEFAULTS` 加 `WEATHER_API`/`EXCHANGE_API`/`DICT_API`/`MASTER_UID` |
| `server-py/service.py` | `reply()`/`add()` 加 `uid`、flag;miss 记录落库;打分选择器;`_touch_favor`/`_is_master`/`_flag_matches`;`_render_template` |
| `server-py/safe_eval.py`(新) | AST 白名单求值器(禁 eval/exec) |
| `server-py/tools.py`(新) | 资讯工具注册表 + 离线/可插拔 handler |
| `server-py/main.py` | 新路由 `Misses`/`Favor`/`Active`/`Block`/`Admin/Delete`(可选 `Grant`/`Tools`);`Add`/`Reply`/`Forget` 加 `uid` 字段 |
| `client/src/core/identity.ts`(新) | cookie UUID 生成/读取 |
| `client/src/core/index.ts` | 请求带 `uid`;新增 `misses/favor/active/block/adminDelete` |
| `client/src/api/index.ts` | 对应新接口与字段 |
| `client/src/views/chatroom.vue` | `miss` 指令、status 好感展示、心跳、flag 教学三段、调教师指令面板 |
