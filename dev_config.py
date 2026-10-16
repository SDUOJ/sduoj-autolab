"""Host-side settings for the independent Docker development environment."""

import os
from pathlib import Path

from dotenv import dotenv_values


SERVICE_PORTS = {
    "gateway": 8080,
    "user-service": 18081,
    "problem-service": 18082,
    "contest-service": 18083,
    "filesys-service": 18084,
    "websocket-service": 18085,
}
for service, default_port in SERVICE_PORTS.items():
    name = "AUTOLAB_DEV_" + service.replace("-", "_").upper() + "_PORT"
    configured = os.environ.get(name, str(default_port))
    if not configured.isascii() or not configured.isdecimal() or not 1 <= int(configured) <= 65535:
        raise RuntimeError("Invalid local service port: " + name)
    SERVICE_PORTS[service] = int(configured)



def load_settings():
    env_file = Path(__file__).resolve().parents[1] / "sduoj-deploy/data/dev/autolab.env"
    local = dotenv_values(env_file) if env_file.exists() else {}

    def required(name):
        value = os.environ.get(name, local.get(name))
        if not value:
            raise RuntimeError(f"Missing {name}; run ../sduoj-deploy/dev up first")
        return value

    def setting(name, default):
        return os.environ.get(name, local.get(name) or default)

    return {
        "SDUOJ_TOKEN": required("SDUOJ_TOKEN"),
        "NACOS_addr": setting("AUTOLAB_NACOS_ADDR", "127.0.0.1:8848"),
        "NACOS_namespace": setting("AUTOLAB_NACOS_NAMESPACE", "dev"),
        "Redis_addr": setting("AUTOLAB_REDIS_ADDR", "127.0.0.1:7361"),
        "Redis_pass": required("AUTOLAB_REDIS_PASSWORD"),
        "Mysql_addr": setting("AUTOLAB_MYSQL_ADDR", "127.0.0.1:7360"),
        "Mysql_user": setting("AUTOLAB_MYSQL_USER", "sduoj_ext_ps"),
        "Mysql_pass": required("AUTOLAB_MYSQL_PASSWORD"),
        "Mysql_db": setting("AUTOLAB_MYSQL_DATABASE", "sduoj_ext_problem_set"),
        "LLM_DOUBAO_BASE_URL": os.environ.get("DEV_LLM_BASE_URL", ""),
        "LLM_DOUBAO_API_KEY": os.environ.get("DEV_LLM_API_KEY", ""),
        "LAYOUT_PARSING_API_URL": os.environ.get("DEV_LAYOUT_PARSING_API_URL", ""),
    }
