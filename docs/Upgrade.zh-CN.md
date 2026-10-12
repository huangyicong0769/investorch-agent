# 升级与预检

[English](Upgrade.md)

本文介绍如何升级到 0.2.0。版本范围与软件包名称见[发行说明](releases/0.2.0.zh-CN.md)。

使用 Python 3.12–3.14（`>=3.12,<3.15`）。下方命令显式选择 Python 3.12。

## 选择预检命令

`doctor`、`upgrade check` 和 `backup` 是 0.2.0 新命令。升级 0.1.0 工具安装时，先保留旧工具，从[源码](../README.zh-CN.md#源码安装)运行 0.2.0 的离线命令：

```sh
uv run --project /path/to/investorch-agent --locked --python 3.12 investorch doctor --root "$HOME/.investorch"
uv run --project /path/to/investorch-agent --locked --python 3.12 investorch upgrade check --root "$HOME/.investorch" --json
```

已经安装 0.2.0 时，运行：

```sh
investorch doctor
investorch upgrade check --json
```

两项命令均为离线只读操作，不创建 ApplicationHost、不连接 Provider/MCP、不初始化数据库、不重写配置，也不更新 Skill。检查独立安装时可加 `--root /path/to/root`。退出码：`0` 表示检查正常或功能禁用，`1` 表示 Warning/Unknown，`2` 表示错误。按每项 remediation 处理；连接未测试或可选数据缺失不等同于数据损坏。

## 升级顺序

1. 阅读预检结果，停止所有客户端和其他写入者。记录 Session ID/标题、Portfolio ID/Ledger 数量/持仓/现金、Skill enabled 状态和关键 Workspace 文件，用于升级后比较。
2. 使用 0.2.0 CLI 创建并校验[离线备份](Backup_and_Recovery.zh-CN.md)。输出目录须已存在，归档须位于 Root 之外。升级后的数据核对完成前，保留归档和旧软件包。若配置校验阻止备份创建，先完整复制已停止的 Root，包括全部 SQLite sidecar，再修复配置；完整副本包含原始凭据，须私密保存。
3. 沿用原安装方式安装已校验 checksum 的 0.2.0 软件包。uv 工具安装使用 `uv tool install --python 3.12 --force ./investorch-0.2.0-py3-none-any.whl`；原工具使用 CNEquity extra 时保留 `[cnequity]`。源码安装切到 `v0.2.0`，以原 extras 执行 `uv sync --locked --python 3.12`。安装软件包本身不迁移应用数据。
4. 按下文处理 Portfolio 显式迁移要求。
5. 需要替换为新包内置 Skill 时执行 `investorch --update`，随后重启。
6. 再运行 Doctor，启动 Web/TUI/plain，与第 1 步记录逐项比较。单独验证模型连接与 MCP/数据集成；离线检查和归档校验不代表外部连接已经成功。

从源码仓库运行时，在命令前加 `uv run --locked`。

## Local TOML 配置

默认值来自 Python Schema，`~/.investorch/investorch.toml` 保存用户覆盖和 Secrets。所有配置均按当前 Schema 校验。模型 Endpoint、名称与 Secret 引用须显式配置；应用不恢复历史 Provider 默认值，也不迁移 Local TOML。

TOML 损坏、含未知字段或字段值非法时，修复原文件或恢复有效备份。不要为绕过启动错误删除 Root 或数据库。模型信息/凭据缺失可运行 `investorch web` 完成 Setup；TUI/plain 也会显示这条指引。

0.1.0 可能依赖随包 DeepSeek 模型默认值，而 Local TOML 只保存 Key。0.2.0 需要显式配置所有必要模型角色，单独保存凭据不会恢复旧默认值。Web 因非法配置进入 recovery 时，先离线修复文件并重启；Setup 处理缺失模型设置，不能修复任意 Schema 错误。

存储/Workspace 目录变化须停机后离线迁移，Web 拒绝这类路径编辑。保存的目录布局须与恢复的数据一致。凭据与其他需要重启的配置在重启后使用保存值。

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

0.1.0 的 `--sync` / `--sync-force` 参数已移除。`--update` 只更新受管 Skill，不合并或替换用户 Memory 与旧产品指南文件。

```sh
investorch --update
```

它用当前包内容替换 managed built-in Skills，保留 enabled 状态和自定义 Skills，报告 installed/updated/unchanged 数量。不初始化或迁移 Session/Portfolio 数据库，不重写 Local TOML。新 Root 中只创建更新 Skill 所需目录和 Registry；正常启动不会自动替换内置内容。

需要定制内置 Skill 时应 Fork 为新名。Doctor 检查缺失、非法和内容差异，版本字符串相同的内容变化也能被发现。管理操作后需重启；同一进程中新开 Session 不会刷新启动时的 catalog。

若已有自定义/未注册 Skill 与新增内置 Skill 同名，初始化/更新会停止而不是覆盖。先保留原 Skill 并改名或移走，再重试。

## 回退升级

停止所有写入者，按[备份和恢复](Backup_and_Recovery.zh-CN.md)把已校验的升级前备份恢复到独立空 Root，再安装保留的旧软件包。默认备份需要重新填写排除的凭据与 MCP 设置。不要用旧软件包打开升级后的存储，也不要在应用运行时单独替换 SQLite 文件。恢复后的记录核对完成后，再将其作为 `~/.investorch` 使用。
