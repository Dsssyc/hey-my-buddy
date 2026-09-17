"""Private, Python-to-Python C-Two contracts for Buddy control and completion."""
import c_two as cc


@cc.crm(namespace="hey.my.buddy", version="0.3.0")
class BuddyControl:
    def dispatch(self, request_json: str) -> str:
        ...


@cc.crm(namespace="hey.my.buddy", version="0.3.0")
class CompletionInbox:
    def submit(self, event_json: str) -> str:
        ...


@cc.crm(namespace="hey.my.buddy", version="0.3.0")
class NotificationReceiverControl:
    def bind(self, binding_json: str) -> str:
        ...

    def ping(self, token: str) -> str:
        ...

    def stop(self, token: str) -> str:
        ...
