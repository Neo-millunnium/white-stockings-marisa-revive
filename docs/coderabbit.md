# CodeRabbit 使用说明

本仓库已接入 CodeRabbit，但**不自动触发审查**（公开仓库 star 数不足 10，且配置里关闭了 auto_review）。

## 手动触发

在 PR 评论里发：

- `@coderabbitai review` —— 增量审查（只看新提交）
- `@coderabbitai full review` —— 全量审查
- `@coderabbitai summary` —— 只更新摘要
- `@coderabbitai configuration` —— 打印当前生效配置

## 与自动合并的关系

`CodeRabbit` 是合并前必需检查之一：没跑过审查，PR 会停在「等待状态上报」，auto-merge 不会合。
手动触发审查并等它通过后，CI 与 CodeRabbit 都绿，GitHub 才会自动 squash 合并。

## 配置

仓库根目录 `.coderabbit.yaml`，改了之后 CodeRabbit 会在下一次审查时生效。
