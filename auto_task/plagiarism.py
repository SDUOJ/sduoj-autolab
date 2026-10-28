"""JPlag candidate retrieval plus evidence-based AI review, never automatic punishment."""

import json
import os
import subprocess
import zipfile
from pathlib import Path

from ser.auto_task_ai import Feedback, output_schema
from .entities import uid
from .runtime import run_process
from .sandbox import arguments


def _native_submissions(ctx, data):
    """Materialize an immutable native OJ snapshot for the JPlag runner."""
    manifest = data.get("native_snapshot")
    if not isinstance(manifest, dict):
        return data
    from .entities import AiPlagiarismSnapshot, AiPlagiarismSubmission
    from .store import digest, transaction

    with transaction() as session:
        snapshot = session.query(AiPlagiarismSnapshot).filter_by(
            id=manifest.get("id"), resource_id=ctx.job.get("resource_id"),
            resource_version=ctx.job.get("input_snapshot", {}).get("version"),
        ).one_or_none()
        if not snapshot or snapshot.sha256 != manifest.get("sha256"):
            raise ValueError("Native plagiarism snapshot is unavailable or changed")
        rows = session.query(AiPlagiarismSubmission).filter_by(snapshot_id=snapshot.id).order_by(
            AiPlagiarismSubmission.native_submission_id).all()
        if len(rows) != snapshot.count or len(rows) < 2:
            raise ValueError("Native plagiarism snapshot is incomplete")
        import hashlib
        manifest_rows = []
        for row in rows:
            code_hash = hashlib.sha256(row.code.encode()).hexdigest()
            if code_hash != row.code_sha256:
                raise ValueError("Native plagiarism submission integrity check failed")
            manifest_rows.append({"submission_id": row.native_submission_id, "user_id": row.user_id,
                                  "username": row.username, "code_sha256": code_hash,
                                  "answer_detail_id": row.answer_detail_id})
        if digest(manifest_rows) != snapshot.sha256:
            raise ValueError("Native plagiarism snapshot integrity check failed")
        submissions = [{"user_id": row.user_id, "username": row.username,
                        "submission_id": row.native_submission_id,
                        "answer_detail_id": row.answer_detail_id, "code": row.code,
                        "gmt_create": row.gmt_create.isoformat() if row.gmt_create else None,
                        "judge_result": row.judge_result, "judge_score": row.judge_score}
                       for row in rows]
    return {**data, "submissions": submissions}


def scan(ctx, data):
    if os.environ.get("AUTOLAB_SANDBOX_ENABLED") != "1":
        raise RuntimeError("An isolated sandbox engine is required")
    jar = Path(os.environ.get("AUTOLAB_JPLAG_JAR", str(Path(__file__).parent / "jplag-6.3.0-jar-with-dependencies.jar"))).resolve()
    if not jar.is_file():
        raise RuntimeError("JPlag jar is not installed")
    data = _native_submissions(ctx, data)
    submissions = data.get("submissions", [])
    if not 2 <= len(submissions) <= 1000:
        raise ValueError("Expected 2 to 1000 submissions")
    identities = [str(item.get("user_id", "")) for item in submissions]
    if not all(identities) or len(set(identities)) != len(identities):
        raise ValueError("Submissions require unique stable user IDs")
    language = data.get("language", "python")
    extensions = {"c": ".c", "cpp": ".cpp", "python": ".py", "java": ".java"}
    if language not in extensions:
        raise ValueError("Unsupported language")
    root = ctx.workspace / ("jplag-" + uid())
    root.mkdir(mode=0o777)
    root.chmod(0o777)
    sources = {}
    for index, item in enumerate(submissions):
        code = str(item.get("code", ""))
        if len(code) > 200000:
            raise ValueError("Submission too large")
        key = "s-" + str(index)
        directory = root / "submissions" / key
        directory.mkdir(parents=True)
        (directory / ("Main" + extensions[language])).write_text(code, encoding="utf8")
        sources[key] = item
    # -1 asks JPlag to retain every pair. Candidate selection is performed below
    # using the configured threshold, so report generation cannot silently hide
    # high-similarity pairs once the class grows beyond 100 submissions.
    threshold = float(data.get("threshold", 0.7))
    if not 0 <= threshold <= 1:
        raise ValueError("Similarity threshold must be between zero and one")
    command = ["java", "-jar", "/opt/jplag.jar", "-l", "python3" if language == "python" else language,
               "-r", "/workspace/report", "--cluster-skip", "--overwrite", "-n", "-1", "-m", str(threshold)]
    if data.get("base_code"):
        (root / "base").mkdir()
        (root / "base" / ("Base" + extensions[language])).write_text(data["base_code"], encoding="utf8")
        command.extend(["-bc", "/workspace/base"])
    command.append("/workspace/submissions")
    name = "autolab-jplag-" + uid()
    # The bundled 6.3 jar targets Java 25; its Java parser needs the JDK compiler.
    args = arguments(os.environ.get("AUTOLAB_JPLAG_IMAGE", "eclipse-temurin:25-jdk"), root, name, command, "1g")
    args[args.index("--workdir"):args.index("--workdir")] = ["--mount", "type=bind,src={},dst=/opt/jplag.jar,readonly".format(jar)]
    try:
        run_process(args, root, ctx.check, 900)
    finally:
        subprocess.run([args[0], "rm", "-f", name], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=15)
    reports = list(root.glob("*.jplag")) + list(root.glob("*.zip"))
    if not reports:
        raise ValueError("JPlag report missing")
    comparisons = read_comparisons(reports[0])
    pairs = []
    review_failures = 0
    ai_review_limit = max(0, min(1000, int(data.get("ai_review_limit", 100))))
    ai_available = True
    for item in comparisons:
        score = float(item.get("similarities", {}).get("MAX", 0) or 0)
        if score < threshold:
            continue
        left, right = item.get("first_submission"), item.get("second_submission")
        left, right = str(left).split("/")[-1], str(right).split("/")[-1]
        if left not in sources or right not in sources:
            raise ValueError("Unknown submission in JPlag result")
        matches = item.get("matches", [])
        if len(pairs) >= ai_review_limit or not ai_available:
            review, review_status, review_error = None, "manual", None
        else:
            try:
                review = Feedback.parse_obj(ctx.ask("Explain similarities AND plausible independent-solution/template causes. Do not decide misconduct. Chinese, cite concrete code segments.\n" + json.dumps({"left": sources[left], "right": sources[right], "base_code": data.get("base_code", ""), "similarity": score, "matches": matches}, ensure_ascii=False), output_schema(Feedback))).dict()
                review_status = "ready"
                review_error = None
            except Exception as error:
                from .store import LeaseLost
                if isinstance(error, LeaseLost):
                    raise
                # A provider outage must not discard JPlag evidence. The pair stays
                # visible and can be reviewed or retried by a later workflow.
                review, review_status, review_error = None, "pending", str(error)[:500]
                review_failures += 1
                ai_available = False
        pairs.append({"left": sources[left].get("user_id"), "right": sources[right].get("user_id"),
                      "left_submission_id": sources[left].get("submission_id"),
                      "right_submission_id": sources[right].get("submission_id"),
                      "left_username": sources[left].get("username"),
                      "right_username": sources[right].get("username"),
                      "similarity": score, "matches": matches, "review": review,
                      "review_status": review_status, "review_error": review_error})
    return {"pairs": pairs, "needs_review": bool(pairs), "comparison_count": len(comparisons),
            "candidate_count": len(pairs), "threshold": threshold, "review_failures": review_failures,
            "ai_review_limit": ai_review_limit,
            "ai_reviewed_count": sum(item["review_status"] == "ready" for item in pairs), "comparison_limit": None,
            "report": str(reports[0].relative_to(ctx.workspace))}


def read_comparisons(path):
    """Read both 6.0 and 6.3 report layouts without extracting untrusted archive paths."""
    with zipfile.ZipFile(path) as archive:
        def read(name):
            info = archive.getinfo(name)
            if info.file_size > 10 * 1024 * 1024:
                raise ValueError("JPlag report entry exceeds limit")
            return json.loads(archive.read(info))
        if "overview.json" in archive.namelist():
            return read("overview.json").get("top_comparisons", [])
        rows = read("topComparisons.json")
        mappings = read("submissionMappings.json").get("submissionIdsToComparisonFileName", {})
        result = []
        for row in rows:
            left, right = row["firstSubmission"], row["secondSubmission"]
            name = mappings.get(left, {}).get(right) or mappings.get(right, {}).get(left)
            details = read("comparisons/" + name) if name else {}
            result.append({"first_submission": left, "second_submission": right,
                           "similarities": row["similarities"], "matches": details.get("matches", [])})
        return result
