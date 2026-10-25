# -*- coding: utf-8 -*-
"""
WebMarisaServer:服务外壳,统一管理 配置加载 -> 应用装配 -> 进程生命周期。

用法(在 server-py 目录下):
    python server.py                  # 默认读 server-py/conf.yaml
    python server.py /path/to.yaml    # 指定配置文件

或代码内:
    from server import WebMarisaServer
    WebMarisaServer("conf.yaml").run()

配置链:环境变量 > conf.yaml > .env > 内置默认值(见 config.py)。
"""
import sys
from pathlib import Path

import config


def _load_main():
    """返回 main 模块;python main.py 时 __main__ 就是 main 本身,直接复用。

    server.py 里 `import main` 在 `python main.py` 场景下会二次执行整个 main.py
    (__main__ 与 main 是两个模块对象),凭空多出一个 MemoriseService 实例和第二份
    内存索引。这里优先复用 __main__,保证全进程只有一个 svc。
    """
    current = sys.modules.get("main")
    if current is not None:
        return current
    entry = sys.modules.get("__main__")
    if entry is not None and getattr(entry, "create_app", None) is not None:
        return entry
    import main as _main
    return _main


class WebMarisaServer:
    """web-marisa 后端服务:conf.yaml 配置 + Litestar 应用 + uvicorn 运行器。"""

    def __init__(self, config_path=None, host="127.0.0.1", port=None):
        """
        config_path:YAML 配置文件路径,默认 server-py/conf.yaml(缺失时走 .env/默认值)。
        host/port:监听地址与端口;port 缺省读配置 HTTP_PORT(默认 3000)。
        """
        self.config_path = config_path or str(Path(__file__).resolve().parent / "conf.yaml")
        # 先加载配置,再导入依赖配置的模块(database/service 在 import 时建引擎/读配置)
        config.load_yaml(self.config_path)
        self.app = _load_main().create_app()
        self.host = host
        self.port = port or config.http_port()

    def run(self):
        """以前台方式运行服务(uvicorn 单进程;生产多进程见 README 部署建议)。"""
        import uvicorn

        uvicorn.run(self.app, host=self.host, port=self.port)


if __name__ == "__main__":
    WebMarisaServer(sys.argv[1] if len(sys.argv) > 1 else None).run()
