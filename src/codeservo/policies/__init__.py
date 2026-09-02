"""Reading the constitution a repository is controlled under.

A constitution is a control input: it is validated once, before anything is
measured, so a declaration that cannot hold ends the run rather than being
discovered by a gate.
"""

from .constitution import ConstitutionError, load_constitution

__all__ = ["ConstitutionError", "load_constitution"]
