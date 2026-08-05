# web-marisa 后端 Python 重写任务(FastAPI + SQLAlchemy + jieba)

## 项目背景
web-marisa 是 2019 年的东方 Project"白丝魔理沙"聊天机器人。当前结构:
- 后端:D:\projects\web-marisa\server\(Go + gin,刚重构过,跑在 127.0.0.1:3000)——本次要重写为 Python
- 前端:D:\projects\web-marisa\client\(Vue3 + Vite,跑在 127.0.0.1:8888,dev proxy 把 /api 转发到 127.0.0.1:3000)——基本不动,只有一处小修改(见下)
- 数据库:本地 MariaDB 10.6,127.0.0.1:3306,root 空密码,库 webmarisa,表 memorise

机器人原理:用户用 `关键词`回答` 格式"教学"(存进 MySQL),提问时后端把输入用中文分词,和库里的关键词词条比对,重合率达到阈值就返回对应回答。纯检索式聊天机器人,没有 AI 模型。

**新后端代码写到 D:\projects\web-marisa\server-py\ 目录(新建),不要动 server/ 里的 Go 代码。** 最后用户会决定旧的 Go 版怎么处理。

## 技术栈
- Python 3.11(本机 C:\Users\Koman\AppData\Local\Programs\Python\Python311\python.exe)
- **注意:本机没有 pip!包管理必须用 uv**(uv 已安装)。流程:`uv venv` 建虚拟环境,`uv pip install fastapi uvicorn sqlalchemy pymysql jieba` 装依赖
- FastAPI + uvicorn + SQLAlchemy + pymysql + jieba 分词
- 虚拟环境建在 server-py/.venv

## API 契约(必须完全兼容,前端依赖这个,不能改)
全部 POST + form-urlencoded,返回 JSON {code, data},HTTP 状态恒为 200(业务码在 JSON 里):
- POST /          -> {"code":200,"message":"hello Marisa~"}(GET 也要支持,方便探活)
- POST /Add       form: ip, keyword, answer -> 教学,成功 {"code":200,"data":{"ip":...,"keyword":...分词结果,"answer":...}}
- POST /Reply     form: keyword -> 命中 {"code":200,"data":{"answer":"..."}};未命中 {"code":10001,"data":{"answer":"唔嗯...不懂你在说什么呢...教教我吧~"}}
- POST /Forget    form: answer -> {"code":200,"data":"success"}
- POST /Status    无参数 -> {"code":200,"data":<记忆条数>}
- 兼容性:前端发的是 application/x-www-form-urlencoded,字段名 ip/keyword/answer 大小写敏感,必须一致

## 数据库
- 现有表 memorise(memoryId 主键自增, ip varchar(15), keyword text, answer text),库里有几条数据,不能丢
- 用 SQLAlchemy ORM,连接串:mysql+pymysql://root:@127.0.0.1:3306/webmarisa?charset=utf8
- **保留现有表结构和数据**,新增列(ALTER TABLE,写成启动时自动执行的 migration 逻辑):
  - raw_keyword TEXT —— 用户教学时的原始关键词(未分词),用于精确匹配优先
  - hit_count INT DEFAULT 0 —— 被命中的次数
  - created_at DATETIME, updated_at DATETIME
- 表名单数(SingularTable 行为),model 类名 Memorise

## 业务逻辑(重写 + 改进,这是重点)

### 教学 Add
1. jieba 分词(加载时 jieba.initialize(),可自定义词典不用做)
2. **输入校验(新增)**:keyword 和 answer 都必须非空;keyword 去掉首尾空白后长度 1-50;answer 长度 1-500;超限返回 {"code":400,"data":"参数不合法..."} 并说明原因
3. **合并逻辑改进(修复原 bug)**:原逻辑是"新词与已有词条重合度 >= 60% 就合并",太激进(会把语义不同的句子合并)。改为:**只有当新分词集合完全包含于某条已有记忆的分词集合时(子集),才把新词合并进去**;否则新增一条记忆。合并时用有序去重
4. raw_keyword 存用户原始输入;hit_count=0;created_at/updated_at 写当前时间
5. 教学后更新内存索引(见下)

### 回复 Reply
1. 先拿用户原始输入,精确匹配:如果某条记忆的 raw_keyword 与输入完全相等,直接返回该条(权重最高)
2. 否则 jieba 分词,查内存倒排索引找到所有命中的记忆(分词交集),计算重合度 >= 40% 的候选集合
3. **命中多条时随机选一条(新增,原实现固定返回第一条)**,并把该条 hit_count +1
4. 未命中返回固定话术(与原来一致)
5. **未命中关键词记录(新增)**:内存里记录最近 50 个未命中的输入词(带时间),供以后可能做"待学习清单",不需要落库,不需要暴露接口(注释说明即可)

### Forget
- 按 answer 精确删除,行为与原来一致(可删除多条同 answer 的记录,返回 success)

### Status
- 返回当前记忆总条数

### 性能改进(新增)
- **内存倒排索引**:启动时把全表加载成 dict 映射 分词 -> 记忆ID列表;Add 时增量更新;Reply 时直接查索引而不是全表扫描。注意:索引是内存态,启动时从库重建,Add/Delete 时同步维护。数据量小,不用担心一致性(单进程)

### 防滥用(新增,轻量)
- 内存版限流:每个 IP 每分钟最多 10 次 Add(教学),用简单 dict + 时间戳实现,超过返回 {"code":429,"data":"教学太频繁了,休息一下吧~"}(HTTP 仍是 200,业务码 429)

## 前端小修改(唯一允许动的地方)
- D:\projects\web-marisa\client\src\views\chatroom.vue 第 15 行:`v-html="item.content"` 是存储型 XSS 漏洞(内容来自用户教学输入,可注入 HTML/脚本)。改为安全渲染:`v-text` 或插值 `{{ item.content }}`(消息内容按纯文本显示)
- 其他前端文件一律不动

## 运行方式
- 启动:`cd D:\projects\web-marisa\server-py && .venv\Scripts\python -m uvicorn main:app --host 127.0.0.1 --port 3100`(注意用 3100 端口验证,因为 3000 被旧 Go 版占着;验证完成后用户会切换)
- 配置文件:server-py/.env 或 config.ini(数据库连接、端口),默认值:root 空密码 @127.0.0.1:3306/webmarisa

## 完成标准
1. uvicorn 在 3100 端口启动成功,日志无错误
2. 写一个 test_api.py 验证:GET/POST /、Status、Add(含校验拒绝:空 keyword/超长 answer)、Reply 命中(精确匹配优先)、Reply 未命中、Add 合并(子集合并 vs 非子集新增)、Forget、Status 回归、限流(连续 11 次 Add 第 11 次返回 429)、命中随机性(教两条同义词,Reply 多次应出现不同答案或至少不报错)——全部通过
3. 数据库现有数据保留(重写后 Status 条数 >= 重写前)
4. 输出 CHANGELOG_PYTHON.md:改了哪些、新功能、与 Go 版的差异
5. 代码全部中文注释

## 禁止事项
- 不要动 D:\projects\web-marisa\server\(Go 版保留原样)
- 不要动 client/ 除了上面指定的 v-html 那一处
- 不要用 pip(没有),一律 uv
- 不要改数据库已有数据的值
- 不要把端口写成 3000(验证用 3100)

开始吧。先读 server/ 的 Go 代码理解原逻辑,再在 server-py/ 里写 Python 版。
