from typing import Optional

from fastapi_cache import FastAPICache


def class_func_key_builder(
        func,
        namespace: Optional[str] = "",
        *args,
        **kwargs,
):
    prefix = FastAPICache.get_prefix()
    kwargs["args"] = list(kwargs["args"])[1:]
    cache_key = f"{prefix}:{namespace}:{func.__module__}:{func.__name__}:{args}:{kwargs}"
    return cache_key
