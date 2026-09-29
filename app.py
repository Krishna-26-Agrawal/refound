import os
import re
import secrets
from html import escape as html_escape
import cloudinary
import cloudinary.uploader
from datetime import datetime, timedelta
from flask import Flask, render_template, request, redirect, url_for, flash, session
from dotenv import load_dotenv
from sqlalchemy import create_engine, select, func, desc, asc
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, Session
from sqlalchemy import Integer, Text, DateTime
import resend
from authlib.integrations.flask_client import OAuth

load_dotenv()

app = Flask(__name__)
SECRET_KEY = os.environ.get("SECRET_KEY")
if not SECRET_KEY:
    if os.environ.get("FLASK_ENV", "production").lower() == "development":
        SECRET_KEY = "dev-only-change-me"
    else:
        raise RuntimeError("SECRET_KEY must be set in the production environment.")
app.secret_key = SECRET_KEY

# Secure defaults for production. For local HTTP development only, set
# SESSION_COOKIE_SECURE=false in your local environment.
app.config['SESSION_COOKIE_SECURE'] = os.environ.get("SESSION_COOKIE_SECURE", "true").lower() == "true"
app.config['SESSION_COOKIE_HTTPONLY'] = True
app.config['SESSION_COOKIE_SAMESITE'] = 'Lax'
app.permanent_session_lifetime = timedelta(hours=2)

# Cloudinary Setup[cite: 7]
cloudinary.config(
    cloud_name = os.environ.get("CLOUD_NAME"),
    api_key = os.environ.get("API_KEY"),
    api_secret = os.environ.get("API_SECRET")
)

# Google OAuth Setup[cite: 7]
oauth = OAuth(app)
google = oauth.register(
    name='google',
    client_id=os.environ.get('GOOGLE_CLIENT_ID'),
    client_secret=os.environ.get('GOOGLE_CLIENT_SECRET'),
    server_metadata_url='https://accounts.google.com/.well-known/openid-configuration',
    client_kwargs={'scope': 'openid email profile'}
)

# PostgreSQL + SQLAlchemy ORM
# A single SQLAlchemy Engine is created for the application and maintains a small
# connection pool, so requests can reuse database connections instead of opening
# a brand-new connection for every request.

def database_url():
    db_url = os.environ.get("DATABASE_URL")
    if not db_url:
        raise ValueError("DATABASE_URL environment variable is missing.")
    # Some providers still expose the old postgres:// prefix.
    if db_url.startswith("postgres://"):
        db_url = "postgresql+pg8000://" + db_url[len("postgres://"):]
    elif db_url.startswith("postgresql://") and "+pg8000" not in db_url:
        db_url = db_url.replace("postgresql://", "postgresql+pg8000://", 1)
    return db_url


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
    reported_date: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    status: Mapped[str] = mapped_column(Text, default="Active")


class Feedback(Base):
    __tablename__ = "feedback"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_email: Mapped[str] = mapped_column(Text, nullable=False)
    user_name: Mapped[str] = mapped_column(Text, nullable=False)
    category: Mapped[str] = mapped_column(Text, nullable=False)
    rating: Mapped[int | None] = mapped_column(Integer, nullable=True)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    message: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(Text, default="Open")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


DATABASE_ENGINE = None
if os.environ.get("DATABASE_URL"):
    try:
        DATABASE_ENGINE = create_engine(
            database_url(),
            pool_size=int(os.environ.get("DB_POOL_SIZE", "2")),
            max_overflow=int(os.environ.get("DB_MAX_OVERFLOW", "3")),
            pool_timeout=10,
            pool_recycle=1800,
            pool_pre_ping=True,
            connect_args={"ssl_context": True},
        )
    except Exception as e:
        print("Database engine initialization failed:", e)


def get_db():
    if DATABASE_ENGINE is None:
        raise RuntimeError("Database is not configured. Set DATABASE_URL.")
    return Session(DATABASE_ENGINE)


def init_db():
    if DATABASE_ENGINE is None:
        return
    Base.metadata.create_all(DATABASE_ENGINE)


try:
    init_db()
except Exception as e:
    print("Database initialization skipped or failed:", e)


# Transactional email setup. Email sending is enabled only when both variables
# are configured in the production environment.
RESEND_API_KEY = os.environ.get("RESEND_API_KEY")
RESEND_FROM_EMAIL = os.environ.get("RESEND_FROM_EMAIL")
if RESEND_API_KEY:
    resend.api_key = RESEND_API_KEY


def send_found_item_notification(owner_email, owner_name, item, actor_name, actor_email):
    """Notify the person who posted a Found item that another user interacted with it."""
    if not RESEND_API_KEY or not RESEND_FROM_EMAIL:
        return False, "Email notifications are not configured yet."

    try:
        safe_owner_name = html_escape(owner_name)
        safe_actor_name = html_escape(actor_name)
        safe_actor_email = html_escape(actor_email)
        safe_item_name = html_escape(item.item_name)
        safe_category = html_escape(item.category)
        safe_location = html_escape(item.location)
        params = {
            "from": RESEND_FROM_EMAIL,
            "to": [owner_email],
            "subject": f"ReFound: Someone responded to your found item – {safe_item_name}",
            "html": f"""
                <div style="font-family:Arial,sans-serif;line-height:1.6">
                    <h2>Someone may have information about your found item</h2>
                    <p>Hi {owner_name},</p>
                    <p><strong>{safe_actor_name}</strong> ({safe_actor_email}) interacted with your ReFound listing.</p>
                    <p><strong>Item:</strong> {safe_item_name}<br>
                    <strong>Category:</strong> {safe_category}<br>
                    <strong>Location:</strong> {safe_location}</p>
                    <p>Please contact them to coordinate the hand-off.</p>
                    <p>— ReFound</p>
                </div>
            """,
        }
        resend.Emails.send(params)
        return True, None
    except Exception as e:
        app.logger.exception("Failed to send ReFound notification email")
        return False, str(e)

def get_csrf_token():
    if 'csrf_token' not in session:
        session['csrf_token'] = secrets.token_urlsafe(32)
    return session['csrf_token']


@app.context_processor
def inject_csrf_token():
    return {'csrf_token': get_csrf_token()}


def validate_csrf():
    submitted = request.form.get('csrf_token', '')
    expected = session.get('csrf_token', '')
    if not submitted or not expected or not secrets.compare_digest(submitted, expected):
        flash('Your session security token is invalid. Please try again.', 'error')
        return False
    return True


@app.before_request
def make_session_permanent():
    session.permanent = True

# Enforce login for the entire app except auth routes[cite: 7]
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

@app.after_request
def add_security_headers(response):
    response.headers.setdefault('X-Content-Type-Options', 'nosniff')
    response.headers.setdefault('X-Frame-Options', 'DENY')
    response.headers.setdefault('Referrer-Policy', 'strict-origin-when-cross-origin')
    if app.config['SESSION_COOKIE_SECURE']:
        response.headers.setdefault('Strict-Transport-Security', 'max-age=31536000; includeSubDomains')
    return response


@app.route('/')
def index():
    with get_db() as db:
        total_registered = db.scalar(select(func.count()).select_from(Item)) or 0
        total_reunited = db.scalar(
            select(func.count()).select_from(Item).where(Item.status == 'Resolved')
        ) or 0
        items = db.scalars(
            select(Item).where(Item.status == 'Active').order_by(Item.id.desc())
        ).all()

    median_speed = "4.2 Hours"
    lost_items = []
    found_items = []
    current_time = datetime.now()

    for item in items:
        reported_date = item.reported_date
        if isinstance(reported_date, str):
            try:
                reported_date = datetime.strptime(reported_date, '%Y-%m-%d %H:%M:%S')
            except ValueError:
                reported_date = datetime.strptime(reported_date.split('.')[0], '%Y-%m-%d %H:%M:%S')

        days_passed = (current_time - reported_date).days
        item.days_passed = days_passed
        item.action_required = days_passed > 30

        if item.report_type == 'Lost':
            lost_items.append(item)
        elif item.report_type == 'Found':
            found_items.append(item)

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
    allowed_categories = {
        'General', 'Bug / Problem', 'Feature Request', 'UI / UX', 'Success Story'
    }

    if request.method == 'POST':
        if not validate_csrf():
            return redirect(url_for('feedback'))

        category = request.form.get('category', '').strip()
        title = request.form.get('title', '').strip()
        message = request.form.get('message', '').strip()
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
            flash('Please provide a short feedback title (maximum 120 characters).', 'error')
            return redirect(url_for('feedback'))
        if not message or len(message) > 2000:
            flash('Please provide feedback between 1 and 2000 characters.', 'error')
            return redirect(url_for('feedback'))
        if rating is not None and rating not in range(1, 6):
            flash('Rating must be between 1 and 5.', 'error')
            return redirect(url_for('feedback'))

        feedback_entry = Feedback(
            user_email=session['user']['email'],
            user_name=session['user']['name'],
            category=category,
            rating=rating,
            title=title,
            message=message,
        )
        with get_db() as db:
            db.add(feedback_entry)
            db.commit()

        flash('Thanks! Your feedback has been added to the community feedback board.', 'success')
        return redirect(url_for('feedback'))

    selected_category = request.args.get('category', 'All').strip()
    selected_sort = request.args.get('sort', 'newest').strip()
    if selected_category not in allowed_categories:
        selected_category = 'All'
    if selected_sort not in {'newest', 'oldest', 'highest', 'lowest'}:
        selected_sort = 'newest'

    order_map = {
        'newest': (Feedback.created_at.desc(), Feedback.id.desc()),
        'oldest': (Feedback.created_at.asc(), Feedback.id.asc()),
        'highest': (Feedback.rating.desc().nullslast(), Feedback.created_at.desc(), Feedback.id.desc()),
        'lowest': (Feedback.rating.asc().nullslast(), Feedback.created_at.desc(), Feedback.id.desc()),
    }

    with get_db() as db:
        query = select(Feedback)
        if selected_category != 'All':
            query = query.where(Feedback.category == selected_category)
        query = query.order_by(*order_map[selected_sort])
        feedback_items = db.scalars(query).all()

        total_feedback = db.scalar(select(func.count()).select_from(Feedback)) or 0
        bug_count = db.scalar(select(func.count()).select_from(Feedback).where(Feedback.category == 'Bug / Problem')) or 0
        feature_count = db.scalar(select(func.count()).select_from(Feedback).where(Feedback.category == 'Feature Request')) or 0
        success_count = db.scalar(select(func.count()).select_from(Feedback).where(Feedback.category == 'Success Story')) or 0
        avg_rating = db.scalar(select(func.avg(Feedback.rating)).where(Feedback.rating.is_not(None)))
        avg_rating = round(float(avg_rating), 1) if avg_rating is not None else None

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
    if not validate_csrf():
        return redirect(url_for('index'))

    form_data = request.form.to_dict()
    report_type = form_data.get('report_type')
    item_name = form_data.get('item_name', '').strip()
    category = form_data.get('category')
    location = form_data.get('location', '').strip()
    description = form_data.get('description', '').strip()
    phone_number = form_data.get('phone_number', '').strip()
    who_has_it = form_data.get('who_has_it', '').strip() if report_type == 'Found' else None

    has_error = False
    if not item_name or not re.match(r'^[A-Za-z\s]+$', item_name):
        flash('Item name can only contain letters (A-Z and a-z). No numbers or special characters.', 'error')
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
            flash('Please specify who currently has the item.', 'error')
            form_data.pop('who_has_it', None)
            has_error = True
    else:
        flash('Invalid report type.', 'error')
        has_error = True

    if phone_number and not has_error:
        clean_phone = re.sub(r'[\s\-()]', '', phone_number)
        if not re.match(r'^[6-9][0-9]{9}$', clean_phone):
            flash('Invalid Indian phone number. Must be 10 digits starting with 6, 7, 8, or 9.', 'error')
            form_data.pop('phone_number', None)
            has_error = True

    photo_path = None
    if 'photo' in request.files and not has_error:
        file = request.files['photo']
        if file and file.filename != '':
            allowed_extensions = {'png', 'jpg', 'jpeg', 'gif'}
            extension = file.filename.rsplit('.', 1)[1].lower() if '.' in file.filename else ''
            if extension not in allowed_extensions:
                flash('Invalid photo format. Only PNG, JPG, JPEG and GIF are allowed.', 'error')
                has_error = True
            else:
                try:
                    upload_result = cloudinary.uploader.upload(file, resource_type='image')
                    photo_path = upload_result.get('secure_url')
                except Exception as e:
                    app.logger.exception('Cloudinary upload failed')
                    flash('Photo upload failed. Please try again.', 'error')
                    has_error = True

    if has_error:
        session['form_data'] = form_data
        return redirect(url_for('index'))

    item = Item(
        report_type=report_type,
        item_name=item_name,
        category=category,
        location=location,
        description=description,
        phone_number=phone_number,
        who_has_it=who_has_it,
        photo_path=photo_path,
        user_email=session['user']['email'],
        user_name=session['user']['name'],
    )
    with get_db() as db:
        db.add(item)
        db.commit()

    flash('Item reported successfully!', 'success')
    return redirect(url_for('index'))


@app.route('/resolve/<int:item_id>', methods=['POST'])
def resolve(item_id):
    if not validate_csrf():
        return redirect(url_for('index'))

    action = request.form.get('action')
    current_user_email = session['user']['email']
    current_user_name = session['user']['name']

    if action not in {'resolve', 'delete'}:
        flash('Invalid action.', 'error')
        return redirect(url_for('index'))

    with get_db() as db:
        item = db.get(Item, item_id)
        if not item:
            flash('Item not found.', 'error')
            return redirect(url_for('index'))

        if item.report_type == 'Lost':
            if current_user_email != item.user_email:
                flash('Permission denied. Only the person who posted this lost item can remove it.', 'error')
                return redirect(url_for('index'))

            if action == 'resolve':
                item.status = 'Resolved'
            else:
                db.delete(item)
            db.commit()
            flash('Request processed successfully.', 'success')
            return redirect(url_for('index'))

        # For Found listings, another user is allowed to trigger a notification,
        # but only the original poster can actually resolve/delete the listing.
        if current_user_email != item.user_email:
            if action == 'resolve':
                sent, error = send_found_item_notification(
                    item.user_email,
                    item.user_name,
                    item,
                    current_user_name,
                    current_user_email,
                )
                if sent:
                    flash(f'Notification sent to {item.user_name}.', 'success')
                else:
                    app.logger.warning('Found-item notification not sent: %s', error)
                    flash('The notification could not be sent because email notifications are not configured.', 'error')
            else:
                flash('Only the person who posted this found item can delete it.', 'error')
            return redirect(url_for('index'))

        if action == 'resolve':
            item.status = 'Resolved'
        else:
            db.delete(item)
        db.commit()

    flash('Request processed successfully.', 'success')
    return redirect(url_for('index'))

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=int(os.environ.get('PORT', 5000)), debug=False)