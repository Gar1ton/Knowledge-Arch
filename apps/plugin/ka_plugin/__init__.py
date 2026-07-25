"""AstrBot 插件壳自有包（发布形态下位于插件根目录）。

存放**只属于 AstrBot 壳**的代码（适配器等）。独立版（``standalone``）与业务核心
（``kacore``）都不会 import 本包——这是「一核两壳」里 AstrBot 侧的边界。

包名带 ``ka_`` 前缀是刻意的：AstrBot 会把插件目录加入 ``sys.path``，通用名（如
``adapters``）易与其他插件撞名，历史上 ``core``/``web`` 就撞过（见 ``main.py`` 的
``_purge_stale_local_modules``）。
"""

__all__: list[str] = []
