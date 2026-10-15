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


def load_settings():
    env_file = Path(__file__).resolve().parents[1] / "sduoj-deploy/data/dev/autolab.env"
    local = dotenv_values(env_file) if env_file.exists() else {}

    def required(name):
        value = os.environ.get(name, local.get(name))
        if not value:
            raise RuntimeError(f"Missing {name}; run ../sduoj-deploy/dev up first")
        return value

    return {
        "SDUOJ_TOKEN": required("SDUOJ_TOKEN"),
        "NACOS_addr": "127.0.0.1:8848",
        "NACOS_namespace": "dev",
        "Redis_addr": "127.0.0.1:7361",
        "Redis_pass": required("AUTOLAB_REDIS_PASSWORD"),
        "Mysql_addr": "127.0.0.1:7360",
        "Mysql_user": "sduoj_ext_ps",
        "Mysql_pass": required("AUTOLAB_MYSQL_PASSWORD"),
        "Mysql_db": "sduoj_ext_problem_set",
        "LLM_DOUBAO_BASE_URL": os.environ.get("DEV_LLM_BASE_URL", ""),
        "LLM_DOUBAO_API_KEY": os.environ.get("DEV_LLM_API_KEY", ""),
        "LAYOUT_PARSING_API_URL": os.environ.get("DEV_LAYOUT_PARSING_API_URL", ""),
    }
