"""旧版插件数据导入器。

这是仓库里**唯一**允许出现旧宿主框架名的目录（守卫测试
`tests/backend/test_no_legacy_host_references.py` 白名单）：旧插件把数据放在宿主的
`data/plugin_data/<插件名>/` 下，配置放在 `data/config/<插件名>_config.json`，导入时必须按名定位。
面向用户的文案一律称「旧版插件数据」，不出现宿主名。

流程（复制，不移动；旧目录只读）：
1. `staging.stage_legacy_import`：复制到 `<data>/.legacy_import/staging/<id>/` → 改写绝对路径 →
   在副本上跑迁移 → 翻译旧配置（返回需要用户确认的事项）→ 写 pending 标记；
2. 软重启：`apply.apply_pending_legacy_import` 在数据库连接建立前把副本换入数据目录、
   写入设置与机密（被替换的内容移入 rollback 目录）；
3. 初始化成功 → `commit_applied_legacy_import`；失败 → `rollback_applied_legacy_import` 原样恢复。
"""
