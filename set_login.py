"""Set the password on one CRM account, from the Railway console.

Run when somebody cannot get in and there is no other way back. It is
deliberately narrow:

  - it touches ONE account, named on the command line, and no other;
  - it never deletes anything;
  - it takes the password from the environment, so no password is ever typed
    into this file, committed, or left in the repository;
  - it writes to the audit log, so the change is on the record like any other.

Usage, in the Railway console for this service:

    NEW_USERNAME=bcowan NEW_PASSWORD='...' python set_login.py

The password is read from the environment and is never printed back.
"""

import os
import sys


def main():
    username = (os.environ.get('NEW_USERNAME') or '').strip()
    password = os.environ.get('NEW_PASSWORD') or ''

    if not username or not password:
        print('Set NEW_USERNAME and NEW_PASSWORD, then run this again.')
        print("  NEW_USERNAME=bcowan NEW_PASSWORD='...' python set_login.py")
        return 1
    if len(password) < 12:
        # A short password on an account that reaches every client record is
        # not worth the trouble of resetting it.
        print('Use a password of at least 12 characters.')
        return 1

    from werkzeug.security import generate_password_hash
    from app import app, db, User, AuditLog

    with app.app_context():
        user = User.query.filter_by(username=username).first()
        created = user is None

        if created:
            # Only ever an admin where there is no account at all to recover.
            user = User(username=username, role='admin')
            db.session.add(user)

        user.password_hash = generate_password_hash(password)
        # An account nobody can sign into is no use. `active` is the column;
        # `is_active` is flask-login's read-only view of it.
        user.active = True

        db.session.add(AuditLog(
            username=username,
            action='password-set' + ('-new-account' if created else ''),
            entity='User', entity_id=str(user.id),
            detail='password set from the server console'))
        db.session.commit()

        others = User.query.filter(User.username != username).count()
        print(f"{'Created' if created else 'Updated'} the account {username!r}.")
        print(f'Role: {user.role}. {others} other account(s) were left alone.')
        print('The password was not printed. Sign in with it, then change it '
              'from Account once you are in.')
    return 0


if __name__ == '__main__':
    sys.exit(main())
