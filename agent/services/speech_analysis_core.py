"""Local advisory measurements; these never rewrite speech or subtitles."""
from agent.media_limits import MAX_DURATION
from collections import defaultdict
import math
import re
import unicodedata


def inspect_signal(path, progress=lambda fraction: None, *, max_seconds=MAX_DURATION, limit=200):
    import numpy as np
    import soundfile as sf

    levels, peaks, invalid = [], [], []
    with sf.SoundFile(path) as audio:
        if not 1 <= audio.channels <= 8 or not 8000 <= audio.samplerate <= 192000:
            raise ValueError("Định dạng âm thanh vượt giới hạn kiểm tra.")
        duration = len(audio) / audio.samplerate
        frames = min(len(audio), round(max_seconds * audio.samplerate))
        window = max(1, round(audio.samplerate / 10))
        for offset in range(0, frames, window):
            block = audio.read(min(window, frames - offset), dtype="float32", always_2d=True)
            finite = np.isfinite(block)
            invalid.append(not bool(finite.all()))
            clean = np.where(finite, block, 0).astype("float64")
            # Check channels separately: opposite-phase stereo must not cancel.
            rms = float(np.sqrt(np.mean(clean * clean, axis=0).max()))
            levels.append(20 * math.log10(max(rms, 1e-10)))
            peaks.append(bool((np.abs(clean) >= .999).sum() >= 3))
            if offset % (window * 100) == 0:
                progress(offset / max(frames, 1))
    progress(1)
    scanned = frames / audio.samplerate
    if not levels:
        return {"duration": duration, "analyzed_seconds": scanned, "limited": False,
                "warnings": [{"kind": "empty", "start": 0, "end": 0}]}
    values = np.asarray(levels)
    active = values[values > -50]
    baseline = float(np.median(active)) if len(active) else -50
    rules = [("invalid", invalid, 0), ("clipping", peaks, 0),
             ("silence", values <= -50, 1 if len(active) else 0),
             ("quiet", (values > -50) & (values < max(-38, baseline - 12)), .6),
             ("loud", values > baseline + 12, .6)]
    warnings = []
    step = window / audio.samplerate
    for kind, mask, minimum in rules:
        opened = None
        for index, detected in enumerate([*mask, False]):
            if detected and opened is None:
                opened = index
            elif not detected and opened is not None:
                start, end = opened * step, min(index * step, scanned)
                if end - start + 1e-6 >= minimum:
                    warnings.append({"kind": kind, "start": round(start, 3), "end": round(end, 3)})
                opened = None
    warnings.sort(key=lambda item: (item["start"], item["kind"]))
    return {"duration": duration, "analyzed_seconds": scanned,
            "limited": duration > scanned + .001 or len(warnings) > limit, "warnings": warnings[:limit]}


def normalized_words(text):
    return re.findall(r"\w+", unicodedata.normalize("NFKC", text).casefold(), re.UNICODE)


def compare_words(expected, recognized):
    """Edit distance after punctuation/case normalization; accents remain meaningful."""
    left, right = normalized_words(expected), normalized_words(recognized)
    if len(left) > 2000 or len(right) > 4000:
        raise ValueError("Kết quả nhận dạng một câu dài bất thường.")
    previous = list(range(len(right) + 1))
    for i, word in enumerate(left, 1):
        row = [i]
        for j, other in enumerate(right, 1):
            row.append(min(row[-1] + 1, previous[j] + 1, previous[j - 1] + (word != other)))
        previous = row
    ratio = previous[-1] / max(1, len(left))
    return {"difference": round(ratio, 3), "needs_review": not right or ratio >= .35}


def recognized_cue(transcript, item):
    """One file is one cue: unreliable ASR token times cannot mix neighboring cues."""
    segments = transcript.get("transcription", [])
    heard = " ".join(segment.get("text", "").strip() for segment in segments).strip()
    probabilities = [token["p"] for segment in segments for token in segment.get("tokens", [])
                     if isinstance(token.get("p"), (int, float)) and normalized_words(token.get("text", ""))
                     and not token.get("text", "").startswith("[_")]
    score = compare_words(item["text"], heard)
    uncertain = bool(probabilities and sum(probabilities) / len(probabilities) < .55)
    return {"id": item["id"], "start": item["start"], "end": item["end"],
            "expected": item["text"], "recognized": heard, **score,
            "uncertain": uncertain, "needs_review": score["needs_review"] or uncertain}


def union_seconds(intervals):
    end, total = -1.0, 0.0
    for start, stop in sorted(intervals):
        total += max(0, stop - max(start, end))
        end = max(end, stop)
    return total


def assign_turns(cues, turns):
    """Map acoustic clusters to existing cues, flagging mixed/poorly covered cues."""
    turns = sorted(turns, key=lambda t: (t["start"], t["end"]))
    labels = {}
    for turn in turns:
        labels.setdefault(str(turn["speaker"]), f"Người nói {len(labels) + 1}")
    rows, active, cursor = [], [], 0
    for cue in cues:
        start, end = float(cue["start"]), float(cue["end"])
        active = [turn for turn in active if turn["end"] > start]
        while cursor < len(turns) and turns[cursor]["start"] < end:
            if turns[cursor]["end"] > start:
                active.append(turns[cursor])
            cursor += 1
        intervals = defaultdict(list)
        for turn in active:
            low, high = max(start, turn["start"]), min(end, turn["end"])
            if high > low:
                intervals[labels[str(turn["speaker"])]].append((low, high))
        weights = sorted(((label, union_seconds(spans)) for label, spans in intervals.items()), key=lambda x: -x[1])
        covered = union_seconds([span for spans in intervals.values() for span in spans])
        dominance = weights[0][1] / max(sum(w for _, w in weights), .001) if weights else 0
        coverage = covered / (end - start)
        rows.append({"id": cue["id"], "start": start, "end": end, "text": cue["text"],
                     "speaker": weights[0][0] if weights else "", "dominance": round(dominance, 3),
                     "coverage": round(coverage, 3), "needs_review": dominance < .8 or coverage < .35,
                     "alternatives": [label for label, _ in weights]})
    return {"speakers": list(labels.values()), "cues": rows,
            "turns": [{**turn, "speaker": labels[str(turn["speaker"])]} for turn in turns]}
