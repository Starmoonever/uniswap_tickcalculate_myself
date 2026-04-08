from pathlib import Path


CONFIG_PATH = Path(__file__).resolve().parent / "config.yaml"


def _parse_scalar(value):
    value = value.strip()
    if not value:
        return ""
    if value.startswith(("'", '"')) and value.endswith(("'", '"')) and len(value) >= 2:
        return value[1:-1]
    lowered = value.lower()
    if lowered == "true":
        return True
    if lowered == "false":
        return False
    if lowered == "null":
        return None
    if value.lstrip("-").isdigit():
        return int(value)
    return value


def load_config(path=CONFIG_PATH):
    if not Path(path).exists():
        raise FileNotFoundError(f"未找到配置文件: {path}")

    root = {}
    stack = [(-1, root)]

    for raw_line in Path(path).read_text(encoding="utf-8").splitlines():
        line = raw_line.rstrip()
        stripped = line.strip()

        if not stripped or stripped.startswith("#"):
            continue

        indent = len(line) - len(line.lstrip(" "))
        key, sep, value = stripped.partition(":")
        if not sep:
            raise ValueError(f"无法解析配置行: {raw_line}")

        while stack and indent <= stack[-1][0]:
            stack.pop()

        current = stack[-1][1]
        key = key.strip()
        value = value.strip()

        if value:
            current[key] = _parse_scalar(value)
            continue

        child = {}
        current[key] = child
        stack.append((indent, child))

    return root
