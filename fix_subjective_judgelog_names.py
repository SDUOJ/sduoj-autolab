#!/usr/bin/env python3
"""Check and repair subjective review name mismatches from auto task logs.

Workflow (by groupId):
1. Read auto task runs (`task_type=subjective_review`) under target group(s).
2. Parse latest `tag=result` JSON and all `tag=log` entries that contain `<schema>...</schema>` + `judgeLog`.
3. Compare result judgeLog names against the best matched schema-log candidate.
4. If inconsistent, remap by maximum string matching and rebuild corrected `result.judgeLog`.
5. With --commit, repair the historical result log only. Current AUTO answers
   require --update-current-answer and a matching latest-run/result snapshot.
6. Report every score that would change within a name-mismatch task.
"""

from __future__ import annotations

import argparse
import json
import math
import re
from dataclasses import dataclass
from datetime import datetime
from difflib import SequenceMatcher
from typing import Any, Dict, List, Optional, Sequence, Tuple

# Import database models only inside the database operations, after parsing CLI options.
# The module and --help must be usable without opening a configured database.

SCHEMA_PATTERN = re.compile(r"<schema>((?:(?!<schema>).)*?)</schema>", re.DOTALL | re.IGNORECASE)


@dataclass
class JudgeRow:
    name: str
    score: float
    jscore: float


@dataclass
class ParsedTask:
    task_id: str
    group_id: Optional[int]
    psid: Optional[int]
    username: Optional[str]
    payload: Dict[str, Any]
    log_id: int
    log_judge: List[JudgeRow]
    result_log_id: int
    result_payload: Dict[str, Any]
    result_judge: List[JudgeRow]


@dataclass
class SchemaLogCandidate:
    log: AutoTaskRunLog
    payload: Dict[str, Any]
    judge_rows: List[JudgeRow]


def parse_args() -> argparse.Namespace:
    now = datetime.now().strftime("%Y%m%d_%H%M%S")
    parser = argparse.ArgumentParser(
        description="Check tag=log vs tag=result judgeLog name consistency and repair mismatches."
    )
    parser.add_argument(
        "--group-id",
        type=int,
        action="append",
        required=True,
        help="Target groupId (repeatable)",
    )
    parser.add_argument("--username", type=str, help="Only process one username")
    parser.add_argument("--task-id", type=str, action="append", help="Only process specific task id (repeatable)")
    parser.add_argument("--only-auto", action="store_true", help="Only select tasks whose answer sheet is locked by <AUTO>")
    parser.add_argument("--min-similarity", type=float, default=0.70, help="Minimum matching similarity (0~1)")
    parser.add_argument("--limit", type=int, default=0, help="Max runs to scan, 0 means no limit")
    parser.add_argument("--commit", action="store_true", help="Persist fixes to DB (default dry-run)")
    parser.add_argument("--update-current-answer", action="store_true",
                        help="With --commit, also repair a matching current AUTO answer; stale or human grades are skipped")
    parser.add_argument("--report", type=str, default=f"judge_log_fix_report_{now}.jsonl", help="Mismatch report path")
    args = parser.parse_args()
    if not 0 <= args.min_similarity <= 1:
        parser.error("--min-similarity must be in 0..1")
    if args.limit < 0 or any(group <= 0 for group in args.group_id):
        parser.error("group IDs must be positive and --limit must be non-negative")
    if args.update_current_answer and not args.commit:
        parser.error("--update-current-answer requires --commit; preview historical mismatches first")
    return args


def _to_float(value: Any, default: float = 0.0) -> float:
    try:
        val = float(value)
        if not math.isfinite(val):
            return default
        return val
    except Exception:
        return default


def _strip_noise(text: str) -> str:
    t = (text or "").strip()
    t = re.sub(r"[（(]\s*满分[^）)]*[）)]\s*$", "", t)
    t = re.sub(r"^\s*\d+\s*[.、:：]\s*", "", t)
    t = re.sub(r"\s+", "", t)
    return t


def _similarity(a: str, b: str) -> float:
    ra = (a or "").strip()
    rb = (b or "").strip()
    if not ra or not rb:
        return 0.0
    if ra == rb:
        return 1.0
    na = _strip_noise(ra)
    nb = _strip_noise(rb)
    if not na or not nb:
        return 0.0
    if na == nb:
        return 0.995
    if na in nb or nb in na:
        shorter = min(len(na), len(nb))
        longer = max(len(na), len(nb))
        return 0.92 + 0.08 * (shorter / longer)
    r1 = SequenceMatcher(None, na, nb).ratio()
    r2 = SequenceMatcher(None, ra, rb).ratio()
    return max(r1, r2 * 0.95)


def _maximum_weight_match(sim: List[List[float]], threshold: float) -> Dict[int, int]:
    if not sim:
        return {}
    m = len(sim)
    n = len(sim[0]) if sim[0] else 0
    if n == 0:
        return {}

    if n <= 16:
        from functools import lru_cache

        @lru_cache(maxsize=None)
        def solve(i: int, used_mask: int) -> Tuple[float, Tuple[Tuple[int, int], ...]]:
            if i >= m:
                return 0.0, tuple()
            best_score, best_pairs = solve(i + 1, used_mask)
            for j in range(n):
                if (used_mask >> j) & 1:
                    continue
                w = sim[i][j]
                if w < threshold:
                    continue
                nxt_score, nxt_pairs = solve(i + 1, used_mask | (1 << j))
                cand = w + nxt_score
                if cand > best_score:
                    best_score = cand
                    best_pairs = ((i, j),) + nxt_pairs
            return best_score, best_pairs

        _, pairs = solve(0, 0)
        return {i: j for i, j in pairs}

    pairs: List[Tuple[float, int, int]] = []
    for i in range(m):
        for j in range(n):
            w = sim[i][j]
            if w >= threshold:
                pairs.append((w, i, j))
    pairs.sort(reverse=True, key=lambda x: x[0])

    res: Dict[int, int] = {}
    used_i = set()
    used_j = set()
    for _, i, j in pairs:
        if i in used_i or j in used_j:
            continue
        used_i.add(i)
        used_j.add(j)
        res[i] = j
    return res


def _parse_judge_rows(payload: Any) -> List[JudgeRow]:
    if not isinstance(payload, dict):
        return []
    raw = payload.get("judgeLog")
    if not isinstance(raw, list):
        return []
    out: List[JudgeRow] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        out.append(
            JudgeRow(
                name=str(item.get("name", "")).strip(),
                score=_to_float(item.get("score", 0.0), 0.0),
                jscore=_to_float(item.get("jScore", 0.0), 0.0),
            )
        )
    return out


def _try_parse_json(content: Optional[str]) -> Optional[Dict[str, Any]]:
    if not content:
        return None
    try:
        data = json.loads(content)
    except Exception:
        return None
    return data if isinstance(data, dict) else None


def _parse_schema_json_from_markdown(content: Optional[str]) -> Optional[Dict[str, Any]]:
    if not content:
        return None
    matches = SCHEMA_PATTERN.findall(content)
    if not matches:
        return None
    raw = matches[-1].strip()
    try:
        data = json.loads(raw)
    except Exception:
        return None
    return data if isinstance(data, dict) else None


def _ordered_exact_presence(result_names: Sequence[str], log_names: Sequence[str]) -> Dict[int, int]:
    """Map result index -> log index by exact name and order-preserving scan."""
    mapping: Dict[int, int] = {}
    j = 0
    for i, rn in enumerate(result_names):
        found = False
        while j < len(log_names):
            if log_names[j] == rn:
                mapping[i] = j
                j += 1
                found = True
                break
            j += 1
        if not found:
            break
    return mapping


def _build_similarity_matrix(result_rows: List[JudgeRow], log_rows: List[JudgeRow]) -> List[List[float]]:
    return [[_similarity(r.name, l.name) for l in log_rows] for r in result_rows]


def _build_corrected_result(
    result_payload: Dict[str, Any],
    result_rows: List[JudgeRow],
    log_rows: List[JudgeRow],
    min_similarity: float,
) -> Tuple[Dict[str, Any], Dict[int, int], List[List[float]]]:
    sim = _build_similarity_matrix(result_rows, log_rows)
    mapping = _maximum_weight_match(sim, min_similarity)

    corrected_rows: List[Dict[str, Any]] = []
    for i, rr in enumerate(result_rows):
        src_idx = mapping.get(i)
        if src_idx is None:
            new_j = rr.jscore
        else:
            new_j = log_rows[src_idx].jscore
        new_j = max(0.0, min(rr.score, float(new_j)))
        corrected_rows.append({
            "name": rr.name,
            "score": rr.score,
            "jScore": new_j,
        })

    corrected = dict(result_payload)
    corrected["judgeLog"] = corrected_rows
    return corrected, mapping, sim


def _judge_rows_equal(a: List[JudgeRow], b_payload: Dict[str, Any]) -> bool:
    b_rows = _parse_judge_rows(b_payload)
    if len(a) != len(b_rows):
        return False
    for x, y in zip(a, b_rows):
        if x.name != y.name:
            return False
        if abs(x.score - y.score) > 1e-6:
            return False
        if abs(x.jscore - y.jscore) > 1e-6:
            return False
    return True


def _get_latest_logs(logs: List[AutoTaskRunLog]) -> Tuple[Optional[AutoTaskRunLog], Optional[AutoTaskRunLog]]:
    payload_log = None
    result_log = None

    for lg in logs:
        if lg.tag == "payload" and payload_log is None:
            payload_log = lg
        elif lg.tag == "result":
            result_log = lg
    return payload_log, result_log


def _extract_schema_log_candidates(logs: List[AutoTaskRunLog]) -> List[SchemaLogCandidate]:
    candidates: List[SchemaLogCandidate] = []
    for lg in logs:
        if lg.tag != "log":
            continue
        payload = _parse_schema_json_from_markdown(lg.content)
        if payload is None:
            continue
        rows = _parse_judge_rows(payload)
        if not rows:
            continue
        candidates.append(SchemaLogCandidate(log=lg, payload=payload, judge_rows=rows))
    return candidates


def _pick_best_schema_candidate(
    result_rows: List[JudgeRow],
    candidates: List[SchemaLogCandidate],
) -> Tuple[SchemaLogCandidate, Dict[int, int], bool]:
    if not candidates:
        raise ValueError("No schema log candidates")

    result_names = [x.name for x in result_rows]
    best: Optional[Tuple[Tuple[int, int, float, int], SchemaLogCandidate, Dict[int, int], bool]] = None

    for cand in candidates:
        log_names = [x.name for x in cand.judge_rows]
        exact_presence = _ordered_exact_presence(result_names, log_names)
        exact_count = len(exact_presence)
        exact_complete = exact_count == len(result_names)
        sim = _build_similarity_matrix(result_rows, cand.judge_rows)
        match_all = _maximum_weight_match(sim, 0.0)
        sim_sum = 0.0
        for i, j in match_all.items():
            sim_sum += sim[i][j]

        score = (
            1 if exact_complete else 0,  # exact full match first
            exact_count,                 # then exact ordered coverage
            sim_sum,                     # then fuzzy similarity
            int(cand.log.log_id),        # finally prefer newer log
        )
        if best is None or score > best[0]:
            best = (score, cand, exact_presence, exact_complete)

    assert best is not None
    _, best_cand, best_exact_presence, best_exact_complete = best
    return best_cand, best_exact_presence, best_exact_complete


def _load_target_tasks(session, args: argparse.Namespace) -> List[AutoTaskRun]:
    from db import AutoTaskRun

    q = session.query(AutoTaskRun).filter(
        AutoTaskRun.task_type == "subjective_review",
        AutoTaskRun.status == "success",
        AutoTaskRun.groupId.in_(args.group_id),
    ).order_by(AutoTaskRun.id.asc())

    if args.username:
        q = q.filter(AutoTaskRun.username == args.username)
    if args.task_id:
        q = q.filter(AutoTaskRun.id.in_(args.task_id))
    if args.limit and args.limit > 0:
        q = q.limit(args.limit)

    return q.all()


def _load_task_logs(session, task_ids: List[str]) -> Dict[str, List[AutoTaskRunLog]]:
    from db import AutoTaskRunLog

    if not task_ids:
        return {}
    rows = (
        session.query(AutoTaskRunLog)
        .filter(
            AutoTaskRunLog.task_id.in_(task_ids),
            AutoTaskRunLog.tag.in_(["payload", "log", "result"]),
        )
        .order_by(AutoTaskRunLog.task_id.asc(), AutoTaskRunLog.log_id.asc())
        .all()
    )
    bucket: Dict[str, List[AutoTaskRunLog]] = {}
    for row in rows:
        bucket.setdefault(str(row.task_id), []).append(row)
    return bucket


def _find_answer_detail(session, psid: int, username: str, gid: int, pid: int, lock=False):
    from db import ProblemSetAnswerSheet, ProblemSetAnswerSheetDetail

    as_row = (
        session.query(ProblemSetAnswerSheet)
        .filter(
            ProblemSetAnswerSheet.psid == psid,
            ProblemSetAnswerSheet.username == username,
        )
        .first()
    )
    if as_row is None:
        return None
    query = (
        session.query(ProblemSetAnswerSheetDetail)
        .filter(
            ProblemSetAnswerSheetDetail.asid == as_row.asid,
            ProblemSetAnswerSheetDetail.gid == gid,
            ProblemSetAnswerSheetDetail.pid == pid,
        )
    )
    if lock:
        query = query.populate_existing().with_for_update()
    return query.first()


def _extract_payload_fields(run: AutoTaskRun, payload: Dict[str, Any]) -> Tuple[Optional[int], Optional[int], Optional[int], Optional[str]]:
    psid = payload.get("psid") if isinstance(payload, dict) else None
    gid = payload.get("gid") if isinstance(payload, dict) else None
    pid = payload.get("pid") if isinstance(payload, dict) else None
    username = payload.get("username") if isinstance(payload, dict) else None

    if psid is None:
        psid = run.psid
    if username is None:
        username = run.username

    try:
        psid = int(psid) if psid is not None else None
    except Exception:
        psid = None
    try:
        gid = int(gid) if gid is not None else None
    except Exception:
        gid = None
    try:
        pid = int(pid) if pid is not None else None
    except Exception:
        pid = None
    username = str(username) if username is not None else None
    return psid, gid, pid, username


class RepairConflict(RuntimeError):
    """The scanned task/result changed before this repair transaction."""


def _latest_answer_run(session, identity):
    """Read all matching task identities, including runs outside CLI filters."""
    from db import AutoTaskRun, AutoTaskRunLog

    psid, gid, pid, username = identity
    runs = session.query(AutoTaskRun).filter(
        AutoTaskRun.task_type == "subjective_review",
        AutoTaskRun.psid == psid, AutoTaskRun.username == username,
    ).order_by(AutoTaskRun.id).populate_existing().with_for_update().all()
    matches = []
    for other in runs:
        payload_log = session.query(AutoTaskRunLog).filter_by(
            task_id=other.id, tag="payload",
        ).order_by(AutoTaskRunLog.log_id).first()
        payload = _try_parse_json(payload_log.content) if payload_log else None
        # Missing identities cannot establish that the other run is unrelated.
        if payload is None:
            return None
        if _extract_payload_fields(other, payload) != identity:
            continue
        if other.status in {"pending", "running", "blocked", "retrying"}:
            return None
        if other.status == "success":
            if other.create_time is None:
                return None
            matches.append(other)
    if not matches:
        return None
    latest = max(matches, key=lambda item: (item.end_time or item.create_time, item.create_time, item.id))
    return latest.id


def _repair_current_answer(session, run, original, corrected):
    from db import AutoTaskRunLog, ProblemSetAnswerSheetDetail

    payload_log = session.query(AutoTaskRunLog).filter_by(
        task_id=run.id, tag="payload",
    ).order_by(AutoTaskRunLog.log_id).first()
    payload = _try_parse_json(payload_log.content) if payload_log else None
    if payload is None:
        return "missing-payload"
    identity = _extract_payload_fields(run, payload)
    psid, gid, pid, username = identity
    if (not all(value is not None and value > 0 for value in (psid, gid, pid)) or not username
            or run.psid != psid or run.username != username
            or (run.problemId is not None and run.problemId != pid)):
        return "unproven-answer-identity"
    detail = _find_answer_detail(session, psid, username, gid, pid, lock=True)
    if detail is None:
        return "missing-answer"
    if detail.judgeLock_username != "<AUTO>":
        return "human-or-unlocked-answer"
    # New grading candidates require teacher adoption through the normal API.
    if original.get("candidate"):
        return "candidate-requires-adoption"
    if _latest_answer_run(session, identity) != run.id:
        return "not-latest-completed-run"
    try:
        current_rows = json.loads(detail.judgeLog)
    except (TypeError, ValueError):
        return "answer-snapshot-differs"
    if (current_rows != original.get("judgeLog")
            or detail.judgeComment != original.get("judgeComment")):
        return "answer-snapshot-differs"
    changed = session.query(ProblemSetAnswerSheetDetail).filter(
        ProblemSetAnswerSheetDetail.asd_id == detail.asd_id,
        ProblemSetAnswerSheetDetail.judgeLock_username == "<AUTO>",
        ProblemSetAnswerSheetDetail.judgeLog == detail.judgeLog,
        ProblemSetAnswerSheetDetail.judgeComment == detail.judgeComment,
    ).update({"judgeLog": json.dumps(corrected["judgeLog"], ensure_ascii=False),
              "judgeComment": corrected.get("judgeComment")}, synchronize_session=False)
    if changed != 1:
        raise RepairConflict("Current answer changed during repair")
    return "updated"


def apply_repair(session, task_id, log_id, original_content, corrected, update_current_answer=False):
    """Caller commits one transaction; any log/answer CAS conflict rolls it back."""
    from db import AutoTaskRun, AutoTaskRunLog

    run = session.query(AutoTaskRun).filter_by(id=task_id).populate_existing().with_for_update().one_or_none()
    if run is None or run.status != "success":
        raise RepairConflict("Task is no longer completed")
    result = session.query(AutoTaskRunLog).filter_by(task_id=task_id, tag="result").order_by(
        AutoTaskRunLog.log_id.desc(),
    ).populate_existing().with_for_update().first()
    if result is None or result.log_id != log_id or result.content != original_content:
        raise RepairConflict("Result log changed after preview")
    original = _try_parse_json(original_content)
    if original is None:
        raise RepairConflict("Original result is invalid")
    answer = _repair_current_answer(session, run, original, corrected) if update_current_answer else "not-requested"
    changed = session.query(AutoTaskRunLog).filter_by(log_id=log_id, task_id=task_id, tag="result").filter(
        AutoTaskRunLog.content == original_content,
    ).update({"content": json.dumps(corrected, ensure_ascii=False)}, synchronize_session=False)
    if changed != 1:
        raise RepairConflict("Result log changed during repair")
    return {"history": "updated", "answer": answer}


def main() -> int:
    args = parse_args()

    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from db import link

    engine = create_engine(link)
    Session = sessionmaker(bind=engine)
    session = Session()

    scanned = 0
    mismatch = 0
    updated = 0
    answer_updated = 0
    skipped = 0

    try:
        runs = _load_target_tasks(session, args)
        log_map = _load_task_logs(session, [str(r.id) for r in runs])

        with open(args.report, "w", encoding="utf-8") as fp:
            for run in runs:
                scanned += 1
                task_id = str(run.id)
                logs = log_map.get(task_id, [])
                payload_log, result_log = _get_latest_logs(logs)
                schema_candidates = _extract_schema_log_candidates(logs)

                if payload_log is None or result_log is None or not schema_candidates:
                    skipped += 1
                    continue

                payload = _try_parse_json(payload_log.content)
                result_payload = _try_parse_json(result_log.content)
                if payload is None or result_payload is None:
                    skipped += 1
                    continue

                result_rows = _parse_judge_rows(result_payload)
                if not result_rows:
                    skipped += 1
                    continue

                selected_cand, _, exact_complete = _pick_best_schema_candidate(
                    result_rows=result_rows,
                    candidates=schema_candidates,
                )
                log_rows = selected_cand.judge_rows

                psid, gid, pid, username = _extract_payload_fields(run, payload)
                if psid is None or gid is None or pid is None or not username:
                    skipped += 1
                    continue

                answer_detail = _find_answer_detail(session, psid, username, gid, pid)
                if args.only_auto:
                    if answer_detail is None or answer_detail.judgeLock_username != "<AUTO>":
                        skipped += 1
                        continue

                if exact_complete:
                    continue

                corrected_payload, match_map, sim = _build_corrected_result(
                    result_payload=result_payload,
                    result_rows=result_rows,
                    log_rows=log_rows,
                    min_similarity=args.min_similarity,
                )

                # Preview every value the corrected payload would write, including
                # exact-name matches and unmatched values clamped to their maximum.
                inconsistent_items: List[Dict[str, Any]] = []
                corrected_rows = _parse_judge_rows(corrected_payload)
                for i, rr in enumerate(result_rows):
                    m_idx = match_map.get(i)
                    m_name = log_rows[m_idx].name if m_idx is not None else None
                    m_sim = sim[i][m_idx] if m_idx is not None else 0.0
                    matched_jscore = (log_rows[m_idx].jscore if m_idx is not None else None)
                    new_jscore = corrected_rows[i].jscore

                    if new_jscore == rr.jscore:
                        continue

                    inconsistent_items.append(
                        {
                            "result_index": i,
                            "result_name": rr.name,
                            "result_score": rr.score,
                            "old_result_jScore": rr.jscore,
                            "matched_log_index": m_idx,
                            "matched_log_name": m_name,
                            "matched_log_jScore": matched_jscore,
                            "similarity": round(float(m_sim), 6),
                            "new_result_jScore": new_jscore,
                        }
                    )

                # If all mismatches are only text differences with equal score, skip silently.
                if not inconsistent_items:
                    continue

                mismatch += 1

                repair = {"history": "preview", "answer": "not-requested"}
                if args.commit:
                    # Keep the scan session read-only. Refresh and compare each result
                    # in a separate transaction rather than flushing scanned ORM rows.
                    try:
                        with Session.begin() as write_session:
                            repair = apply_repair(write_session, task_id, result_log.log_id,
                                                  result_log.content, corrected_payload,
                                                  args.update_current_answer)
                        updated += 1
                        answer_updated += repair["answer"] == "updated"
                    except RepairConflict as error:
                        repair = {"history": "conflict", "answer": "not-written", "reason": str(error)}

                fp.write(
                    json.dumps(
                        {
                            "status": repair["history"] if args.commit else "dry_run_mismatch",
                            "task_id": task_id,
                            "groupId": run.groupId,
                            "psid": psid,
                            "gid": gid,
                            "pid": pid,
                            "username": username,
                            "schema_log_candidates": len(schema_candidates),
                            "log_id": selected_cand.log.log_id,
                            "result_log_id": result_log.log_id,
                            "write_mode": ("historical_log_and_matching_auto_answer" if args.update_current_answer
                                           else "historical_log_only") if args.commit else "dry_run",
                            "repair": repair,
                            "inconsistent_items": inconsistent_items,
                        },
                        ensure_ascii=False,
                    )
                    + "\n"
                )

                print(
                    f"[{repair['history'] if args.commit else 'dry-run'}] "
                    f"task={task_id} groupId={run.groupId} psid={psid} user={username} "
                    f"inconsistent={len(inconsistent_items)}"
                )

        print("=" * 72)
        print(
            f"Done. scanned={scanned}, mismatch={mismatch}, updated={updated}, answer_updated={answer_updated}, skipped={skipped}, "
            f"mode={'commit' if args.commit else 'dry-run'}"
        )
        print(f"Report written to: {args.report}")
        return 0
    finally:
        try:
            session.close()
        except Exception:
            pass


if __name__ == "__main__":
    raise SystemExit(main())
