# 变更记录

[English](CHANGELOG.md)

## 0.1.0

InvestOrch Agent 的首个公开预览版本。

### 新增

- 基于同一 Application 与 Runtime 的 Web 和 Textual TUI，以及用于诊断的 plain console。
- 持久化 Session，支持归档、恢复、Fork、清除、上下文压缩和不同 Session 的并发 Run。
- Steer 与 Queue 跟进模式、manual 与模型辅助审批、用量展示和持久化对话 Journal。
- Workspace 范围内的文件、命令执行、计算、Todo、配置和 MCP registry Tool。
- 基于 RQAlpha 的日频股票回测，包含可复现元数据和 Workspace 相对 artifact。
- 可选 CNEquity CLI、数据覆盖层和只读 MCP 集成。
- 通过 `--sync` 与 `--sync-force` 同步 Workspace 引导文件。

### 发行

- 通过 GitHub Release 提供 wheel、源码包和 SHA-256 校验文件。
- InvestOrch Agent 代码与文档采用 Apache-2.0 许可证，并随包提供第三方声明和 Web 依赖许可证清单。

安装要求、当前范围和已知边界见 [0.1.0 发行说明](docs/releases/0.1.0.zh-CN.md)。
