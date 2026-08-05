# web-marisa 后端重构任务(Go)

## 项目背景
web-marisa 是 2019 年的东方 Project"白丝魔理沙"聊天机器人。当前后端在 D:\projects\web-marisa\server\,技术栈:Go 1.12 + iris v11 框架 + gorm + MySQL,前端是 Vue2(本次不动前端)。

机器人原理:用户用 `关键词`回答` 格式"教学"(存进 MySQL),提问时后端把输入用 sego 中文分词,和库里的关键词词条比对,重合率 >= 40% 就返回对应回答。这是一个纯检索式聊天机器人,没有 AI 模型。

## 重构目标(保持技术栈,只重构后端)
把 server 目录从 iris 迁移到 **gin**,整理分层架构,修复已知 bug,保持 API 完全兼容。

### API 必须保持不变(前端靠这几个接口活着)
- POST / —— 返回 JSON {"code":200,"message":"hello Marisa~"}
- POST /Add —— form 参数 ip, keyword, answer;分词合并后入库;返回 {"code":200,"data":{ip,keyword,answer}}
- POST /Reply —— form 参数 keyword;返回 {"code":200,"data":{"answer":"..."}} 或未命中时 {"code":10001,"data":{"answer":"唔嗯...不懂你在说什么呢...教教我吧~"}}
- POST /Forget —— form 参数 answer;按 answer 删除;返回 {"code":200,"data":"success"}
- POST /Status —— 返回 {"code":200,"data":<记忆条数>}
- 所有接口都是 POST + form-urlencoded,返回值是 ModelAndView{Code, Data} 结构(Code 200 成功,10001 是业务上"没答上"但 HTTP 200)

### 必须修复的 bug
1. memoriseService.go Add() 里内层 for 循环每次迭代都 goto DATA,导致只用第一个 keyword 做合并判断——重写合并逻辑(两个分词集合去重合并)
2. Reply() 里命中后用 FetchMemory(answer) 按 answer 查库的怪逻辑——直接返回已找到的 v.Answer 即可
3. Reply() 里 ratio 计算有 0 >= 0.4 的恒假分支和无意义循环——清理
4. Config/config.ini 的数据库密码是空的(本地 MariaDB root 无密码),库名 webmarisa,不要改回 123456

### 分层架构要求
- main.go:组装启动
- routes:gin 路由注册
- handler:HTTP 层(读 form、写 JSON 响应)
- service:业务逻辑(分词、匹配、合并)
- repository:gorm 数据访问
- model:Memorise 结构
- segment:sego 分词(保留原实现,Config/dictionary.txt 路径不变)
- config:ini 配置读取(保留 go-ini,或迁移到环境变量+默认值,选你觉得合理的)

### 技术约束
- 用 gin 替换 kataras/iris,去掉 hero 依赖注入
- go.mod 用 go 1.21(本机 Go 1.21.13)
- 保留 gorm(或升级到 gorm.io/gorm 也行,但要保证表结构兼容:SingularTable 行为,表名 memorise,字段 memoryId/ip/keyword/answer)
- 保留 sego 分词(huichen/sego + cedar-go)
- 数据库连接串格式保持 mysql driver 兼容:user:pass@tcp(host:3306)/webmarisa?charset=utf8&parseTime=True&loc=Local
- 编译命令:cd D:\projects\web-marisa\server && export PATH=/d/tools/go/bin:$PATH && export GOPROXY=https://goproxy.cn,direct && go build -o server.exe .
- 禁止改前端 client/ 目录任何文件
- 禁止改数据库结构(marisa_memorise.sql 不动)

### 完成标准
1. go build 编译通过,生成 server.exe
2. 你写一个简短的 curl/python 测试脚本验证 4 个接口(教学、回复命中、回复未命中、忘记、状态)行为与原来一致
3. 输出一份 CHANGELOG.md 说明改了哪些文件、修了哪些 bug

开始动手吧。先读现有代码理解逻辑,再重构。所有代码注释和输出用中文。
