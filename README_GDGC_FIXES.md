# ReFound – GDGC Review Fixes

This package contains the ReFound changes requested after the GDGC technical review.

## Files

- `app.py` – SQLAlchemy ORM + connection pooling, secure cookie configuration, CSRF protection, security headers, and real email notifications.
- `index.html` – existing ReFound page with CSRF tokens and Feedback navigation.
- `feedback.html` – existing community feedback page with CSRF token.
- `requirements.txt` – adds SQLAlchemy and Resend.
- `SECURITY_UPDATE.md` – explanation of the changes and deployment environment variables.

## Deployment

1. Replace the current `app.py`.
2. Replace the current `templates/index.html`.
3. Replace the current `templates/feedback.html`.
4. Install the updated requirements.
5. Add the environment variables described in `SECURITY_UPDATE.md`.
6. Deploy behind HTTPS with `SESSION_COOKIE_SECURE=true`.

The existing PostgreSQL `items` table is preserved. SQLAlchemy's `create_all()` will create the existing `feedback` table if it does not already exist; it does not drop or replace existing data.
