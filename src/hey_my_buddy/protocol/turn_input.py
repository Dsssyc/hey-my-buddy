"""Identity frozen in a service-written Worker turn input."""
import json


def turn_buddy(input_json: str | None) -> dict | None:
    """Read the turn's own buddy, never the run's later choice or an attempt triple."""
    return json.loads(input_json)["context"]["buddy"] if input_json is not None else None
