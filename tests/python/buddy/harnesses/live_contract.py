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
    endpoint._contract = HarnessRunLive
    return endpoint.start()
