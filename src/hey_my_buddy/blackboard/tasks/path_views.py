"""Bounded path samples shared by workspace errors and workflow views."""

MAX_OUTPUT_PATH_VIEW = 32
MAX_OUTPUT_PATH_SAMPLE_BYTES = 500


def _bounded_path_fields(source: dict, fields: tuple[str, ...]) -> dict:
    """Project path evidence without changing the authoritative record."""
    def sample_path(path: str) -> str:
        raw = path.encode("utf-8")
        return path if len(raw) <= MAX_OUTPUT_PATH_SAMPLE_BYTES else raw[:MAX_OUTPUT_PATH_SAMPLE_BYTES].decode(
            "utf-8", errors="ignore")

    view = dict(source)
    for key in fields:
        values = source.get(key)
        if isinstance(values, list):
            samples = []
            shortened = False
            for value in values[:MAX_OUTPUT_PATH_VIEW]:
                if isinstance(value, str):
                    sample = sample_path(value)
                    shortened |= len(sample) < len(value)
                elif isinstance(value, dict) and isinstance(value.get("path"), str):
                    path = value["path"]
                    sample = {**value, "path": sample_path(path)}
                    shortened |= len(sample["path"]) < len(path)
                else:
                    sample = value
                samples.append(sample)
            view[key] = samples
            view[key + "Count"] = len(values)
            view[key + "Truncated"] = len(values) > MAX_OUTPUT_PATH_VIEW
            if shortened:
                view[key + "SamplesShortened"] = True
    return view

