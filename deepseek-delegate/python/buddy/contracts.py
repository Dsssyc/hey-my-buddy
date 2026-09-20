"""Named C-Two contracts for the Buddy blackboard.

Every operation is a named method with a validated request schema — there is no
public ``dispatch(method, JSON)`` facade and no Python-to-Node engine relay. Complex
payloads travel as bounded JSON strings. In the released C-Two version (0.5.1) an
ordinary Python ``str`` argument is still transported with the Python pickle
protocol, so this DTO is a **same-user Python API only**: it is not claimed to be
cross-language portable, no FastDB DTO is used here, and a non-Python client must
define its own C-Two contract instead of relying on these pickle payloads.

``BuddyControl`` owns mutations and reads. ``BuddyWait`` is a *separate* resource
name with its own bounded capacity, so many waiting clients can never consume the
control capacity that cancel, renew and result commits need.
"""
import c_two as cc

CONTRACT_VERSION = "0.4.0"
CONTROL_NAME = "buddy-control"
WAIT_NAME = "buddy-wait"


@cc.crm(namespace="hey.my.buddy", version=CONTRACT_VERSION)
class BuddyControl:
    """Authoritative board operations. Each returns a bounded JSON response string."""

    # -- service ------------------------------------------------------------
    def health(self, request_json: str) -> str:
        ...

    def capabilities(self, request_json: str) -> str:
        ...

    def service_control(self, request_json: str) -> str:
        ...

    def dashboard(self, request_json: str) -> str:
        ...

    def legacy_import(self, request_json: str) -> str:
        ...

    def runtime_info(self, request_json: str) -> str:
        ...

    # -- tasks --------------------------------------------------------------
    def task_submit(self, request_json: str) -> str:
        ...

    def task_get(self, request_json: str) -> str:
        ...

    def task_list(self, request_json: str) -> str:
        ...

    def task_result(self, request_json: str) -> str:
        ...

    def task_cancel(self, request_json: str) -> str:
        ...

    def task_retry(self, request_json: str) -> str:
        ...

    def task_acknowledge(self, request_json: str) -> str:
        ...

    def task_wait(self, request_json: str) -> str:
        ...

    # -- workers ------------------------------------------------------------
    def worker_register(self, request_json: str) -> str:
        ...

    def worker_claim(self, request_json: str) -> str:
        ...

    def worker_reconcile(self, request_json: str) -> str:
        ...

    def worker_renew(self, request_json: str) -> str:
        ...

    def worker_progress(self, request_json: str) -> str:
        ...

    def worker_result(self, request_json: str) -> str:
        ...

    def worker_release(self, request_json: str) -> str:
        ...

    def worker_list(self, request_json: str) -> str:
        ...

    # -- messages and artifacts --------------------------------------------
    def message_post(self, request_json: str) -> str:
        ...

    def message_update(self, request_json: str) -> str:
        ...

    def message_get(self, request_json: str) -> str:
        ...

    def message_list(self, request_json: str) -> str:
        ...

    def inquiry_observe(self, request_json: str) -> str:
        ...

    def artifact_list(self, request_json: str) -> str:
        ...

    # -- events -------------------------------------------------------------
    def events_read(self, request_json: str) -> str:
        ...


@cc.crm(namespace="hey.my.buddy", version=CONTRACT_VERSION)
class BuddyWait:
    """Bounded waits on a dedicated resource so waiting never blocks mutations."""

    def events_wait(self, request_json: str) -> str:
        ...

    def task_wait(self, request_json: str) -> str:
        ...

    def message_wait(self, request_json: str) -> str:
        ...

    def wait_capacity(self, request_json: str) -> str:
        ...
