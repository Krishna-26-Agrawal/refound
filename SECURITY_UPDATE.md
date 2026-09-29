# ReFound – GDGC feedback fixes

This update addresses the technical observations from GDGC.

## 1. Database / ORM / connection pooling

The previous version created a new `pg8000` connection inside each route. The new version uses SQLAlchemy ORM with one application-level `Engine` and a small connection pool.

- `pool_size=2`
- `max_overflow=3`
- `pool_pre_ping=True`
- `pool_recycle=1800`
- `pool_timeout=10`

The existing `items` table is mapped to the `Item` ORM model, and the feedback table is mapped to `Feedback`.

The existing SQL used parameterized `%s` placeholders, so it was not simply vulnerable to SQL injection as written. Moving to ORM removes the remaining hand-written SQL from application routes and makes query construction safer and easier to maintain.

## 2. Secure production cookies

`SESSION_COOKIE_SECURE` now defaults to `true`. Production should therefore run behind HTTPS.

For local HTTP development only, set:

```text
SESSION_COOKIE_SECURE=false
FLASK_ENV=development
```

The app also keeps:

- `SESSION_COOKIE_HTTPONLY=true`
- `SESSION_COOKIE_SAMESITE=Lax`

Additional response headers are added for clickjacking, MIME-sniffing and referrer protection.

## 3. Real email notification

The old `/resolve/<item_id>` route displayed a message claiming that a notification had been sent even though no email service existed.

The new implementation uses Resend's Python SDK. When another logged-in user interacts with a Found listing, the original poster receives an email if the email configuration is present.

Required environment variables:

```text
RESEND_API_KEY=re_...
RESEND_FROM_EMAIL=notifications@your-verified-domain.com
```

If these are not configured, the app no longer falsely claims an email was sent. It shows an error message and logs the reason.

## 4. CSRF protection

A session-backed CSRF token is now required for the state-changing POST requests:

- submit report
- submit feedback
- resolve/delete item

This is an additional security hardening measure.

## 5. Environment configuration

Production must provide:

```text
SECRET_KEY=<long-random-secret>
DATABASE_URL=<postgresql-url>
GOOGLE_CLIENT_ID=<...>
GOOGLE_CLIENT_SECRET=<...>
CLOUD_NAME=<...>
API_KEY=<...>
API_SECRET=<...>
RESEND_API_KEY=<...>
RESEND_FROM_EMAIL=<verified-sender>
SESSION_COOKIE_SECURE=true
DB_POOL_SIZE=2
DB_MAX_OVERFLOW=3
```

Do not commit `.env` or any API keys to GitHub.

## 6. Dependency changes

`requirements.txt` now includes:

- `SQLAlchemy==2.1.1`
- `resend==2.48.0`

`pg8000` remains because SQLAlchemy uses it as the PostgreSQL DBAPI driver.
