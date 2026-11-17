"""Spaces: several people on one Kazma (``docs/plans/SPACES_PLAN.md``).

A space is a private world of data; its id is what the stores call
``tenant_id``. An account is one person's login. Import from the module that
defines a name:

* :mod:`kazma_core.spaces.scope` -- the space and the asker bound to a
  request or a task, with no default once Spaces is enforced.
* :mod:`kazma_core.spaces.identity` -- accounts, spaces and memberships.
* :mod:`kazma_core.spaces.threads` -- the space and opener of a chat thread.
"""
