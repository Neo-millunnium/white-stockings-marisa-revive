# CHANGELOG_FRONTEND_V8

前端依赖升级任务(Vite 8 + stylus → sass)完成记录。

## 改动文件清单

### 依赖与配置
| 文件 | 改动 |
|---|---|
| `client/package.json` | 依赖升级与替换(见下方版本对照) |
| `client/package-lock.json` | npm install 自动更新 |

### 样式文件(stylus → sass)
| 旧文件 | 新文件 | 说明 |
|---|---|---|
| `client/src/assets/css/variable.styl` | `client/src/assets/css/_variables.scss` | 变量定义改为 sass `$x: v;` 语法;下划线前缀便于 `@use` |
| `client/src/assets/css/base.styl` | `client/src/assets/css/base.scss` | html/body 白底 `#ffffff`、sans-serif |
| `client/src/assets/css/fonts.styl` | `client/src/assets/css/fonts.scss` | 空占位,仅保留注释 |
| `client/src/assets/css/index.styl` | `client/src/assets/css/index.scss` | 入口 `@import` → `@use` |
| `client/src/assets/css/reset.styl` | `client/src/assets/css/reset.scss` | reset 规则转 sass 花括号语法 |

### 源码引用
| 文件 | 改动 |
|---|---|
| `client/src/main.ts` | css 入口改为 `import './assets/css/index.scss'` |
| `client/src/views/chatroom.vue` | `<style lang="stylus" scoped>` → `<style lang="scss" scoped>`;stylus 缩进式语法全部转为 sass 花括号/分号/冒号语法;`@import '../assets/css/variable'` → `@use '../assets/css/variables' as *` |

## 版本对照(升级前 → 升级后)
| 包 | 升级前 | 升级后 | 说明 |
|---|---|---|---|
| vite | 5.4.21 | **8.2.0** | |
| @vitejs/plugin-vue | 5.2.1 | **6.0.8** | peer 支持 vite ^8 |
| typescript | 5.9.3 | 5.9.3 → 尝试 7.0.2 → **回落 5.9.3** | TS 7.0.2 与 vue-tsc 3.3.9 不兼容(见下) |
| vue-tsc | 2.1.10 | **3.3.9** | peer: typescript >=5.0.0 |
| vue | 3.5.41 | **3.5.41(未动)** | 按任务要求保持 |
| stylus | 0.64.0 | **移除** | |
| sass | — | **1.102.0** | 替换 stylus |

### TypeScript 7 回落说明
`typescript@7.0.2` 是 Go 原生重写版,包结构移除了 `./lib/tsc` 子路径导出。`vue-tsc` 通过 `require.resolve('typescript/lib/tsc')` 加载编译器,直接报 `ERR_PACKAGE_PATH_NOT_EXPORTED`。按任务预案回退到 5.9.x(最新 5.x),`vue-tsc`/`vite` 均正常。`vite@8` 本身与 TS7 兼容,问题仅在 vue-tsc。

## stylus → sass 语法迁移说明
- **变量**:stylus `$box-color = #cccc99` → sass `$box-color: #cccc99;`(冒号 + 分号)。
- **规则块**:stylus 缩进式(可省略花括号/冒号/分号)→ sass 必须 `{ }` + `:` + `;`。
- **嵌套与 `&`**:stylus `& input[name='you']` / `&:hover` 写法 sass 完全一致,仅需补花括号。
- **模块引入**:`@import '../assets/css/variable'` → `@use '../assets/css/variables' as *`(用 `as *` 去掉命名空间,变量名保持 `$box-color` 等不变)。sass 已弃用 `@import`,改用 `@use`/`@forward`。
- **颜色变量**:原 stylus 变量都是 hex,直接照搬,无 rgba()/颜色类型问题。

## 验证结果
1. ✅ `npm install` 成功;`package.json` 中 vite ^8、sass 替换 stylus。
2. ✅ `npm run type-check` 通过(vue-tsc 3.3.9 零错误)。
3. ✅ `npm run build` 成功,dist 正常产出。
4. ✅ dev server 8888(host 0.0.0.0)实测:
   - 页面 `GET /` 200。
   - `POST /api/Status` 经代理转发到 3000,返回 `{"code":200,"data":5}`,与直连后端一致(代理去 `/api` 前缀正常)。
   - 编译后 CSS 关键色值逐一比对:
     - html/body `background: #ffffff`(白底)✅
     - `.chatroom` `background: #cccc99`、`border: 1px solid #022c60`,尺寸 712×512 ✅
     - 输入框 `background: #ffffff` ✅
     - `.avatar` `background-image: url(.../marisa.jpg)`、`background-size: 333px`、`background-position: 58% 2%` ✅
     - `.you_color` `color: #4876FF !important` ✅
   - 生产构建 CSS 中 `#ffffff` 压缩为 `#fff`、`#cccc99` 压缩为 `#cc9`,值等价,视觉不变。

## 未改动项(保持)
- `client/vite.config.ts`:`/api` 代理到 `127.0.0.1:3000` + 去前缀 rewrite、8888 端口、0.0.0.0 全部保留。
- `client/src/api/index.ts`、`client/src/core/index.ts`:API 契约不变。
- chatroom.vue 第 15 行仍为 `v-text` 安全渲染(未改回 `v-html`)。
- `server/`(Go)、`server-py/`(Python)均未触碰。
