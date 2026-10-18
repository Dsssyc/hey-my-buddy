"""Read a workflow request's own reason or its referenced decision's reason."""
from ...protocol.reasons import reason_from_json


def request_reason(connection, row):
    if row["reason_code"] is None and row["decision_id"] is not None:
        row = connection.execute(
            "SELECT reason_code,reason_params_json FROM evaluation_decisions WHERE decision_id=?",
            (row["decision_id"],),
        ).fetchone()
    return reason_from_json(row["reason_code"], row["reason_params_json"])
