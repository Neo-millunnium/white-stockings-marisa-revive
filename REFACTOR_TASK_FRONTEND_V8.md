# web-marisa 前端依赖升级任务(Vite 8 + 去 stylus)

## 项目背景
web-marisa 前端在 D:\projects\web-marisa\client\,当前是 Vue 3.5 + Vite 5 + TypeScript 5 + stylus。本次任务:**升级到 2026 年当代版本,并把 stylus 换成 sass**。功能、视觉、API 契约一律不变。

当前版本(实测):
- vite 5.4.21(旧,Vite 8.2.0 是当前最新)
- @vitejs/plugin-vue 5.2.1(最新 6.0.8)
- typescript 5.9.3(最新 7.0.2——注意 TS 7 是大版本跳,如果插件不兼容就留在 5.x 最新,选能编译过的组合)
- vue-tsc 2.1.10(最新 3.3.9)
- vue 3.5.41(保持,不要动)
- stylus 0.64.0(停更多年,替换目标:sass 1.102.0)

## 目标版本(以能编译通过为准,允许小调整)
- vite ^8(如果 vite 8 有兼容问题,退 vite 7 也行,但优先 8)
- @vitejs/plugin-vue 与 vite 配套的最新版
- typescript:尝试 7;若 vue-tsc 3.x 或 vite 8 不兼容 TS7,回退到 5.9.x
- vue-tsc 与 typescript 配套
- vue 保持 3.5.x 不动
- stylus -> sass(@use/@forward 语法,注意 sass 弃用 @import,用新版模块语法)

## stylus 改造范围(全部要转成 sass)
1. src/assets/css/variable.styl -> _variables.scss(sass 变量 $xxx,注意 stylus 变量赋值是 `=`,sass 是 `$x: v;`,函数/混合宏语法也不同)
2. src/assets/css/base.styl -> base.scss(html,body 白底 #ffffff、sans-serif)
3. src/assets/css/fonts.styl -> fonts.scss(现在是空占位文件,保留为空即可,注释说明)
4. src/assets/css/index.styl -> index.scss(@import 全部改 @use)
5. src/assets/css/reset.styl -> reset.scss
6. src/views/chatroom.vue 里 `<style lang="stylus" scoped>` -> `<style lang="scss" scoped>`,内部 stylus 语法转 sass(注意:stylus 没有冒号、缩进式;scss 有花括号+分号+冒号,变量引用 $box-color 要改成引用 _variables.scss 的变量)
7. main.ts 里 import 的 css 入口文件路径跟着改
8. stylus 依赖从 package.json 移除,加 sass

## 必须保持的行为(禁止改变)
- 视觉:白底 #ffffff 背景、无像素字体(marisa-cmd 用 sans-serif 13px bold)、712x512 左右布局、marisa.jpg 立绘位置(background-size 333px, position 58% 2%)、#cccc99 米色窗口配色、输入框纯白底
- API 契约:前端 api/index.ts 调 /api 路径,vite dev proxy 把 /api 转发到 127.0.0.1:3000 并去掉前缀(见 vite.config.ts,proxy 配置必须保留)
- 功能:聊天室、teach/forget/status 指令、v-text 安全渲染(第 15 行,不要改回 v-html)
- dev server 端口 8888,host 0.0.0.0

## 技术约束
- 后端 server-py/(Python)正在 3000 端口跑着,别动它;server/(Go)也别动
- 只动 client/ 目录
- npm registry 已配 npmmirror
- 用 npm 而不是 yarn(旧 yarn.lock 已删,现在用 package-lock.json)

## 完成标准
1. npm install 成功,package.json 里 vite ^8、sass 已替换 stylus
2. npm run type-check 通过(vue-tsc 零错误)
3. npm run build 成功,dist 正常产出
4. 起 dev server 8888,实测:
   - 页面 200
   - /api/Status 代理到 3000 返回正常
   - 界面样式和白底视觉和升级前一模一样(可以截图或用 curl 对比编译后 CSS 里的关键色值:background:#fff / #cccc99 / #022c60)
5. 输出 CHANGELOG_FRONTEND_V8.md:改了哪些文件、版本对照、stylus->sass 语法迁移说明

## 陷阱提醒
- stylus 和 sass 语法差异大:stylus 可以省略冒号/花括号/分号,sass 不行;stylus 变量 `x = v`,sass `$x: v`;stylus 的 rgba() 和 sass 一样但注意颜色变量类型
- vite 8 可能要求 node 版本更高(本机 node 26 应该够)
- vue-tsc 3.x 对 TS 版本有 peer 要求,装的时候注意 npm 报错信息
- 升级后跑一遍 dev server 的页面,确保没有样式丢失(特别是 marisa.jpg 立绘和输入框)
- 不要升级 vue 本身(保持 3.5.x)

开始吧。先备份/梳理现有 stylus 文件,再逐个转换,最后升级依赖版本。
