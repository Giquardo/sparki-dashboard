"""
Sparki webapp — server-rendered HTML layer.

This package owns everything related to the Jinja2 + HTMX UI:
  - `session.py`         : signed-cookie session + user lookup dependency
  - `templates_env.py`   : Jinja2 environment, filters, context helpers
  - `routes.py`          : public HTML routes (/, /login, /logout, ...)

The JSON REST API in `app/api/...`-shaped routers remains untouched —
existing 39 integration tests must keep passing without modification.
"""
