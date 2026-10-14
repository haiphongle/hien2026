"""Cancellable subprocess for audio measurements and optional ONNX inference."""
import json
from pathlib import Path
import subprocess
import sys
import wave

from agent.services.speech_analysis_core import inspect_signal


def progress(value, stage):
    print(json.dumps({"progress": round(value), "stage": stage}), flush=True)


def prepare(config):
    """Normalize each full cue independently, retaining its identity for Whisper."""
    items, seconds = [], 0
    for index, item in enumerate(config["items"]):
        output = Path(config["directory"]) / f"cue-{index}.wav"
        subprocess.run(["ffmpeg", "-nostdin", "-v", "error", "-y", "-i", item["path"],
                        "-map", "0:a:0", "-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le", str(output)],
                       check=True, timeout=600, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        with wave.open(str(output)) as audio:
            duration = audio.getnframes() / audio.getframerate()
        seconds += duration
        if not duration or seconds > 10800:
            raise ValueError("Lời đọc rỗng hoặc tổng lời đọc quá dài để kiểm tra trong một lượt.")
        items.append({**item, "path": str(output), "duration": duration})
        progress((index + 1) / len(config["items"]) * 100, "Chuẩn bị lời đọc")
    return items


def signals(config):
    results = []
    for index, item in enumerate(config["items"]):
        try:
            measured = inspect_signal(item["path"], lambda fraction: progress(
                (index + fraction) / len(config["items"]) * 100, "Kiểm tra tín hiệu âm thanh"))
        except (ValueError, RuntimeError, OSError) as exc:
            measured = {"duration": 0, "analyzed_seconds": 0, "limited": False,
                        "warnings": [{"kind": "unreadable", "start": 0, "end": 0}], "error": str(exc)}
        results.append({k: v for k, v in item.items() if k != "path"} | measured)
    return results


def speakers(config):
    import soundfile as sf
    import sherpa_onnx

    progress(0, "Nạp bộ nhận diện người nói")
    options = sherpa_onnx.OfflineSpeakerDiarizationConfig(
        segmentation=sherpa_onnx.OfflineSpeakerSegmentationModelConfig(
            pyannote=sherpa_onnx.OfflineSpeakerSegmentationPyannoteModelConfig(model=config["segmentation"]),
            num_threads=2, provider="cpu"),
        embedding=sherpa_onnx.SpeakerEmbeddingExtractorConfig(model=config["embedding"], num_threads=2, provider="cpu"),
        clustering=sherpa_onnx.FastClusteringConfig(num_clusters=config.get("num_speakers") or -1, threshold=.5),
        min_duration_on=.3, min_duration_off=.5)
    if not options.validate():
        raise ValueError("Bộ nhận diện người nói thiếu hoặc lỗi model.")
    engine = sherpa_onnx.OfflineSpeakerDiarization(options)
    samples, rate = sf.read(config["wav"], dtype="float32")
    if rate != engine.sample_rate or samples.ndim != 1:
        raise ValueError("Âm thanh nhận diện phải là mono 16 kHz.")
    def callback(done, total):
        progress(done / max(total, 1) * 95, "Phân tích lượt nói")
        return 0
    turns = engine.process(samples, callback=callback).sort_by_start_time()
    return [{"start": round(t.start, 3), "end": round(t.end, 3), "speaker": str(t.speaker)} for t in turns]


def main():
    config = json.load(sys.stdin)
    result = {"signal": signals, "prepare": prepare, "speakers": speakers}[config["mode"]](config)
    Path(config["output"]).write_text(json.dumps(result, ensure_ascii=False), encoding="utf-8")


if __name__ == "__main__":
    main()
