# 场馆餐饮队列工作台

这是体育商业团队使用的本地业务工具，现有能力包括人工业务登记、状态核对、事务执行和变更追溯。经营数据采用本地合成记录，不含真实个人资料，也不需要外部账户。现有业务操作见 `docs/业务约定.md`，当前不提供自动统计建模或自动经营决策。

## 环境与运行

使用 Python 3.11，依赖只有标准库和内置 SQLite。无须安装任何第三方运行库。全部演示离线执行，写入用户指定的本地数据库；不指定数据库时只在内存中运行，不产生文件。

```bash
python3 -m product demo
python3 -m product demo --db /tmp/concourse-capacity.sqlite3
python3 -m product inspect --db /tmp/concourse-capacity.sqlite3
```

业务数据的完整顺序保存在 `data/demo.json`。也可将同格式的操作序列交给现有操作台执行，每项为业务操作名称与参数对象，不会隐式替换已有数据。

```bash
python3 -m product replay --file data/demo.json
```

## 测试与编译

```bash
python3 -m unittest discover -s tests -v
python3 -m compileall -q product tests
```

测试覆盖业务链、非法操作回滚、重复编号、变更历史与磁盘重启。演示与测试的数据互相隔离，重复执行使用新的内存实例；持久库内的重复业务编号会被拒绝，不要在同一个库中重复导入演示。

## 容器

```bash
docker build -t concourse-capacity .
docker run --rm --network none concourse-capacity
```

容器仅运行本项目，不依赖旁路服务。常规测试和演示合计通常在数秒内完成；本地核验脚本 `scripts/verify.sh` 将演示输出放在临时目录并清理，运行预算为九十秒。后续新增功能也应保持小规模业务样本可快速复验，不要求预设统计方向。
