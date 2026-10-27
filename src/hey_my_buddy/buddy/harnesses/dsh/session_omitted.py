"""No ACP display omissions established by the available samples.

The desensitized native-usage-dsh.json fixture is a DSH session.v3 storage
sample, not a versioned ACP capture, and cannot establish ACP omissions or
complete reasoning coverage. fake_agent.py (agentInfo 0.0.1) is synthetic.
Do not infer an omitted reason from either sample.
"""

OMITTED = ()
