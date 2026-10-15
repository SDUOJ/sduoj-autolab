import base64
import os

MOD = os.environ.get("SDUOJ_MODE", "debug").lower()
if MOD == "dev":
    MOD = "debug"
if MOD not in ("debug", "release"):
    raise ValueError("SDUOJ_MODE must be debug, dev or release")

PUBLIC_OJ_BASE_URL = "http://api.test.sduoj.com:8080" if MOD == "debug" else "https://oj.qd.sdu.edu.cn"


def required_release_setting(name):
    value = os.environ.get(name)
    if not value or value != value.strip() or "\x00" in value:
        raise RuntimeError("Missing release configuration: " + name)
    return value


def required_fernet_key(name):
    value = required_release_setting(name)
    try:
        decoded = base64.urlsafe_b64decode(value.encode("ascii"))
    except (ValueError, UnicodeError) as error:
        raise RuntimeError("Invalid release configuration: " + name) from error
    if len(decoded) != 32:
        raise RuntimeError("Invalid release configuration: " + name)
    return value


if MOD == "release":
    SDUOJ_TOKEN = required_release_setting("SDUOJ_TOKEN")
    NACOS_addr = os.environ.get("AUTOLAB_NACOS_ADDR", "127.0.0.1:8848")
    NACOS_namespace = os.environ.get("AUTOLAB_NACOS_NAMESPACE", "prod")
    Redis_addr = os.environ.get("AUTOLAB_REDIS_ADDR", "127.0.0.1:7361")
    Redis_pass = required_release_setting("AUTOLAB_REDIS_PASSWORD")
    Mysql_addr = os.environ.get("AUTOLAB_MYSQL_ADDR", "127.0.0.1:7360")
    Mysql_user = os.environ.get("AUTOLAB_MYSQL_USER", "sduoj_ext_ps")
    Mysql_pass = required_release_setting("AUTOLAB_MYSQL_PASSWORD")
    Mysql_db = os.environ.get("AUTOLAB_MYSQL_DATABASE", "sduoj_ext_problem_set")
    AUTOLAB_AI_SECRET_KEY = required_fernet_key("AUTOLAB_AI_SECRET_KEY")

LLM_DOUBAO_BASE_URL = os.environ.get("AUTOLAB_LEGACY_LLM_BASE_URL", "")
LLM_DOUBAO_API_KEY = os.environ.get("AUTOLAB_LEGACY_LLM_API_KEY", "")
LLM_DOUBAO_MODEL = os.environ.get("AUTOLAB_LEGACY_LLM_MODEL", "")
LLM_VOLC_DEEPSEEK_MODEL = os.environ.get("AUTOLAB_LEGACY_DEEPSEEK_MODEL", "")
LAYOUT_PARSING_API_URL = os.environ.get("AUTOLAB_LAYOUT_PARSING_API_URL", "")

if MOD == "debug":
    from dev_config import load_settings

    globals().update(load_settings())
