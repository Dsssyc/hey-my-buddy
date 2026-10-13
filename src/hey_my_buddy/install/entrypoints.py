"""Entry modules for the current runtime package.

The launcher and storage inventory share these service process names. This
module stays stdlib-only so the launcher can load it before dependencies exist.
"""

#: Entry modules for the current materialized runtime.
ENTRY_MODULES = {
    'hey_my_buddy': {'client': 'hey_my_buddy.protocol.client',
                     'daemon': 'hey_my_buddy.blackboard.service.daemon',
                     'supervisor': 'hey_my_buddy.buddy.runtime.supervisor',
                     'cli': 'hey_my_buddy.cli.main'},
}

#: The roles a coordinator addresses in the current runtime.
COORDINATED_ROLES = ('client', 'daemon', 'cli')

#: Long-lived service roles the storage inventory observes. The role of the
#: pattern that matches an observed ``-m`` module is the kind it reports.
SERVICE_ROLES = ('daemon', 'supervisor')
