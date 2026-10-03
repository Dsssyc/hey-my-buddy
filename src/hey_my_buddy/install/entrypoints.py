"""The one table of entry modules for every package generation a runtime ships.

The launcher and the storage inventory both derive service process names from
this table, so each generation's module names are defined exactly once. The
file stays stdlib-only: the launcher bootstrap loads before any package
dependency exists, also when it is executed directly as a script.
"""

#: Entry modules for each package generation a materialized runtime can ship.
#: A coordinator addresses a target runtime with that runtime's own modules: an
#: old-layout rollback target still imports ``buddy``, and imposing the
#: coordinator's current layout on it would strand that target's client, daemon
#: and CLI. The table is keyed by the top-level package each generation ships
#: under ``src/``; no compatibility name is installed into either generation.
ENTRY_MODULES = {
    'hey_my_buddy': {'client': 'hey_my_buddy.protocol.client',
                     'daemon': 'hey_my_buddy.blackboard.service.daemon',
                     'supervisor': 'hey_my_buddy.buddy.runtime.supervisor',
                     'cli': 'hey_my_buddy.cli.main'},
    'buddy': {'client': 'buddy.client', 'daemon': 'buddy.daemon',
              'supervisor': 'buddy.worker.supervisor', 'cli': 'buddy.cli'},
}

#: The roles a coordinator addresses in a target runtime's own namespace: the
#: exact contract ``entry_modules`` returns.
COORDINATED_ROLES = ('client', 'daemon', 'cli')

#: Long-lived service roles the storage inventory observes. The role of the
#: pattern that matches an observed ``-m`` module is the kind it reports.
SERVICE_ROLES = ('daemon', 'supervisor')
