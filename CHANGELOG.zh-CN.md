# 变更记录

[English](CHANGELOG.md)

## 0.2.0

### Portfolio 与审批

- 新增 Portfolio 生命周期、不可变经济 Ledger、确定性精确十进制持仓与现金投影、单一记账币种和显式未知成本。
- 新增独立 Portfolio SQLite 持久化、经审批的初始化/成交/资金流/收入/纠错/原子内部转移 Agent Tool，以及只读 Web Portfolio 视图。
- 新增 New Portfolio / Ask Agent 工作流和持久 Session–Portfolio 关系，归档、恢复与 Fork 均保留关联。
- 在固定的轮次边界审查完整有效用户指令历史；历史超限时压缩授权上下文，压缩失败转人工审批。要求会计事实有依据、费用显式提供。
- 使用支持账户归属的 Portfolio schema v6，经备份迁移保留原会计事实。v3–v5 必须先停止旧 live deployment，再显式迁移。

### Skills

- 新增六个带版本的 Workspace Skill、注册/来源记录、启动发现和按需加载。
- 新增独立候选安全审查和经过审批的安装、删除与启停工作流，并提供 Web 列表/详情与 Create/Install/Edit/Fork Session 入口。
- 移除产品指南同步参数 `--sync` / `--sync-force`，使用确定性 `investorch --update` 更新内置 Skill，定制通过 fork 完成；保留用户 Memory。
- 将配置、Portfolio、RQAlpha 方法从 Memory/Core prompt 迁入 Skill，新增供人工部署的大 QMT 策略编写指导。

### 图片对话

- 新增端到端图片对话、通过 `explore` 读取 Workspace 图片、内联历史与点击加载外部图片。

### Setup、设置与恢复

- 使用严格 Python Schema 校验替代随包默认 TOML；Provider 须显式配置，支持原子配置更新/重置。原 Local TOML 必须符合当前 Schema。
- 新增 Agent 启动前的 Web Setup、使用后端字段元数据的持久 Settings，以及独立的进程级未来 Run 临时覆盖。需要重启的偏好与替换后的凭据在重启后生效。
- 新增安全脱敏的 MCP Integrations 管理；修改重启后生效，清空 Web timeout 恢复应用默认值。
- 新增只读 System Status、`doctor` 和 `upgrade check`，报告受管内置 Skill 差异；`--update` 仍需显式执行，且不初始化 Portfolio。
- 新增离线 Backup Create/Verify，默认排除凭据配置并支持人工恢复到独立 Root；Portfolio v6 迁移保持独立。
- 当原存储、配置或已启用 Skill 阻止安全启动时，Web 保持恢复指引可用。

### 兼容性

- 更新 Python Runtime/开发工具和 Web 依赖及 lockfile，包括 RQAlpha 6.4.1；可选 CNEquity 集成仍固定为 0.7.3。
- 允许 Linux 启动 ApplicationHost，Agent `exec_command` 仍只支持 macOS。
- Portfolio 写入沿用审批，Web Portfolio 页面只读，QMT 部署由人执行。本版本不包含实盘下单和券商对账。

安装和升级步骤见 [0.2.0 发行说明](docs/releases/0.2.0.zh-CN.md)。

## 0.1.0

InvestOrch Agent 的首个公开预览版本。

### 新增

- 基于同一 Application 与 Runtime 的 Web 和 Textual TUI，以及用于诊断的 plain console。
- 持久化 Session，支持归档、恢复、Fork、清除、上下文压缩和不同 Session 的并发 Run。
- Steer 与 Queue 跟进模式、manual 与模型辅助审批、用量展示和持久化对话 Journal。
- Workspace 范围内的文件、命令执行、计算、Todo、配置和 MCP registry Tool。
- 基于 RQAlpha 的日频股票回测，包含可复现元数据和 Workspace 相对 artifact。
- 可选 CNEquity CLI、数据覆盖层和只读 MCP 集成。

### 发行

- 通过 GitHub Release 提供 wheel、源码包和 SHA-256 校验文件。
- InvestOrch Agent 代码与文档采用 Apache-2.0 许可证，并随包提供第三方声明和 Web 依赖许可证清单。

安装要求、当前范围和已知边界见 [0.1.0 发行说明](docs/releases/0.1.0.zh-CN.md)。
