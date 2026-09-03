import re
import httpx
from . import SourceExtractor, SourceType


_MODEL_PATTERN = re.compile(
    r"^https://huggingface\.co/(?!datasets/|spaces/)([^/]+)/([^/]+)/?$"
)
_DATASET_PATTERN = re.compile(
    r"^https://huggingface\.co/datasets/([^/]+)/([^/]+)/?$"
)
_SPACE_PATTERN = re.compile(
    r"^https://huggingface\.co/spaces/([^/]+)/([^/]+)/?$"
)


class HuggingFaceExtractor(SourceExtractor):
    def __init__(self, token: str | None = None):
        self._token = token

    def _headers(self) -> dict:
        if self._token:
            return {"Authorization": f"Bearer {self._token}"}
        return {}

    def match(self, url: str) -> bool:
        return bool(
            _MODEL_PATTERN.match(url)
            or _DATASET_PATTERN.match(url)
            or _SPACE_PATTERN.match(url)
        )

    def kind(self) -> SourceType:
        return SourceType.HUGGINGFACE

    async def fetch_render(self, client: httpx.AsyncClient, url: str) -> str | None:
        entry = _parse_url(url)
        if entry is None:
            return None
        kind, org, repo = entry
        repo_id = f"{org}/{repo}"
        headers = self._headers()

        api_url = f"https://huggingface.co/api/{kind}/{repo_id}"
        try:
            resp = await client.get(api_url, headers=headers)
            if resp.status_code != 200:
                return None
            data = resp.json()
        except Exception:
            return None

        readme_prefix = "" if kind == "models" else f"{kind}/"
        readme_url = f"https://huggingface.co/{readme_prefix}{repo_id}/raw/main/README.md"
        readme = ""
        try:
            r = await client.get(readme_url, headers=headers)
            if r.status_code == 200:
                readme = r.text.strip()
        except Exception:
            pass

        config_info = ""
        if kind == "models":
            config_info = await _fetch_model_config(client, repo_id, headers)

        tree = {}
        try:
            r = await client.get(f"https://huggingface.co/api/{kind}/{repo_id}/tree/main", headers=headers)
            if r.status_code == 200:
                tree = r.json()
        except Exception:
            pass

        discussions = []
        try:
            r = await client.get(f"https://huggingface.co/api/{kind}/{repo_id}/discussions", headers=headers)
            if r.status_code == 200:
                payload = r.json()
                if isinstance(payload, dict):
                    payload = payload.get("discussions", [])
                discussions = payload
        except Exception:
            pass

        return _render_entry(kind, repo_id, data, readme, config_info, tree, discussions)


def _parse_url(url: str) -> tuple[str, str, str] | None:
    m = _MODEL_PATTERN.match(url)
    if m:
        return "models", m.group(1), m.group(2)
    m = _DATASET_PATTERN.match(url)
    if m:
        return "datasets", m.group(1), m.group(2)
    m = _SPACE_PATTERN.match(url)
    if m:
        return "spaces", m.group(1), m.group(2)
    return None


def _fmt_count(n) -> str:
    try:
        n = int(n)
    except (TypeError, ValueError):
        return "N/A"
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f}M"
    if n >= 1_000:
        return f"{n / 1_000:.1f}K"
    return str(n)


def _fmt_bytes(n) -> str:
    try:
        n = int(n)
    except (TypeError, ValueError):
        return ""
    if n >= 1_073_741_824:
        return f"({n / 1_073_741_824:.1f}GB)"
    if n >= 1_048_576:
        return f"({n / 1_048_576:.1f}MB)"
    if n >= 1_024:
        return f"({n / 1_024:.1f}KB)"
    return f"({n}B)"


async def _fetch_model_config(client: httpx.AsyncClient, repo_id: str, headers: dict) -> str:
    try:
        r = await client.get(
            f"https://huggingface.co/{repo_id}/raw/main/config.json",
            headers=headers,
        )
        if r.status_code != 200:
            return ""
        cfg = r.json()
    except Exception:
        return ""
    parts = []
    arch = cfg.get("architectures")
    if isinstance(arch, list) and arch:
        parts.append(f"**架构：** {', '.join(str(a) for a in arch)}")
    for key in ("model_type", "hidden_size", "num_hidden_layers", "num_attention_heads", "num_parameters"):
        v = cfg.get(key)
        if v is not None:
            parts.append(f"**{key}：** {v}")
    return " | ".join(parts) if parts else ""


def _render_entry(kind: str, repo_id: str, data: dict, readme: str,
                  config_info: str = "", tree: list | None = None,
                  discussions: list | None = None) -> str:
    title = data.get("id") or data.get("modelId") or repo_id
    lines = [f"# {title}"]
    meta = []

    if kind == "models":
        pipeline = data.get("pipeline_tag", "")
        if pipeline:
            meta.append(f"**任务：** {pipeline}")
        if data.get("downloads") is not None:
            meta.append(f"**下载：** {_fmt_count(data['downloads'])}")
        if data.get("likes") is not None:
            meta.append(f"**点赞：** {data['likes']}")
        if data.get("author"):
            meta.append(f"**作者：** {data['author']}")
        card = data.get("cardData") or {}
        if card.get("license"):
            meta.append(f"**许可：** {card['license']}")
    elif kind == "datasets":
        if data.get("downloads") is not None:
            meta.append(f"**下载：** {_fmt_count(data['downloads'])}")
        if data.get("likes") is not None:
            meta.append(f"**点赞：** {data['likes']}")
        if data.get("author"):
            meta.append(f"**作者：** {data['author']}")
        card = data.get("cardData") or {}
        if card.get("license"):
            meta.append(f"**许可：** {card['license']}")
    else:  # spaces
        if data.get("sdk"):
            meta.append(f"**SDK：** {data['sdk']}")
        runtime = data.get("runtime") or {}
        if runtime.get("stage"):
            meta.append(f"**状态：** {runtime['stage']}")
        if data.get("likes") is not None:
            meta.append(f"**点赞：** {data['likes']}")
        host = data.get("host", "")
        if host:
            meta.append(f"**Demo：** {host}")

    if meta:
        lines.append("> " + " | ".join(meta))
    lines.append("")

    if tags := data.get("tags"):
        meaningful = [t for t in tags if ":" not in t and t not in ("transformers", "pytorch", "tf", "jax", "rust", "onnx", "safetensors")]
        if meaningful:
            lines.append(f"**标签：** {', '.join(meaningful[:12])}")
            lines.append("")

    if config_info:
        lines.append(config_info)
        lines.append("")

    if tree:
        files = [t for t in tree if t.get("type") == "file"]
        if files:
            lines.append("**文件：**")
            for f in files[:15]:
                size = f.get("size") or 0
                size_str = _fmt_bytes(size) if size else ""
                lines.append(f"- `{f.get('path', '')}` {size_str}".rstrip())
            if len(files) > 15:
                lines.append(f"- *…共 {len(files)} 个文件*")
            lines.append("")

    if discussions:
        lines.append("**讨论：**")
        for d in discussions[:5]:
            title = d.get("title", "")
            num = d.get("num", "")
            status = d.get("status", "")
            author = (d.get("author") or {}).get("name", "unknown")
            lines.append(f"- **#{num}** {title} ({status}, @{author})")
        lines.append("")

    if readme:
        lines.append(readme[:8000])
        lines.append("")
    lines.append("---")
    source_prefix = "" if kind == "models" else f"{kind}/"
    lines.append(f"*来源：[Hugging Face {kind}](https://huggingface.co/{source_prefix}{repo_id})*")
    return "\n".join(lines)