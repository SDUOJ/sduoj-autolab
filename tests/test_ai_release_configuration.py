import os
import subprocess
import sys

from cryptography.fernet import Fernet


def test_release_refuses_missing_credentials_and_reads_explicit_environment():
    env = dict(os.environ, SDUOJ_MODE="release")
    for name in ("SDUOJ_TOKEN", "AUTOLAB_MYSQL_PASSWORD", "AUTOLAB_REDIS_PASSWORD",
                 "AUTOLAB_AI_SECRET_KEY"):
        env.pop(name, None)
    missing = subprocess.run([sys.executable, "-c", "import const"], env=env, capture_output=True, text=True)
    assert missing.returncode != 0
    assert "Missing release configuration: SDUOJ_TOKEN" in missing.stderr
    env.update(SDUOJ_TOKEN="fixture-token", AUTOLAB_MYSQL_PASSWORD="fixture-db",
               AUTOLAB_REDIS_PASSWORD="fixture-redis",
               AUTOLAB_AI_SECRET_KEY=Fernet.generate_key().decode())
    configured = subprocess.run([sys.executable, "-c", 'import const; assert const.Mysql_pass == "fixture-db"; assert const.Redis_pass == "fixture-redis"; assert const.LLM_DOUBAO_API_KEY == ""'], env=env, capture_output=True, text=True)
    assert configured.returncode == 0, configured.stderr


def test_release_rejects_whitespace_credentials_and_invalid_fernet_key():
    base = dict(os.environ, SDUOJ_MODE="release", SDUOJ_TOKEN="fixture-token",
                AUTOLAB_MYSQL_PASSWORD="fixture-db", AUTOLAB_REDIS_PASSWORD="fixture-redis")
    invalid_secret = subprocess.run(
        [sys.executable, "-c", "import const"],
        env={**base, "AUTOLAB_AI_SECRET_KEY": "not-a-fernet-key"},
        capture_output=True, text=True)
    assert invalid_secret.returncode != 0
    assert "Invalid release configuration: AUTOLAB_AI_SECRET_KEY" in invalid_secret.stderr
    whitespace = subprocess.run(
        [sys.executable, "-c", "import const"],
        env={**base, "SDUOJ_TOKEN": " fixture-token ",
             "AUTOLAB_AI_SECRET_KEY": Fernet.generate_key().decode()},
        capture_output=True, text=True)
    assert whitespace.returncode != 0
    assert "Missing release configuration: SDUOJ_TOKEN" in whitespace.stderr
