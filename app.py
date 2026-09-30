import os
import re
import ssl
from urllib.parse import urlsplit, urlunsplit, parse_qsl, urlencode

import cloudinary
import cloudinary.uploader
from datetime import datetime, timedelta

from flask import Flask, render_template, request, redirect, url_for, flash, session
from dotenv import load_dotenv
from authlib.integrations.flask_client import OAuth
from markupsafe import escape

from sqlalchemy import create_engine, select, func
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, sessionmaker
from sqlalchemy import Integer, String, Text, DateTime


load_dotenv()


app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "super_secret_key_change_in_production")

# GDGC CHANGE: make this configurable so it can be False for local HTTP
# and True for the deployed HTTPS application.
app.config['SESSION_COOKIE_SECURE'] = (
    os.environ.get("SESSION_COOKIE_SECURE", "false").lower() == "true"
)
app.config['SESSION_COOKIE_HTTPONLY'] = True
app.config['SESSION_COOKIE_SAMESITE'] = 'Lax'
app.permanent_session_lifetime = timedelta(hours=2)


# Cloudinary Setup
cloudinary.config(
    cloud_name=os.environ.get("CLOUD_NAME"),
    api_key=os.environ.get("API_KEY"),
    api_secret=os.environ.get("API_SECRET")
)


# Google OAuth Setup
oauth = OAuth(app)
google = oauth.register(
    name='google',
    client_id=os.environ.get('GOOGLE_CLIENT_ID'),
    client_secret=os.environ.get('GOOGLE_CLIENT_SECRET'),
    server_metadata_url='https://accounts.google.com/.well-known/openid-configuration',
    client_kwargs={'scope': 'openid email profile'}
)


# ---------------------------------------------------------------------------
# Database Setup
# GDGC CHANGE: SQLAlchemy ORM + connection pooling.
# ---------------------------------------------------------------------------

class Base(DeclarativeBase):
    pass


class Item(Base):
    __tablename__ = "items"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    report_type: Mapped[str] = mapped_column(Text, nullable=False)
    item_name: Mapped[str] = mapped_column(Text, nullable=False)
    category: Mapped[str] = mapped_column(Text, nullable=False)
    location: Mapped[str] = mapped_column(Text, nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    phone_number: Mapped[str | None] = mapped_column(Text, nullable=True)
    who_has_it: Mapped[str | None] = mapped_column(Text, nullable=True)
    photo_path: Mapped[str | None] = mapped_column(Text, nullable=True)
    user_email: Mapped[str] = mapped_column(Text, nullable=False)
    user_name: Mapped[str] = mapped_column(Text, nullable=False)
    reported_date: Mapped[datetime | None] = mapped_column(
        DateTime,
        server_default=func.current_timestamp()
    )
    status: Mapped[str | None] = mapped_column(
        Text,
        server_default="Active"
    )


class Feedback(Base):
    __tablename__ = "feedback"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_email: Mapped[str] = mapped_column(Text, nullable=False)
    user_name: Mapped[str] = mapped_column(Text, nullable=False)
    category: Mapped[str] = mapped_column(Text, nullable=False)
    rating: Mapped[int | None] = mapped_column(Integer, nullable=True)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    message: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str | None] = mapped_column(
        Text,
        server_default="Open"
    )
    created_at: Mapped[datetime | None] = mapped_column(
        DateTime,
        server_default=func.current_timestamp()
    )


def database_url():
    db_url = os.environ.get("DATABASE_URL")

    if not db_url:
        raise ValueError("DATABASE_URL environment variable is missing.")

    # Keep the existing DATABASE_URL format compatible with SQLAlchemy/pg8000.
    if db_url.startswith("postgres://"):
        db_url = "postgresql+pg8000://" + db_url[len("postgres://"):]
    elif db_url.startswith("postgresql://"):
        db_url = "postgresql+pg8000://" + db_url[len("postgresql://"):]

    # pg8000 does not accept libpq-specific URL parameters such as
    # sslmode/channel_binding. SSL is configured below using ssl_context.
    parts = urlsplit(db_url)
    query_params = [
        (key, value)
        for key, value in parse_qsl(parts.query, keep_blank_values=True)
        if key.lower() not in {"sslmode", "channel_binding"}
    ]

    return urlunsplit((
        parts.scheme,
        parts.netloc,
        parts.path,
        urlencode(query_params),
        parts.fragment,
    ))


DATABASE_ENGINE = create_engine(
    database_url(),
    pool_size=int(os.environ.get("DB_POOL_SIZE", "2")),
    max_overflow=int(os.environ.get("DB_MAX_OVERFLOW", "3")),
    pool_timeout=10,
    pool_recycle=1800,
    pool_pre_ping=True,
    connect_args={"ssl_context": ssl.create_default_context()},
)

SessionLocal = sessionmaker(
    bind=DATABASE_ENGINE,
    autoflush=False,
    autocommit=False,
    expire_on_commit=False,
)


def init_db():
    # SQLAlchemy creates the existing tables only if they do not already exist.
    # It does not delete or replace existing data.
    Base.metadata.create_all(DATABASE_ENGINE)


if os.environ.get("DATABASE_URL"):
    try:
        init_db()
    except Exception as e:
        print("Database initialization skipped or failed:", e)


# ---------------------------------------------------------------------------
# Existing request/session behaviour
# ---------------------------------------------------------------------------

@app.before_request
def make_session_permanent():
    session.permanent = True


# Enforce login for the entire app except auth routes
@app.before_request
def require_login():
    allowed_routes = ['landing', 'login', 'authorize', 'static']
    if request.endpoint not in allowed_routes and 'user' not in session:
        return redirect(url_for('landing'))


@app.route('/landing')
def landing():
    if 'user' in session:
        return redirect(url_for('index'))
    return render_template('login.html')


@app.route('/login')
def login():
    redirect_uri = url_for('authorize', _external=True)
    return google.authorize_redirect(redirect_uri, prompt='select_account')


@app.route('/authorize')
def authorize():
    token = google.authorize_access_token()
    resp = google.get('https://www.googleapis.com/oauth2/v1/userinfo')
    user_info = resp.json()

    email = user_info.get('email', '')

    session['user'] = {
        'email': email,
        'name': user_info.get('name', 'Campus User')
    }

    flash('Successfully logged in with Google!', 'success')
    return redirect(url_for('index'))


@app.route('/logout')
def logout():
    session.clear()
    flash('Logged out successfully.', 'success')
    return redirect(url_for('landing'))


@app.route('/')
def index():
    with SessionLocal() as db:
        total_registered = db.scalar(
            select(func.count()).select_from(Item)
        ) or 0

        total_reunited = db.scalar(
            select(func.count()).select_from(Item).where(
                Item.status == 'Resolved'
            )
        ) or 0

        median_speed = "4.2 Hours"

        items = db.scalars(
            select(Item)
            .where(Item.status == 'Active')
            .order_by(Item.id.desc())
        ).all()

    lost_items = []
    found_items = []
    current_time = datetime.now()

    for item in items:
        reported_date = item.reported_date

        if isinstance(reported_date, str):
            try:
                reported_date = datetime.strptime(
                    reported_date,
                    '%Y-%m-%d %H:%M:%S'
                )
            except ValueError:
                reported_date = datetime.strptime(
                    reported_date.split('.')[0],
                    '%Y-%m-%d %H:%M:%S'
                )

        days_passed = (current_time - reported_date).days
        action_required = days_passed > 30

        item_dict = {
            'id': item.id,
            'report_type': item.report_type,
            'item_name': item.item_name,
            'category': item.category,
            'location': item.location,
            'description': item.description,
            'phone_number': item.phone_number,
            'who_has_it': item.who_has_it,
            'photo_path': item.photo_path,
            'user_email': item.user_email,
            'user_name': item.user_name,
            'reported_date': item.reported_date,
            'status': item.status,
            'days_passed': days_passed,
            'action_required': action_required,
        }

        if item.report_type == 'Lost':
            lost_items.append(item_dict)
        elif item.report_type == 'Found':
            found_items.append(item_dict)

    form_data = session.pop('form_data', {})

    return render_template(
        'index.html',
        lost_items=lost_items,
        found_items=found_items,
        form_data=form_data,
        total_registered=total_registered,
        total_reunited=total_reunited,
        median_speed=median_speed
    )


@app.route('/feedback', methods=['GET', 'POST'])
def feedback():
    if 'user' not in session:
        return redirect(url_for('landing'))

    allowed_categories = {
        'General',
        'Bug / Problem',
        'Feature Request',
        'UI / UX',
        'Success Story'
    }

    if request.method == 'POST':
        category = request.form.get('category', '').strip()
        title = escape(request.form.get('title', '').strip())
        message = escape(request.form.get('message', '').strip())
        rating_raw = request.form.get('rating', '').strip()

        rating = None

        if rating_raw:
            try:
                rating = int(rating_raw)
            except ValueError:
                rating = None

        if category not in allowed_categories:
            flash('Please select a valid feedback category.', 'error')
            return redirect(url_for('feedback'))

        if not title or len(title) > 120:
            flash(
                'Please provide a short feedback title (maximum 120 characters).',
                'error'
            )
            return redirect(url_for('feedback'))

        if not message or len(message) > 2000:
            flash(
                'Please provide feedback between 1 and 2000 characters.',
                'error'
            )
            return redirect(url_for('feedback'))

        if rating is not None and rating not in range(1, 6):
            flash('Rating must be between 1 and 5.', 'error')
            return redirect(url_for('feedback'))

        with SessionLocal() as db:
            feedback_entry = Feedback(
                user_email=session['user']['email'],
                user_name=session['user']['name'],
                category=category,
                rating=rating,
                title=title,
                message=message
            )

            db.add(feedback_entry)
            db.commit()

        flash(
            'Thanks! Your feedback has been added to the community feedback board.',
            'success'
        )
        return redirect(url_for('feedback'))

    selected_category = request.args.get('category', 'All').strip()
    selected_sort = request.args.get('sort', 'newest').strip()

    if selected_category not in allowed_categories:
        selected_category = 'All'

    sort_map = {
        'newest': (Feedback.created_at.desc(), Feedback.id.desc()),
        'oldest': (Feedback.created_at.asc(), Feedback.id.asc()),
        'highest': (
            Feedback.rating.desc().nullslast(),
            Feedback.created_at.desc(),
            Feedback.id.desc()
        ),
        'lowest': (
            Feedback.rating.asc().nullslast(),
            Feedback.created_at.desc(),
            Feedback.id.desc()
        )
    }

    if selected_sort not in sort_map:
        selected_sort = 'newest'

    with SessionLocal() as db:
        feedback_query = select(Feedback)

        if selected_category != 'All':
            feedback_query = feedback_query.where(
                Feedback.category == selected_category
            )

        feedback_query = feedback_query.order_by(*sort_map[selected_sort])

        feedback_items_db = db.scalars(feedback_query).all()

        total_feedback = db.scalar(
            select(func.count()).select_from(Feedback)
        ) or 0

        bug_count = db.scalar(
            select(func.count()).select_from(Feedback).where(
                Feedback.category == 'Bug / Problem'
            )
        ) or 0

        feature_count = db.scalar(
            select(func.count()).select_from(Feedback).where(
                Feedback.category == 'Feature Request'
            )
        ) or 0

        success_count = db.scalar(
            select(func.count()).select_from(Feedback).where(
                Feedback.category == 'Success Story'
            )
        ) or 0

        avg_rating = db.scalar(
            select(func.avg(Feedback.rating)).where(
                Feedback.rating.is_not(None)
            )
        )

    feedback_items = [
        {
            'id': item.id,
            'user_email': item.user_email,
            'user_name': item.user_name,
            'category': item.category,
            'rating': item.rating,
            'title': item.title,
            'message': item.message,
            'status': item.status,
            'created_at': item.created_at,
        }
        for item in feedback_items_db
    ]

    avg_rating = (
        round(float(avg_rating), 1)
        if avg_rating is not None
        else None
    )

    return render_template(
        'feedback.html',
        feedback_items=feedback_items,
        total_feedback=total_feedback,
        bug_count=bug_count,
        feature_count=feature_count,
        success_count=success_count,
        avg_rating=avg_rating,
        selected_category=selected_category,
        selected_sort=selected_sort
    )


@app.route('/submit', methods=['POST'])
def submit():
    if 'user' not in session:
        return redirect(url_for('landing'))

    form_data = request.form.to_dict()

    report_type = form_data.get('report_type')
    item_name = escape(form_data.get('item_name', '').strip())
    category = form_data.get('category')
    location = escape(form_data.get('location', '').strip())
    description = escape(form_data.get('description', '').strip())
    phone_number = form_data.get('phone_number', '').strip()
    who_has_it = (
        escape(form_data.get('who_has_it', '').strip())
        if report_type == 'Found'
        else None
    )

    has_error = False

    # Validation: Item name can only contain letters (A-Z, a-z) and spaces
    if not item_name or not re.match(r'^[A-Za-z\s]+$', item_name):
        flash(
            'Item name can only contain letters (A-Z and a-z). '
            'No numbers or special characters.',
            'error'
        )
        form_data.pop('item_name', None)
        has_error = True

    if report_type == 'Lost':
        if not description:
            flash('Description is required for lost items.', 'error')
            form_data.pop('description', None)
            has_error = True

        if not phone_number:
            flash('Phone number is required for lost items.', 'error')
            form_data.pop('phone_number', None)
            has_error = True

    elif report_type == 'Found':
        if not location:
            flash('Location is required for found items.', 'error')
            form_data.pop('location', None)
            has_error = True

        if not who_has_it:
            flash(
                'Please specify who currently has the item.',
                'error'
            )
            form_data.pop('who_has_it', None)
            has_error = True

    # Validation: Indian phone number
    if phone_number and not has_error:
        clean_phone = re.sub(r'[\s\-()]', '', phone_number)

        if not re.match(r'^[6-9][0-9]{9}$', clean_phone):
            flash(
                'Invalid Indian phone number. Must be 10 digits starting '
                'with 6, 7, 8, or 9.',
                'error'
            )
            form_data.pop('phone_number', None)
            has_error = True

    # Validation: Photo format
    photo_path = None

    if 'photo' in request.files and not has_error:
        file = request.files['photo']

        if file and file.filename != '':
            if not (
                '.' in file.filename
                and file.filename.rsplit('.', 1)[1].lower()
                in {'png', 'jpg', 'jpeg', 'gif'}
            ):
                flash(
                    'Invalid photo format. Only valid image files '
                    '(PNG, JPG, JPEG, GIF) are allowed.',
                    'error'
                )
                has_error = True
            else:
                try:
                    upload_result = cloudinary.uploader.upload(
                        file,
                        resource_type="image"
                    )
                    photo_path = upload_result.get('secure_url')
                except Exception as e:
                    flash(f'Cloudinary Error: {str(e)}', 'error')
                    has_error = True

    if has_error:
        session['form_data'] = form_data
        return redirect(url_for('index'))

    user_email = session['user']['email']
    user_name = session['user']['name']

    with SessionLocal() as db:
        item = Item(
            report_type=report_type,
            item_name=item_name,
            category=category,
            location=location,
            description=description,
            phone_number=phone_number,
            who_has_it=who_has_it,
            photo_path=photo_path,
            user_email=user_email,
            user_name=user_name
        )

        db.add(item)
        db.commit()

    flash('Item reported successfully!', 'success')
    return redirect(url_for('index'))


@app.route('/resolve/<int:item_id>', methods=['POST'])
def resolve(item_id):
    if 'user' not in session:
        return redirect(url_for('landing'))

    action = request.form.get('action')  # 'resolve' or 'delete'
    current_user_email = session['user']['email']

    with SessionLocal() as db:
        item = db.scalar(
            select(Item).where(Item.id == item_id)
        )

        if not item:
            flash('Item not found.', 'error')
            return redirect(url_for('index'))

        report_type = item.report_type
        owner_email = item.user_email
        owner_name = item.user_name

        # Permission Rule: Only reporting person can remove 'Lost' items.
        if report_type == 'Lost':
            if current_user_email != owner_email:
                flash(
                    'Permission denied. Only the person who posted this lost '
                    'item can remove it.',
                    'error'
                )
                return redirect(url_for('index'))

            if action == 'resolve':
                item.status = 'Resolved'
            elif action == 'delete':
                db.delete(item)

        elif report_type == 'Found':
            # Found items: preserve the existing behaviour for another user.
            # Email/notification delivery is intentionally deferred for now.
            if current_user_email != owner_email:
                flash(
                    'Action triggered! The reporting person has not been notified yet.',
                    'info'
                )
                return redirect(url_for('index'))

            if action == 'resolve':
                item.status = 'Resolved'
            elif action == 'delete':
                db.delete(item)

        db.commit()

    flash('Request processed successfully.', 'success')
    return redirect(url_for('index'))


if __name__ == '__main__':
    app.run(
        host='0.0.0.0',
        port=int(os.environ.get('PORT', 5000)),
        debug=False
    )
