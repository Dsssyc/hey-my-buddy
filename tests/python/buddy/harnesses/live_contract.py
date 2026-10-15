"""Test CRM for the generic live backend; production uses ExecutionLive."""
import c_two as cc

from hey_my_buddy.protocol.contracts import CONTRACT_VERSION


@cc.crm(namespace="hey.my.buddy.test", version=CONTRACT_VERSION)
class HarnessRunLive:
    def request(self, request_json: str) -> str:
        ...

    def observe(self, request_json: str) -> str:
        ...

    def capabilities(self, request_json: str) -> str:
        ...


def register_test_endpoint(endpoint):
    """Expose an otherwise in-process channel for a standalone transport test."""
    import secrets
    from hey_my_buddy.buddy.harnesses.c_two_live import random_person_name
    endpoint._contract = HarnessRunLive
    endpoint._name = random_person_name()
    endpoint._instance_id = secrets.token_hex(32)
    endpoint._token = secrets.token_hex(32)
    return endpoint.start()
