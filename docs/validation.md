# 验证记录

本轮使用 Python 3.12，未提供真实用户投影或 Minecraft 游戏实例。

- 标准库 unittest：32 项通过。包含 NBT 类型/Unicode、跨 64 位打包、调色板位宽扩容、正负尺寸、多区域保留、区域选择/分页、切片、属性修改、写入保护、损坏文件处理、CLI 与 MCP 子进程、独立导出技能包。
- 独立库交叉验证：在 `/tmp` 的单独虚拟环境安装 Litemapy 0.11.0b0，使用其生成投影，检查三种正负区域尺寸共 567 个方块；修改后全部由 Litemapy 重新读取比对，中文与 emoji 元数据保留。
- 技能包从仓库外解压后，用 Python isolated 模式运行；不依赖原仓库、PYTHONPATH 或第三方安装。
- MCP 测试覆盖初始化、工具发现、真实编辑、重读、错误响应、根目录与符号链接越界拒绝。

这些结果验证文件处理与工具接口，不验证 Minecraft 内红石运行效果、性能、机器功能或全部游戏版本兼容性。

重跑标准测试：

```bash
PYTHONPATH=src python -m unittest discover -s tests -v
```

可选独立验证（仅开发交叉检查使用，运行时不需要 Litemapy）：在单独测试虚拟环境安装 `litemapy==0.11.0b0`，然后在仓库根目录用该环境 Python 运行：

```bash
PYTHONPATH=src /path/to/test-venv/bin/python scripts/verify_litemapy.py
```
