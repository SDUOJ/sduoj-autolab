"""Automation task registry and worker helpers."""

from importlib import import_module

from .base import BaseAutoTask, TASK_REGISTRY, register_task
from .constants import TASK_QUEUE_NAME

def load_builtin_tasks():
    for name in ("code_similarity", "subjective_review", "summary_report"):
        import_module("." + name, __name__)


def start_background_worker():
    from .worker import start_background_worker as start
    return start()

_LAZY_IMPORTS = {
    "AutoTaskLLMClient": ("auto_task.llm_client", "AutoTaskLLMClient"),
    "LLMConfig": ("auto_task.llm_client", "LLMConfig"),
    "call_structured_llm": ("auto_task.llm_client", "call_structured_llm"),
    "describe_image_to_text": ("auto_task.llm_client", "describe_image_to_text"),
    "convert_document_to_markdown": ("auto_task.document_parser", "convert_document_to_markdown"),
}


def __getattr__(name):
    if name in _LAZY_IMPORTS:
        module_name, attr = _LAZY_IMPORTS[name]
        module = import_module(module_name)
        value = getattr(module, attr)
        globals()[name] = value
        return value
    raise AttributeError(name)


__all__ = [
    "AutoTaskLLMClient",
    "BaseAutoTask",
    "LLMConfig",
    "TASK_QUEUE_NAME",
    "TASK_REGISTRY",
    "call_structured_llm",
    "convert_document_to_markdown",
    "describe_image_to_text",
    "register_task",
    "start_background_worker",
]
