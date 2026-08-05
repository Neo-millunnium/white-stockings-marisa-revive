# web-marisa 前端(重写版)

白丝魔理沙聊天室前端,已从 Vue 2.6 + vue-cli(webpack)重写为 **Vue 3 + Vite + TypeScript**。
功能与视觉保持旧版不变:深色背景的复古像素聊天室、角色立绘、`teach` / `forget` / `status` 教学指令、右侧系统指令说明面板。

## 技术栈

- Vue 3.5(Composition API,`<script setup>`)
- Vite 5
- TypeScript 5
- Stylus(视觉还原旧版)

## 目录结构

```
client/
├── index.html                 # 入口 HTML
├── vite.config.ts             # dev 端口 8888 + /api 代理
├── package.json
├── src/
│   ├── main.ts                # 应用入口,引入全局样式
│   ├── App.vue                # 直接渲染聊天室(单页,无路由)
│   ├── api/index.ts           # 后端 API 封装(POST + form-urlencoded)
│   ├── core/index.ts          # 魔理沙核心逻辑(回复/教学/忘记/状态)
│   ├── assets/
│   │   ├── css/               # 全局样式:reset / base / 像素字体
│   │   └── fonts/PressStart2P.ttf
│   └── views/
│       ├── chatroom.vue       # 聊天室组件
│       └── img/marisa.jpg     # 角色立绘(保留自旧版)
```

## 开发

```bash
npm install
npm run dev
```

- dev server 跑在 **http://127.0.0.1:8888**
- 需要先启动后端(见下),否则对话无响应。
- 开发环境请求 `/api/xxx`,Vite 会把 `/api` 前缀去掉后代理到 `http://127.0.0.1:3000/xxx`(见 `vite.config.ts` 的 `server.proxy`),因此**开发时没有跨域问题**。

## 构建

```bash
npm run build
```

产物输出到 `client/dist/`(Vite 每次构建会清空旧产物)。

- 构建产物的 API 基础地址默认是相对路径 **`/api`**,所以需要把 `dist/` 交给一个能把 `/api` 转发到后端的 Web 服务(见下"生产环境怎么配")。
- 也可以用环境变量在构建时指定后端绝对地址:

```bash
# Windows PowerShell
$env:VITE_API_BASE_URL="http://127.0.0.1:3000"; npm run build
# git-bash
VITE_API_BASE_URL=http://127.0.0.1:3000 npm run build
```

> 直接使用绝对地址时,浏览器会跨域请求后端,**后端必须开启 CORS**,见下。

## 后端(不要改动,这里只说明怎么起和怎么配 CORS)

### 后端怎么起

后端是 Go + gin,代码在 `../server/`,跑在 `127.0.0.1:3000`,依赖本机 MariaDB/MySQL(`127.0.0.1:3306`,库名 `webmarisa`)。

```bash
cd ../server
./server.exe        # 已编译好的 Windows 可执行文件
# 或
go run .
```

> 注意:必须在 `server/` 目录下启动(它按相对路径 `Config/config.ini` 读配置)。
> 探活:`curl http://127.0.0.1:3000/` 应返回 `{"code":200,"message":"hello Marisa~"}`。

### 生产环境 /api 怎么配(反向代理)

后端接口目前挂在根路径(`/Add`、`/Reply`、`/Forget`、`/Status`),没有 `/api` 前缀。
生产环境请用反向代理把 `/api/*` 转发到后端并去掉前缀。以 nginx 为例:

```nginx
server {
    listen 80;
    root /path/to/web-marisa/client/dist;
    index index.html;

    location /api/ {
        proxy_pass http://127.0.0.1:3000/;   # 去掉 /api 前缀
        proxy_set_header Host $host;
    }

    # SPA 单页,无路由,可不配 fallback;若要保留 history 路由再补 try_files
}
```

### 如果要用绝对地址直连后端,需要给 gin 加 CORS

不改后端代码的前提下,以上代理方案就够了。若坚持让前端直接请求 `http://127.0.0.1:3000/`,需要在 `server/main.go` 的 `gin.Default()` 之后、`routes.Register(r, h)` 之前加 CORS 中间件,例如:

```go
import "github.com/gin-contrib/cors"

r := gin.Default()
// 允许前端源(开发时是 http://127.0.0.1:8888;生产按实际源调整)
r.Use(cors.New(cors.Config{
    AllowOrigins:     []string{"http://127.0.0.1:8888"},
    AllowMethods:     []string{"POST", "GET", "OPTIONS"},
    AllowHeaders:     []string{"Origin", "Content-Type"},
    AllowCredentials: true,
}))
```

依赖需要在 `server/go.mod` 增加 `github.com/gin-contrib/cors`。

## 后端 API 契约(前端按此调用,不可改)

所有接口 `POST` + `application/x-www-form-urlencoded`,返回 `JSON {code, data}`:

| 接口      | 参数 form                | 成功                              | 未命中                           |
| --------- | ------------------------ | --------------------------------- | -------------------------------- |
| `/Add`    | `ip`, `keyword`, `answer` | `{code:200, data:{ip,keyword,answer}}` | —                                |
| `/Reply`  | `keyword`                | `{code:200, data:{answer}}`        | `{code:10001, data:{answer}}`    |
| `/Forget` | `answer`                 | `{code:200, data:"success"}`       | —                                |
| `/Status` | (无)                     | `{code:200, data:<条数>}`          | —                                |

前端用 `URLSearchParams` 构造 body(见 `src/api/index.ts`),不要用 JSON body。

## 验证脚本

后端接口可用性可用纯标准库脚本验证(需要后端已启动):

```bash
python verify_api.py   # 或用仓库根目录 server/test_api.py
```

## 与旧版差异说明

- 技术栈:`Vue 2.6 + webpack` → `Vue 3 + Vite + TS`,Composition API,去掉 vuex/vue-router/axios(单页无需路由,请求改用 fetch)。
- 视觉:完整保留旧版布局与配色(`#cccc99` 窗口、`#f5f7ea` 面板、`#022c60` 描边/文字);按任务要求把页面背景改为深色(`#0a1628`),并让 `PressStart2P` 像素字体生效在指令名上(`teach/forget/status/application`)。
- 修复:旧版 `.talk-place` 的 `width: clac(...)` 是无效值,已改为 `calc(100% - 10px)`。
- 教学成功/失败现在真实等待后端结果(旧版因 `if (Promise)` 恒真,失败也会显示"行,我知道了")。
- `teach` 的 `关键词`回答`(反引号分隔)产品逻辑不变。
