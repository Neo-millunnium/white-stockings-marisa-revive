# CI 与自动合并

本仓库对 `master` 启用了「CI 必须通过 + PR 自动合并」的流水线：

## 流程

1. 任何人提 PR 到 `master`（含 fork 的 PR）。
2. `.github/workflows/ci.yml` 在 PR 上跑三项检查：
   - `client`：`npm ci` + `vue-tsc` 类型检查 + `vite build`
   - `server-py`：`python -m compileall` 语法检查
   - `server-go`：`go build ./...`
3. `.github/workflows/auto-merge.yml` 给该 PR 打开 auto-merge（squash）。
4. Copilot 自动审查会在这个 PR 上留评论（规则集 `copilot_code_review`）。
5. 三项检查全绿后，GitHub 自动 squash 合并并删除分支。

## 规则集

`Settings → Rules → protect-master-auto-merge`，作用于默认分支：

- 禁止删除分支、禁止强推
- 必需检查：`client` / `server-go` / `server-py`
- PR 必需：要求 0 个批准（所以全自动），过期 review 自动 dismiss
- Copilot code review：每次 push 自动再审
- 仓库管理员在 bypass 名单里（必要时可直推）

## 想改成「人工批准后再自动合并」

把规则集的必需批准数改成 1：

```bash
gh api -X PUT repos/Neo-millunnium/white-stockings-marisa-revive/rulesets/26259566 \
  --input <(gh api repos/Neo-millunnium/white-stockings-marisa-revive/rulesets/26259566 \
    | jq '.rules |= map(if .type == "pull_request" then .parameters.required_approving_review_count = 1 else . end)')
```

## 已知限制

- **首次贡献者**的 PR 需要维护者在 Actions 页面点一次「Approve and run」，CI 才会跑；检查没上报前 auto-merge 会一直等。
- 外部贡献者的 PR 默认拿不到写权限，所以 auto-merge 由 `pull_request_target` 里的 `gh pr merge --auto` 代劳（该工作流不 checkout PR 代码）。
- 自动合并只代表「CI 绿」，不代表代码安全；公开仓库如需更严，可把批准数改为 1 或引入 CodeRabbit 等第三方审查。
