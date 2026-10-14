"""Bounded CLI calls for short, reviewable AI tasks. No Flow generation jobs."""
import asyncio
import json
import shutil
import tempfile
from pathlib import Path

from agent.services import media_jobs
from agent.services.cli_providers import executable

_gate = asyncio.Semaphore(1)


def _codex_schema(node):
    """Require all output fields without changing Pydantic input defaults."""
    if isinstance(node, list):
        return [_codex_schema(item) for item in node]
    if not isinstance(node, dict):
        return node
    result = {}
    for key, value in node.items():
        if key in {"properties", "$defs", "definitions"}:
            result[key] = {name: _codex_schema(spec) for name, spec in value.items()}
        elif key != "default":
            result[key] = _codex_schema(value)
    if result.get("type") == "object" or "properties" in result:
        result["required"] = list(result.get("properties", {}))
        result["additionalProperties"] = False
    return result


async def complete(role: dict, prompt: str, *, schema: dict, images: tuple[Path, ...] = (), binary=None) -> str:
    provider, model, effort = role["provider"], role.get("model"), role.get("effort")
    binary = binary or executable(provider)
    if not binary:
        raise ValueError(f"Chưa tìm thấy {provider} trên máy. Chọn AI trong Cài đặt.")
    # A busy service refuses new interactive requests instead of accumulating
    # expensive, invisible jobs after browser retries.
    if _gate.locked():
        raise ValueError("AI đang xử lý một yêu cầu. Đợi hoàn tất rồi thử lại.")
    async with _gate:
        with tempfile.TemporaryDirectory(prefix="frameweaver-ai-") as directory:
            root = Path(directory)
            refs = []
            for index, image in enumerate(images):
                target = root / f"reference-{index + 1}{image.suffix}"
                shutil.copyfile(image, target)
                refs.append(target)
            if provider == "codex":
                output = root / "answer.txt"
                schema_path = root / "schema.json"
                schema_path.write_text(json.dumps(_codex_schema(schema)))
                args = [binary, "exec", "--ignore-user-config", "--ephemeral", "--skip-git-repo-check",
                        "--sandbox", "read-only", "-c", 'approval_policy="never"', "-c", 'web_search="disabled"',
                        "--disable", "shell_tool", "--disable", "apps", "--disable", "multi_agent",
                        "--disable", "browser_use", "--disable", "computer_use",
                        "--output-schema", str(schema_path), "-o", str(output)]
                for image in refs:
                    args += ["-i", str(image)]
                if model:
                    args += ["--model", model]
                if effort:
                    args += ["-c", f"model_reasoning_effort={json.dumps(effort)}"]
                await media_jobs.process(args + ["-"], timeout=240, cwd=directory, stdin=prompt)
                answer = output.read_text().strip() if output.is_file() else ""
            else:
                if refs:
                    prompt = ("Read only these reference images with the file-reading tool: "
                              + ", ".join(map(str, refs)) + ". Do not run shell commands.\n" + prompt)
                args = [binary, "-p"]
                if provider == "claude":
                    args += ["--tools", "Read" if refs else "", "--output-format", "text"]
                    if refs:
                        args += ["--allowedTools", "Read", "--add-dir", directory]
                else:
                    args += [prompt, "--output-format", "json", "--print-timeout", "230s"]
                    if refs:
                        args += ["--add-dir", directory]
                if model:
                    args += ["--model", model]
                if effort and not (provider == "agy" and model):
                    args += ["--effort", effort]
                answer = await media_jobs.process(args, timeout=240, cwd=directory, stdin=prompt if provider == "claude" else None)
                if provider == "agy":
                    from agent.services.video_reviewer import _parse_agy_envelope
                    answer = _parse_agy_envelope(answer)
            if not answer.strip():
                raise ValueError("AI trả kết quả trống. Chưa áp dụng thay đổi.")
            return answer


def parse_json(raw: str) -> dict:
    raw = raw.strip()
    if raw.startswith("```"):
        raw = raw.partition("\n")[2].rsplit("```", 1)[0]
    result = json.loads(raw)
    if not isinstance(result, dict):
        raise ValueError("AI trả định dạng không hợp lệ. Chưa áp dụng thay đổi.")
    return result
