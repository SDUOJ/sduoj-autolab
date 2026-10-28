"""Bounded Docker-compatible execution; production must use an isolated engine."""

import os
import subprocess

from .entities import uid
from .runtime import run_process

RUNTIMES = {
    "c": ("gcc:14-bookworm", "main.c", "gcc -O2 main.c -o main && timeout 5 ./main"),
    "cpp": ("gcc:14-bookworm", "main.cpp", "g++ -std=c++20 -O2 main.cpp -o main && timeout 5 ./main"),
    "python": ("python:3.13-slim", "main.py", "timeout 5 python -I main.py"),
    "java": ("eclipse-temurin:21-jdk", "Main.java", "javac Main.java && timeout 5 java -Xmx128m Main"),
}


def arguments(image, directory, name, command, memory="256m"):
    return [os.environ.get("AUTOLAB_DOCKER_BIN", "docker"), "run", "--rm", "-i", "--name", name,
            "--network", "none", "--read-only", "--cap-drop", "ALL", "--security-opt", "no-new-privileges",
            "--pids-limit", "64", "--memory", memory, "--cpus", "1", "--user", "65534:65534",
            "--tmpfs", "/tmp:rw,noexec,nosuid,size=64m", "--mount", "type=bind,src={},dst=/workspace".format(directory.resolve()),
            "--workdir", "/workspace", image, *command]


def execute(ctx, language, code, stdin=""):
    if language not in RUNTIMES or len(code) > 200000 or len(stdin) > 1000000:
        raise ValueError("Invalid sandbox input")
    if os.environ.get("AUTOLAB_SANDBOX_ENABLED") != "1":
        raise RuntimeError("Configure an isolated sandbox engine before enabling execution")
    image, filename, command = RUNTIMES[language]
    image = os.environ.get("AUTOLAB_SANDBOX_" + language.upper() + "_IMAGE", image)
    directory = ctx.workspace / ("sandbox-" + uid())
    directory.mkdir(mode=0o777)
    directory.chmod(0o777)
    (directory / filename).write_text(code, encoding="utf8")
    name = "autolab-task-" + uid()
    args = arguments(image, directory, name, ["sh", "-c", "cp /inputs/" + filename + " /workspace/" + filename + " && " + command])
    mount_index = args.index("--mount") + 1
    args[mount_index] = "type=bind,src={},dst=/inputs,readonly".format(directory.resolve())
    workdir_index = args.index("--workdir")
    args[workdir_index:workdir_index] = ["--tmpfs", "/workspace:rw,exec,nosuid,nodev,size=64m,uid=65534,gid=65534"]
    try:
        output, diagnostics, code = run_process(args, directory, ctx.check, 30, stdin.encode(), max_output=128 * 1024, allow_nonzero=True, separate_stderr=True)
        return {"exit_code": code, "output": output.decode(errors="replace"), "diagnostics": diagnostics.decode(errors="replace"), "language": language, "timed_out": code == 124}
    except (RuntimeError, TimeoutError) as error:
        return {"exit_code": 1, "output": "", "diagnostics": str(error), "language": language, "timed_out": isinstance(error, TimeoutError)}
    finally:
        subprocess.run([args[0], "rm", "-f", name], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=15)
