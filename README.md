# 场馆餐饮队列工作台

这是体育商业团队使用的本地业务工具，现有能力包括人工业务登记、状态核对、事务执行、变更追溯，以及基于历史记录的容量情景比较（加开窗口、提前备餐等）。经营数据采用本地合成记录，不含真实个人资料，也不需要外部账户。现有业务操作见 `docs/业务约定.md`，容量比较的口径、观察边界与不能推荐的范围见 `docs/容量比较.md`。

## 环境与运行

使用 Python 3.11，业务运行只使用标准库和内置 SQLite。测试与打包另需安装精确锁定的构建工具；Docker 镜像与冻结任务环境均在准备阶段安装它们，正式业务回放不访问网络。全部演示离线执行，写入用户指定的本地数据库；不指定数据库时只在内存中运行，不产生文件。

```bash
python3 -m pip install -r requirements-build.txt
```

```bash
python3 -m product demo
python3 -m product demo --db /tmp/concourse-capacity.sqlite3
python3 -m product inspect --db /tmp/concourse-capacity.sqlite3
```

业务数据的完整顺序保存在 `data/demo.json`。也可将同格式的操作序列交给现有操作台执行，每项为业务操作名称与参数对象，不会隐式替换已有数据。

```bash
python3 -m product replay --file data/demo.json
```

容量比较需要先回放历史样本，再执行 `data/compare.json` 中的覆盖检查、方案评估、情景保存与采纳：

```bash
python3 -m product replay --db /tmp/halftime.sqlite3 --file data/history.json
python3 -m product replay --db /tmp/halftime.sqlite3 --file data/compare.json
python3 -m product inspect --db /tmp/halftime.sqlite3
```

## 测试与编译

```bash
python3 -m unittest discover -s tests -v
python3 -m compileall -q product tests
python3 -m build --wheel --no-isolation
sh scripts/verify.sh
```

测试覆盖业务链、非法操作回滚、重复编号、变更历史与磁盘重启。演示与测试的数据互相隔离，重复执行使用新的内存实例；持久库内的重复业务编号会被拒绝，不要在同一个库中重复导入演示。

## 容器

```bash
docker build -t concourse-capacity .
docker run --rm --network none concourse-capacity
```

容器仅运行本项目，不依赖旁路服务。常规测试和演示合计通常在数秒内完成；本地核验脚本 `scripts/verify.sh` 将演示输出放在临时目录并清理，运行预算为九十秒。后续新增功能也应保持小规模业务样本可快速复验，不要求预设统计方向。
