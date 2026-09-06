"""Session defaults for the test suite.

`DISABLE_TELEGRAM_ADMIN` defaults to true HERE and only here. The two cases are
protected differently and deliberately:

  - A real local run must keep alerting. The operator ruled that a genuine job
    failure reaching the admin chat is wanted -- they are the only person in that
    channel -- so the product default stays false (`config.get_disable_telegram_admin`).
  - The test suite must never reach it. `load_dotenv()` at config import supplies
    this worktree's live credentials, so a test that forgets to stub the admin
    send would post for real. That happened once already while this suite was
    being written.

Discipline is not a control: today every admin-send test stubs its transport, and
one future test that forgets is all it takes. `setdefault`, not an assignment, so
someone deliberately exporting the variable still wins -- and `monkeypatch.setenv`
or `delenv` in a test overrides it per-test, which is how the tests that exercise
the delivered path keep working.
"""
import os

os.environ.setdefault('DISABLE_TELEGRAM_ADMIN', 'true')
