# 升级与预检

[English](Upgrade.md)

升级已有安装前，先运行：

```sh
investorch doctor
investorch upgrade check --json
```

两项命令均为离线只读操作，不创建 ApplicationHost、不连接 Provider/MCP、不初始化数据库、不重写配置，也不更新 Skill。检查独立安装时可加 `--root /path/to/root`。退出码：`0` 表示检查正常或功能禁用，`1` 表示 Warning/Unknown，`2` 表示错误。按每项 remediation 处理；连接未测试或可选数据缺失不等同于数据损坏。

## 升级顺序

1. 阅读预检结果，停止所有客户端和其他写入者。
2. 创建并校验[离线备份](Backup_and_Recovery.zh-CN.md)。升级后的数据核对完成前，保留归档和旧软件包。
3. 沿用原安装方式安装选定的软件包。升级命令不创建 Release 或 Tag。
4. 按下文处理 Portfolio 显式迁移要求。
5. 需要替换为新包内置 Skill 时执行 `investorch --update`，随后重启。
6. 再运行 Doctor，启动 Web/TUI/plain，核对原有 Session、Portfolio 记录和 Skill enabled 状态。

从源码仓库运行时，在命令前加 `uv run --locked`。

## Local TOML 配置

默认值来自 Python Schema，`~/.investorch/investorch.toml` 保存用户覆盖和 Secrets。所有配置均按当前 Schema 校验。模型 Endpoint、名称与 Secret 引用须显式配置；应用不恢复历史 Provider 默认值，也不迁移 Local TOML。

TOML 损坏、含未知字段或字段值非法时，修复原文件或恢复有效备份。不要为绕过启动错误删除 Root 或数据库。模型信息/凭据缺失可运行 `investorch web` 完成 Setup；TUI/plain 也会显示这条指引。

## Portfolio v1–v6

| 当前版本 | 行为 |
| --- | --- |
| v1 / v2 | 正常初始化复用已有备份迁移流程升级到 v6，也可以显式迁移。 |
| v3 / v4 / v5 | 必须显式退役旧 live 状态并迁移，正常启动不会自动退役。 |
| v6 | 校验后直接使用。 |
| 未知版本 / 损坏数据库 | 报错且不替换原库，保留后调查或恢复。 |

```sh
investorch migrate portfolio --dry-run
investorch migrate portfolio
# 检查独立数据库：
investorch migrate portfolio --database /path/to/portfolio.db --dry-run
```

Dry-run 不创建归档、不写文件。WAL 模式会被拒绝，因为 SQLite 只读连接也可能修改 sidecar；先停止写入者，通过原应用 checkpoint 并切换到 `journal_mode=DELETE` 后重试。Doctor/Preflight 对此显示 Unknown。离线备份可以包含 WAL 已提交行，不对原库 checkpoint。

旧 `PREPARED` / `ACTIVE` live deployment 会阻止迁移。须通过旧版本停止并清理，包括旧 QMT 执行节点和自动策略，然后重新预检。已终止的旧 live 状态会先归档，再由显式迁移删除旧表。保留现有归档与账本事实；当前 QMT 路线仍是 Agent 生成策略 Artifact，再由人审查、部署。

## 显式更新内置 Skills

```sh
investorch --update
```

它用当前包内容替换 managed built-in Skills，保留 enabled 状态和自定义 Skills，报告 installed/updated/unchanged 数量。不初始化或迁移 Session/Portfolio 数据库，不重写 Local TOML。新 Root 中只创建更新 Skill 所需目录和 Registry；正常启动不会自动替换内置内容。

需要定制内置 Skill 时应 Fork 为新名。Doctor 检查缺失、非法和内容差异，版本字符串相同的内容变化也能被发现。管理操作后需重启；同一进程中新开 Session 不会刷新启动时的 catalog。
