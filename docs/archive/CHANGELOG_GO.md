# web-marisa 后端重构 CHANGELOG

重构日期:2026-08-05
范围:仅 `server/` 后端,前端 `client/` 与数据库结构 `Datasource/marisa_memorise.sql` 均未改动。

## 一、技术栈变更

| 项目 | 重构前 | 重构后 |
| ---- | ------ | ------ |
| Web 框架 | kataras/iris v11 + hero 依赖注入 | gin-gonic/gin v1.9.1(去掉 hero) |
| Go 版本 | go 1.12 | go 1.21 |
| ORM | jinzhu/gorm v1.9.8 | jinzhu/gorm v1.9.16(保留,表结构兼容) |
| 分词 | huichen/sego | huichen/sego(保留原实现,`Config/dictionary.txt` 路径不变) |
| 配置 | go-ini | gopkg.in/ini.v1(同库,新模块路径) |
| MySQL 驱动 | go-sql-driver/mysql v1.4.1 | go-sql-driver/mysql v1.7.1 |

## 二、分层目录结构

```
server/
├── main.go                # 组装启动:加载配置 -> 连库 -> 依赖注入 -> gin 路由 -> 监听端口
├── Config/
│   ├── config.ini         # 配置(未改,数据库密码保持为空)
│   └── dictionary.txt     # sego 词典(未改)
├── Datasource/
│   └── marisa_memorise.sql # 建表脚本(未改)
├── config/  (package config)   # 读取 Config/config.ini(gopkg.in/ini.v1)
├── database/ (package database)# MySQL 连接、gorm 初始化(表名单数 SingularTable)
├── model/   (package model)    # Memorise 实体(含 memoryId 主键)
├── segment/ (package segment)  # sego 分词封装(原实现平移)
├── repository/ (package repository) # gorm 数据访问层
├── service/ (package service)  # 业务逻辑:分词、匹配、去重合并
├── handler/ (package handler)  # HTTP 层:读 form、写 ModelAndView JSON
└── Routes/  (package routes)   # gin 路由注册
```

说明:由于 Windows 文件系统不区分大小写,`Config/`、`Routes/` 目录名沿用旧名(其中还含有 `config.ini`/`dictionary.txt` 等数据文件),Go 包名保持小写 `config`/`routes`,导入路径分别为 `server/Config`、`server/Routes`,与磁盘目录名一致,保证 Linux 上也能正常编译。

## 三、修复的 bug

1. **`Add()` 合并逻辑(goto DATA bug)**:原实现内层 `for keyword` 循环每次迭代都 `goto DATA`,导致只用第一个 keyword 做重合判断,合并形同虚设。重写为完整遍历已有词条、计算全量重合度,`>= 60%` 时用 `mergeUnique()` 把两个分词集合**去重合并**(先旧后新、保持顺序)后入库。
2. **`Reply()` 命中后错误反查**:原实现命中后用 `FetchMemory(answer)` 按 answer 反查数据库,可能返回错误/空数据。改为直接返回已匹配词条 `v.Answer`。
3. **`Reply()` 恒假分支与无意义循环**:清理了 `0 >= 0.4` 的恒假分支和无意义循环,统一用 `overlapRatio()` 计算重合度,`>= 40%` 即命中。
4. **数据库写入不生效(gorm 传值 bug)**:仓库层 `Create(memory)` 传了**值**而非指针,触发 gorm `ErrUnaddressable`(自增主键无法回写),导致整个事务被回滚,教学内容实际存不进库。改为 `Create(&memory)`,并检查 `Error` 返回值。

## 四、API 兼容性

全部接口保持 POST + form-urlencoded,HTTP 状态恒为 200,业务码在 JSON `code` 字段:
- `POST /` → `{"code":200,"message":"hello Marisa~"}`(同时支持 GET,方便探活)
- `POST /Add`(form: ip, keyword, answer)→ `{"code":200,"data":{ip,keyword,answer}}`
- `POST /Reply`(form: keyword)→ 命中 `{"code":200,"data":{"answer":"..."}}`;未命中 `{"code":10001,"data":{"answer":"唔嗯...不懂你在说什么呢...教教我吧~"}}`
- `POST /Forget`(form: answer)→ `{"code":200,"data":"success"}`
- `POST /Status` → `{"code":200,"data":<记忆条数>}`

## 五、文件变更清单

新增:
- `config/config.go`(原 `Middlewares/setting/setting.go` 迁移)
- `database/db.go`(原 `Datasource/db.go` 迁移,去掉对 iris 的依赖)
- `model/memorise.go`(原 `Models/memorise.go` 迁移,新增 `memoryId` 主键字段)
- `segment/segment.go`(原 `Middlewares/segment/segment.go` 迁移)
- `service/memoriseService.go`(原 `Services/memoriseService.go` 重写)
- `handler/handler.go`(原 `Controllers/*` 重写为 gin handler)
- `test_api.py`(接口验证脚本)
- `CHANGELOG.md`(本文档)

重写/修改:
- `main.go`(iris → gin 组装)
- `Routes/routes.go`(iris+hero → gin 路由)
- `repository/memoriseRepo.go`(去掉废弃的 `FetchMemory`,修复 Create 传指针)
- `go.mod`(go 1.21,去掉 iris 全家桶,引入 gin)

删除:
- `Controllers/`、`Services/`、`Models/`、`Middlewares/`(内容已迁移到新分层)
- `Datasource/db.go`、`Datasource/.gitkeep`(`marisa_memorise.sql` 保留)

## 六、编译与运行

编译(生成 `server.exe`):

```bash
cd D:\projects\web-marisa\server
export PATH=/d/tools/go/bin:$PATH
export GOPROXY=https://goproxy.cn,direct
go build -o server.exe .
```

运行(需本地 MySQL/MariaDB,库 `webmarisa`,root 空密码,监听 :3000):

```bash
cd D:\projects\web-marisa\server
./server.exe
```

验证(另开终端,依赖 python 与 MariaDB 客户端,测试后自动清理数据):

```bash
cd D:\projects\web-marisa\server
python test_api.py
```

测试覆盖:GET/POST `/`、Add 教学、Reply 命中/未命中、Add 去重合并(bug#1)、Forget、Status,共 10 项全部通过。

## 七、备注

- 数据库表 `memorise` 的 `memoryId/ip/keyword/answer` 结构未变。
- 原 `build.sh` / `build_windows.bat` / `build_linux.bat` 保留可用。
- 前端 `client/` 未做任何改动。
