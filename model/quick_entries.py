"""Homepage entry contract: string IDs and Unix millisecond timestamps."""

DAY_MS = 24 * 60 * 60 * 1000


def problem_set_entries(problem_sets, now):
    result = {"running": [], "upcoming": []}
    for ps in problem_sets:
        if (ps.get("config") or {}).get("useSameSE") == 1:
            windows = [{"tm_start": ps.get("tm_start"), "tm_end": ps.get("tm_end")}]
        else:
            windows = [ts for group in ps.get("groupInfo") or []
                       for ts in group.get("timeSetting") or []]
        running, upcoming = [], []
        for window in windows:
            try:
                start, end = int(window["tm_start"]), int(window["tm_end"])
            except (ValueError, TypeError, KeyError):
                continue
            if start >= end or end <= now:
                continue
            if start <= now:
                running.append((start, end))
            elif start <= now + DAY_MS:
                upcoming.append((start, end))
        # A currently open window takes precedence over a future window in another section.
        if running:
            bucket, (start, end) = "running", min(running, key=lambda pair: (pair[1], pair[0]))
        elif upcoming:
            bucket, (start, end) = "upcoming", min(upcoming)
        else:
            continue
        result[bucket].append({
            "type": "problemSet", "id": str(ps["psid"]), "title": ps["name"],
            "startTime": str(start), "endTime": str(end),
            "groupId": str(ps["groupId"]) if ps.get("groupId") is not None else None,
            "groupTitle": None, "tag": ps.get("tag"),
        })
    for bucket, time_key in (("running", "endTime"), ("upcoming", "startTime")):
        result[bucket].sort(key=lambda entry: (int(entry[time_key]), entry["id"]))
    return result
