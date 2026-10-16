"""Opt-in real local Docker execution. Never use a remote daemon for this suite."""
import os
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

pytestmark = pytest.mark.skipif(os.environ.get("AUTOLAB_AI_REAL_SANDBOX_TEST") != "1", reason="Opt-in local container tests")


@pytest.fixture
def context(tmp_path, monkeypatch):
    endpoint = subprocess.check_output(["docker", "context", "inspect", "desktop-linux", "--format", "{{.Endpoints.docker.Host}}"], text=True).strip()
    assert endpoint.startswith("unix://")
    monkeypatch.setenv("DOCKER_HOST", endpoint)
    monkeypatch.setenv("AUTOLAB_SANDBOX_ENABLED", "1")
    for language in ("C", "CPP"):
        monkeypatch.setenv("AUTOLAB_SANDBOX_" + language + "_IMAGE", "gcc:14-bookworm")
    monkeypatch.setenv("AUTOLAB_SANDBOX_PYTHON_IMAGE", "python:3.13-slim")
    return SimpleNamespace(workspace=tmp_path, check=lambda: None)


@pytest.mark.parametrize("language,code", [
    ("c", '#include <stdio.h>\nint main(){int a,b;scanf("%d%d",&a,&b);printf("%d\\n",a+b);}'),
    ("cpp", '#include <iostream>\nint main(){int a,b;std::cin>>a>>b;std::cout<<a+b<<"\\n";}'),
    ("python", 'a,b=map(int,input().split());print(a+b)'),
    ("java", 'import java.util.*;public class Main{public static void main(String[] args){Scanner s=new Scanner(System.in);System.out.println(s.nextInt()+s.nextInt());}}'),
])
def test_real_stdin_and_output(context, language, code):
    from auto_task.sandbox import execute
    result = execute(context, language, code, "17 25\n")
    assert result["exit_code"] == 0, result
    assert result["output"].strip() == "42"


def test_compilation_error_returns_diagnostics(context):
    from auto_task.sandbox import execute
    result = execute(context, "cpp", "int main( {", "")
    assert result["exit_code"] != 0
    assert "error" in result["diagnostics"].lower()


def test_time_limit_terminates_student_program(context):
    from auto_task.sandbox import execute
    result = execute(context, "python", "while True: pass", "")
    assert result["timed_out"] and result["exit_code"] == 124


def test_network_is_unavailable(context):
    from auto_task.sandbox import execute
    code = "import socket\ns=socket.socket();s.settimeout(1)\ntry:\n s.connect(('1.1.1.1',80));print('UNSAFE')\nexcept OSError:\n print('BLOCKED')"
    result = execute(context, "python", code, "")
    assert result["exit_code"] == 0 and result["output"].strip() == "BLOCKED"


def test_student_filesystem_is_bounded_and_source_mount_is_read_only(context):
    from auto_task.sandbox import execute
    code = """import errno
try:
 with open('/inputs/unwanted', 'wb') as f: f.write(b'x')
 print('UNSAFE')
except OSError:
 print('READONLY')
try:
 with open('/workspace/fill', 'wb') as f:
  for _ in range(80): f.write(b'x' * 1048576)
 print('UNBOUNDED')
except OSError as e:
 print('BOUNDED' if e.errno == errno.ENOSPC else 'UNEXPECTED')
"""
    result = execute(context, "python", code)
    assert result["exit_code"] == 0, result
    assert result["output"].splitlines() == ["READONLY", "BOUNDED"]
    assert not list(context.workspace.rglob("fill"))
    assert not list(context.workspace.rglob("unwanted"))


def test_jplag_real_report(context, monkeypatch):
    from auto_task.plagiarism import scan
    context.ask = lambda *args: {"answer": "Review candidate only", "citations": []}
    monkeypatch.setenv("AUTOLAB_JPLAG_IMAGE", "eclipse-temurin:25-jdk")
    code = """import java.util.*;
public class Main {
 public static void main(String[] args) {
  Scanner scanner = new Scanner(System.in);
  int n = scanner.nextInt();
  int[] values = new int[n];
  for (int i = 0; i < n; i++) values[i] = scanner.nextInt();
  Arrays.sort(values);
  long sum = 0;
  for (int value : values) {
   if (value % 2 == 0) sum += value * value;
   else sum -= value;
  }
  System.out.println(sum);
 }
}
"""
    result = scan(context, {"language": "java", "threshold": 0.5, "submissions": [
        {"user_id": "fixture-1", "code": code}, {"user_id": "fixture-2", "code": code.replace("values", "numbers")} ]})
    assert result["needs_review"]
    assert len(result["pairs"]) >= 1
    assert Path(context.workspace / result["report"]).exists()
