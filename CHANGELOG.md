# Changelog

本项目的变更记录。格式参考 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/)。

## [2026-08-06]

### Added

- **待学习清单(miss 指令)**:未命中关键词落库聚合(`miss_keyword` 表,归一化去重/计数/首次与最近时间),被问过 >=2 次且未学会的通过 `POST /Misses` 展示;教学 AI 审核**通过时**自动标记已解决(精确或分词子集判定),从清单隐藏;前端 `miss` 指令逐条引导教学
- **资讯工具**:正则触发,优先级为精确匹配(用户教学)> 工具 > 分词匹配;内置离线工具——时间/日期(现在几点/今天日期/星期几)、计算器(二元运算,`safe_eval` 白名单求值,除零/超大数回落兜底话术);可插拔在线工具(天气/汇率/百科,`WEATHER_API`/`EXCHANGE_API`/`DICT_API` 配置非空才注册,urllib+5s 超时,失败静默降级)
- **safe_eval.py**:AST 白名单安全求值器(仅放行数值/字符串常量、算术/比较/布尔运算,禁 Call/Attribute/Subscript/Import/循环/lambda/推导式,命名空间清空 builtins,结果限标量且超大数拦截)——为计算器与后续代码执行功能共用
- **防分布式注入(换 IP 多来源攻击)**:三层内存防线,均随服务重启清零
  - **全局教学限流**:全站所有来源合计每分钟最多 100 次,防多 IP 打爆审核队列与数据库
  - **回答内容指纹去重**:回答文本归一化(去空白/标点/转小写)后 sha256,10 分钟窗口内同一回答被 >=5 个不同 IP 或 >=20 次总提交即拒绝(防批量刷库)
  - **IP 信誉**:30 分钟窗口内教学被拒 >=5 次且成功 <3 次的 IP,临时拉黑教学 30 分钟
- **AI 内容审核(先审后玩)**:教学内容提交后进入待审队列(`review_status=pending`),不立即生效;每小时由 DeepSeek 批量审核(每批最多 10 条,`_review_loop`),审核通过才进内存索引生效,被拒的回答原文进 `blacklist` 表(同回答再次教学直接拒绝)。`POST /Review`、`POST /Reload` 可手动触发(secret=`REVIEW_SECRET`)
- **违禁词正则前处理**:`server-py/banned_words.txt` 配置正则词库,教学回答命中违禁词直接拒绝 + 拉黑,不进待审队列
- **回复限流**:每 IP 每分钟 30 次回复(独立于教学限流的 10 次/分),前端请求携带 ip
- **教学分类体系(teach 指令)**:恢复 2010 年原始 QQ 调教 bot 的 `teach word / sentence / syntax / logic / greeting` 分门别类教学,新增 `POST /Categories`、`POST /Greeting` 接口
  - `auto` 自动判定分类(问候词表命中 -> greeting,否则 -> word)
  - greeting 语义:单条问候语(开场白),非问答对——存储时清空 keyword/分词/raw_keyword,只保留 answer,不参与问答匹配
  - 各分类独立匹配阈值:word 0.4 / sentence 0.3 / logic 0.2(提到即答)/ syntax 0.4(剔除语气虚词)/ greeting 0.4 + 精确匹配优先;旧数据 NULL 视为未分类,保持 0.4 与原版一致
- **数据库迁移**:SQLite 老库启动时自动补列,MySQL/MariaDB 启动时对缺失列执行幂等 `ALTER TABLE`(含黑名单表),`DB_TYPE=mysql` 无缝升级

### Changed

- 教学限流维持 10 次/分;回复新增独立限流 30 次/分;全站教学合计新增 100 次/分上限
- 审核 key 读取顺序:环境变量 `DEEPSEEK_API_KEY` -> `server-py/.env` 兜底,解析容错(容忍空格、跳过注释)

### Fixed

- 审核 `review.py` 的 .env 解析与 `config.py` 一致(容忍空格、跳过注释、无导入顺序依赖)
- review 文档注释修正:审核为每小时批次,非每日 4 点

## [2026-07-31 之前(重构基线)]

2026 年本地现代化重构完成前的历史变更见 git 历史(baseline commit 为原项目 zip 快照,包含 2018-2019 原网页版与 2010 原始调教 bot 的玩法传承)。
