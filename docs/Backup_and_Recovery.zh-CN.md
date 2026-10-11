# 备份与恢复

[English](Backup_and_Recovery.md)

InvestOrch 可以创建本地 ZIP 备份并校验完整性。恢复需要手动执行，目标必须是空目录。

## 离线创建备份

先停止 Web、TUI、plain 客户端，以及其他会写入 Root 或 Workspace 的进程，直到备份结束。系统提供 `lsof` 时，命令会检查可写文件占用；检查不完整时会提示。这项检查不会冻结其他进程。

```sh
investorch backup create --output /root之外的已有目录/investorch-backup.zip
investorch backup verify /root之外的已有目录/investorch-backup.zip
```

输出目录必须已存在，目标不能位于 Root 内，也不能覆盖已有文件。只有校验通过后才生成最终归档；POSIX 文件权限为 `0600`。

可以指定独立 Root，或排除配置中的回测产物目录：

```sh
investorch backup create --root /path/to/root --output /path/to/backup.zip --exclude-artifacts
```

从源码仓库运行时，在命令前加 `uv run --locked`。

## 归档内容

默认包含 Session 和 Portfolio 的 SQLite 快照、Session Journal、Skill Registry、已安装 Skills、`MEMORY.md` 及其他 Workspace 文件。SQLite 快照包含 WAL 中已提交的数据，不对原数据库执行 checkpoint。Manifest 记录相对路径、大小、SHA-256、数据库版本、排除项和配置中的目录布局。

默认 `investorch.toml` 只保存非 Secret 覆盖项，移除整个 `secrets` 表，以及 URL 含用户名/密码、查询参数或片段的模型 Endpoint。原始 `mcp.toml`、日志、后台任务文件、配置中的 RQAlpha Bundle 和 CNEquity 配置不归档。`--exclude-artifacts` 额外排除回测产物目录。符号链接会跳过并记录，不跟随 Root 外的文件。

Workspace 和 Journal 本身也可能含私人信息或凭据。**所有备份均未加密**，默认备份也应妥善保管。明确需要原始 Local TOML 和 MCP 配置时使用：

```sh
investorch backup create --output /path/to/private-backup.zip --include-sensitive-config
```

Manifest 会标记敏感配置已包含。命令不会上传归档。

## 恢复到空 Root

1. 停止写入者并执行 `backup verify`。它检查条目、相对路径、大小、哈希和 SQLite 版本，不测试 Provider 连通性或行情数据。
2. 选择一个尚不存在的目标目录。在恢复结果核对完成前保留原 Root。
3. 使用[英文版中的完整恢复脚本](Backup_and_Recovery.md#restore-into-an-empty-root)，传入归档和新 Root。脚本使用 Manifest 内的实际相对路径，保留自定义 `paths.state` / `paths.workspace` 布局，不假设固定目录名。然后执行：

```sh
investorch doctor --root /path/to/new-root --json
investorch upgrade check --root /path/to/new-root --json
```

4. 核对 Session ID/标题和 Journal、Portfolio ID/账本序号、Skill enabled 状态，以及重要 Workspace 文件哈希。恢复文件默认仅当前用户可读写；需直接执行的脚本应补回执行权限。
5. 重新填写被排除的模型凭据/Endpoint 和 MCP 配置，按需恢复行情依赖或下载数据。此时缺凭据、行情未验证等 Warning 属于预期。
6. 若 Portfolio 需要迁移，遵循[升级说明](Upgrade.zh-CN.md)。不要用新数据库覆盖不可读的已有数据。
7. 正常 Web/TUI/plain 使用 `~/.investorch`。停止客户端后，将原目录保留为独立备份，再把核对通过的新 Root 移到该位置；缺凭据时通过 Web Setup 完成设置，然后在应用中复核相同记录。

解压中断时，保留归档，换一个空目录重试。产品不提供自动 Restore 或全状态回滚命令。

## 已执行的恢复验证

`tests/behavior/test_backup_behavior.py` 使用真实 SQLite 和 ZIP，在不同临时 Root 间恢复自定义目录布局，并对比 Session 身份/标题、Portfolio 账本与现金投影、禁用 Skill、Journal、`MEMORY.md` 和策略文件。另有 WAL 已提交数据、原始文件不变、凭据排除、占用检测和损坏归档拒绝测试。`tests/behavior/test_management_cli_behavior.py` 在独立 HOME 下通过子进程执行 create/verify。
