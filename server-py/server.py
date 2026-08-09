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
        import main  # 延迟导入:保证配置在 main 装配前就绪

        self.app = main.create_app()
        self.host = host
        self.port = port or config.http_port()

    def run(self):
        """以前台方式运行服务(uvicorn 单进程;生产多进程见 README 部署建议)。"""
        import uvicorn

        uvicorn.run(self.app, host=self.host, port=self.port)


if __name__ == "__main__":
    WebMarisaServer(sys.argv[1] if len(sys.argv) > 1 else None).run()
