"""Validated iterative programming-problem authoring packages."""

import hashlib

from typing import List

from pydantic import BaseModel, Field, validator

from .store import digest, dumps


class AuthoredCase(BaseModel):
    input: str = Field(..., max_length=262144)
    output: str = Field(..., max_length=262144)
    public: bool = False
    purpose: str = Field(..., min_length=1, max_length=200)


class AuthoringDraft(BaseModel):
    title: str = Field(..., min_length=1, max_length=128)
    description: str = Field(..., min_length=1, max_length=200000)
    reference_code: str = Field(..., min_length=1, max_length=200000)
    language: str
    tests: List[AuthoredCase] = Field(..., min_items=2, max_items=20)
    generator_code: str = Field(..., min_length=1, max_length=200000)
    validator_code: str = Field(..., min_length=1, max_length=200000)
    generator_seeds: List[int] = Field(..., min_items=1, max_items=10)
    tags: List[str] = Field(default_factory=list, max_items=6)
    explanation: str = Field(..., min_length=1, max_length=200000)

    @validator("language")
    def supported_language(cls, value):
        if value not in ("c", "cpp", "python", "java"):
            raise ValueError("Unsupported authoring language")
        return value

    @validator("generator_seeds")
    def distinct_seeds(cls, value):
        if len(set(value)) != len(value) or any(seed < 0 or seed > 2**31 - 1 for seed in value):
            raise ValueError("Generator seeds must be distinct unsigned 31-bit integers")
        return value


def _run(execute, ctx, language, code, stdin):
    result = execute(ctx, language, code, stdin)
    if not isinstance(result, dict):
        raise ValueError("Sandbox returned an invalid result")
    return result


def content_sha(value):
    return hashlib.sha256(str(value).encode()).hexdigest()


def _require_success(result, label):
    if result.get("exit_code") != 0 or result.get("timed_out"):
        raise ValueError(label + " failed in the isolated sandbox")


def package_core(candidate):
    return {key: candidate[key] for key in (
        "title", "description", "reference_code", "language", "tests",
        "generator_code", "validator_code", "generator_seeds", "tags", "explanation",
        "test_plan", "revision", "parent_hash", "revision_instruction", "validation",
        "generator_validation",
    )}


def verify_candidate(candidate):
    if not isinstance(candidate, dict) or not candidate.get("validated"):
        raise ValueError("Authoring candidate has not passed validation")
    if digest(package_core(candidate)) != candidate.get("package_hash"):
        raise ValueError("Authoring candidate package hash mismatch")
    plan = candidate.get("test_plan")
    if not isinstance(plan, list) or not 3 <= len(plan) <= 30:
        raise ValueError("Authoring test plan is incomplete")
    if sum(int(item.get("score", -1)) for item in plan) != 100:
        raise ValueError("Authoring checkpoint scores must total 100")
    tests = candidate.get("tests")
    if not isinstance(tests, list) or len(tests) != len(plan):
        raise ValueError("Authoring tests and checkpoint plan differ")
    for index, (case, item) in enumerate(zip(tests, plan), 1):
        if item.get("index") != index or item.get("input") != case.get("input") or item.get("output") != case.get("output"):
            raise ValueError("Authoring checkpoint plan content mismatch")
        if item.get("input_sha256") != content_sha(item["input"]) or item.get("output_sha256") != content_sha(item["output"]):
            raise ValueError("Authoring checkpoint content hash mismatch")
    return candidate


def generate(ctx, data, payload, ask):
    """Generate one new revision and validate all executable evidence."""
    previous = data.get("candidate") if isinstance(data.get("candidate"), dict) else None
    instruction = str(payload.get("instruction", "")).strip()
    if not instruction:
        instruction = "Create the first complete draft from the teacher brief."
    prompt = (
        "Create or revise a programming problem draft. Return a reference solution, at least two explicit tests, "
        "a deterministic Python input generator driven only by an integer seed on stdin, and a Python validator "
        "that reads a test input from stdin and prints exactly OK when valid. Include boundary/adversarial cases. "
        "Public tests are examples; generated tests remain private. Do not use network, files, time, randomness "
        "without the provided seed, or external packages.\n" + dumps({
            "teacher_brief": {key: value for key, value in data.items() if key != "candidate"},
            "previous_draft": previous,
            "revision_instruction": instruction,
        })
    )
    draft = ask(ctx, AuthoringDraft, prompt)
    from .sandbox import execute

    explicit = [dict(case, generated=False) for case in draft["tests"]]
    generated = []
    generator_runs = []
    for seed in draft["generator_seeds"]:
        first = _run(execute, ctx, "python", draft["generator_code"], str(seed) + "\n")
        second = _run(execute, ctx, "python", draft["generator_code"], str(seed) + "\n")
        _require_success(first, "Input generator")
        _require_success(second, "Input generator repeat")
        if first.get("output") != second.get("output"):
            raise ValueError("Input generator is not deterministic for its seed")
        generated_input = str(first.get("output", ""))
        if not generated_input or len(generated_input.encode()) > 262144:
            raise ValueError("Generated test input is empty or too large")
        generated.append({"input": generated_input, "output": "", "public": False,
                          "purpose": "generated seed {}".format(seed), "generated": True, "seed": seed})
        generator_runs.append({"seed": seed, "input_sha256": content_sha(generated_input)})

    tests = explicit + generated
    if not 3 <= len(tests) <= 30:
        raise ValueError("Expected 3 to 30 explicit and generated tests")
    inputs = [item["input"] for item in tests]
    if len(set(inputs)) != len(inputs):
        raise ValueError("Authoring tests contain duplicate inputs")
    if sum(len(item["input"].encode()) + len(item.get("output", "").encode()) for item in tests) > 2 * 1024 * 1024:
        raise ValueError("Authoring test package exceeds 2 MiB")
    if sum(bool(item["public"]) for item in tests) > 20:
        raise ValueError("OJ supports at most 20 public example cases")

    validation = []
    for index, case in enumerate(tests):
        checked = _run(execute, ctx, "python", draft["validator_code"], case["input"])
        _require_success(checked, "Input validator")
        if str(checked.get("output", "")).strip() != "OK":
            raise ValueError("Input validator did not accept test {}".format(index + 1))
        solved = _run(execute, ctx, draft["language"], draft["reference_code"], case["input"])
        _require_success(solved, "Reference solution")
        actual = str(solved.get("output", ""))
        if case["generated"]:
            case["output"] = actual
        elif actual.strip() != str(case["output"]).strip():
            raise ValueError("Reference solution output mismatch on explicit test {}".format(index + 1))
        else:
            # Native checkpoints use the reference program's exact bytes so a
            # harmless newline discrepancy in the model draft cannot reject AC.
            case["output"] = actual
        validation.append({"index": index + 1, "validator": "accepted",
                           "reference_exit_code": solved["exit_code"],
                           "input_sha256": content_sha(case["input"]),
                           "output_sha256": content_sha(case["output"])})

    count = len(tests)
    if sum(len(item["input"].encode()) + len(item["output"].encode()) for item in tests) > 2 * 1024 * 1024:
        raise ValueError("Validated authoring test package exceeds 2 MiB")
    quotient, remainder = divmod(100, count)
    test_plan = []
    for index, case in enumerate(tests):
        test_plan.append({"index": index + 1, "input": case["input"], "output": case["output"],
                          "public": bool(case["public"]), "purpose": case["purpose"],
                          "generated": bool(case["generated"]), "seed": case.get("seed"),
                          "score": quotient + (1 if index < remainder else 0),
                          "input_sha256": content_sha(case["input"]),
                          "output_sha256": content_sha(case["output"])})
    revision = int(previous.get("revision", 0)) + 1 if previous else 1
    parent_hash = previous.get("package_hash", "") if previous else ""
    candidate = {**draft, "tests": tests, "test_plan": test_plan, "revision": revision,
                 "parent_hash": parent_hash, "revision_instruction": instruction,
                 "validation": validation, "generator_validation": generator_runs, "validated": True}
    candidate["package_hash"] = digest(package_core(candidate))
    return candidate
