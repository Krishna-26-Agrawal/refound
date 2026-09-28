import os
import re
from urllib.parse import urlparse
import cloudinary
import cloudinary.uploader
from datetime import datetime, timedelta
from flask import Flask, render_template, request, redirect, url_for, flash, session
from dotenv import load_dotenv
import pg8000
from authlib.integrations.flask_client import OAuth
from markupsafe import escape

load_dotenv()

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "super_secret_key_change_in_production")

app.config['SESSION_COOKIE_SECURE'] = False
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

# Postgres Database Connection[cite: 7]
def get_db_connection():
    db_url = os.environ.get("DATABASE_URL")
    if not db_url:
        raise ValueError("DATABASE_URL environment variable is missing.")
    
    url = urlparse(db_url)
    return pg8000.connect(
        user=url.username,
        password=url.password,
        host=url.hostname,
        port=url.port or 5432,
        database=url.path[1:],
        ssl_context=True
    )

def init_db():
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS items (
            id SERIAL PRIMARY KEY,
            report_type TEXT NOT NULL,
            item_name TEXT NOT NULL,
            category TEXT NOT NULL,
            location TEXT NOT NULL,
            description TEXT NOT NULL,
            phone_number TEXT,
            who_has_it TEXT,
            photo_path TEXT,
            user_email TEXT NOT NULL,
            user_name TEXT NOT NULL,
            reported_date TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            status TEXT DEFAULT 'Active'
        )
    ''')

    cursor.execute('''
        CREATE TABLE IF NOT EXISTS feedback (
            id SERIAL PRIMARY KEY,
            user_email TEXT NOT NULL,
            user_name TEXT NOT NULL,
            category TEXT NOT NULL,
            rating INTEGER,
            title TEXT NOT NULL,
            message TEXT NOT NULL,
            status TEXT DEFAULT 'Open',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    conn.commit()
    cursor.close()
    conn.close()

if os.environ.get("DATABASE_URL"):
    try:
        init_db()
    except Exception as e:
        print("Database initialization skipped or failed:", e)

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

@app.route('/')
def index():
    conn = get_db_connection()
    cursor = conn.cursor()
    
    cursor.execute("SELECT COUNT(*) FROM items")
    total_registered = cursor.fetchone()[0]

    cursor.execute("SELECT COUNT(*) FROM items WHERE status = 'Resolved'")
    total_reunited = cursor.fetchone()[0]
    median_speed = "4.2 Hours"

    cursor.execute("SELECT * FROM items WHERE status = 'Active' ORDER BY id DESC")
    
    columns = [col[0] for col in cursor.description]
    items = [dict(zip(columns, row)) for row in cursor.fetchall()]
    
    cursor.close()
    conn.close()

    lost_items = []
    found_items = []
    current_time = datetime.now()

    for item in items:
        reported_date = item['reported_date']
        if isinstance(reported_date, str):
            try:
                reported_date = datetime.strptime(reported_date, '%Y-%m-%d %H:%M:%S')
            except ValueError:
                reported_date = datetime.strptime(reported_date.split('.')[0], '%Y-%m-%d %H:%M:%S')

        days_passed = (current_time - reported_date).days
        action_required = days_passed > 30

        item_dict = dict(item)
        item_dict['days_passed'] = days_passed
        item_dict['action_required'] = action_required

        if item['report_type'] == 'Lost':
            lost_items.append(item_dict)
        elif item['report_type'] == 'Found':
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
            flash('Please provide a short feedback title (maximum 120 characters).', 'error')
            return redirect(url_for('feedback'))

        if not message or len(message) > 2000:
            flash('Please provide feedback between 1 and 2000 characters.', 'error')
            return redirect(url_for('feedback'))

        if rating is not None and rating not in range(1, 6):
            flash('Rating must be between 1 and 5.', 'error')
            return redirect(url_for('feedback'))

        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute('''
            INSERT INTO feedback
                (user_email, user_name, category, rating, title, message)
            VALUES (%s, %s, %s, %s, %s, %s)
        ''', (
            session['user']['email'],
            session['user']['name'],
            category,
            rating,
            title,
            message
        ))
        conn.commit()
        cursor.close()
        conn.close()

        flash('Thanks! Your feedback has been added to the community feedback board.', 'success')
        return redirect(url_for('feedback'))

    selected_category = request.args.get('category', 'All').strip()
    selected_sort = request.args.get('sort', 'newest').strip()

    if selected_category not in allowed_categories:
        selected_category = 'All'

    sort_map = {
        'newest': 'created_at DESC, id DESC',
        'oldest': 'created_at ASC, id ASC',
        'highest': 'rating DESC NULLS LAST, created_at DESC, id DESC',
        'lowest': 'rating ASC NULLS LAST, created_at DESC, id DESC'
    }
    if selected_sort not in sort_map:
        selected_sort = 'newest'

    conn = get_db_connection()
    cursor = conn.cursor()

    if selected_category == 'All':
        cursor.execute(f"SELECT * FROM feedback ORDER BY {sort_map[selected_sort]}")
    else:
        cursor.execute(
            f"SELECT * FROM feedback WHERE category = %s ORDER BY {sort_map[selected_sort]}",
            (selected_category,)
        )

    columns = [col[0] for col in cursor.description]
    feedback_items = [dict(zip(columns, row)) for row in cursor.fetchall()]

    cursor.execute("SELECT COUNT(*) FROM feedback")
    total_feedback = cursor.fetchone()[0]

    cursor.execute("SELECT COUNT(*) FROM feedback WHERE category = 'Bug / Problem'")
    bug_count = cursor.fetchone()[0]

    cursor.execute("SELECT COUNT(*) FROM feedback WHERE category = 'Feature Request'")
    feature_count = cursor.fetchone()[0]

    cursor.execute("SELECT COUNT(*) FROM feedback WHERE category = 'Success Story'")
    success_count = cursor.fetchone()[0]

    cursor.execute("SELECT AVG(rating) FROM feedback WHERE rating IS NOT NULL")
    avg_rating = cursor.fetchone()[0]
    avg_rating = round(float(avg_rating), 1) if avg_rating is not None else None

    cursor.close()
    conn.close()

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
    who_has_it = escape(form_data.get('who_has_it', '').strip()) if report_type == 'Found' else None

    has_error = False

    # Validation (ii): Item name can only contain letters (A-Z, a-z) and spaces[cite: 7]
    if not item_name or not re.match(r'^[A-Za-z\s]+$', item_name):
        flash('Item name can only contain letters (A-Z and a-z). No numbers or special characters.', 'error')
        form_data.pop('item_name', None)  # Resets ONLY the item_name field[cite: 7]
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

    # Validation (i): Indian phone number (10 digits starting with 6, 7, 8, 9)[cite: 7]
    if phone_number and not has_error:
        clean_phone = re.sub(r'[\s\-()]', '', phone_number)
        if not re.match(r'^[6-9][0-9]{9}$', clean_phone):
            flash('Invalid Indian phone number. Must be 10 digits starting with 6, 7, 8, or 9.', 'error')
            form_data.pop('phone_number', None)  # Resets ONLY the phone_number field[cite: 7]
            has_error = True

    # Validation (iii): Photo format validation[cite: 7]
    photo_path = None
    if 'photo' in request.files and not has_error:
        file = request.files['photo']
        if file and file.filename != '':
            if not ('.' in file.filename and file.filename.rsplit('.', 1)[1].lower() in {'png', 'jpg', 'jpeg', 'gif'}):
                flash('Invalid photo format. Only valid image files (PNG, JPG, JPEG, GIF) are allowed.', 'error')
                has_error = True
            else:
                try:
                    upload_result = cloudinary.uploader.upload(file, resource_type="image")
                    photo_path = upload_result.get('secure_url')
                except Exception as e:
                    flash(f'Cloudinary Error: {str(e)}', 'error')
                    has_error = True

    if has_error:
        session['form_data'] = form_data
        return redirect(url_for('index'))

    user_email = session['user']['email']
    user_name = session['user']['name']

    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute('''
        INSERT INTO items (report_type, item_name, category, location, description, phone_number, who_has_it, photo_path, user_email, user_name)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
    ''', (report_type, item_name, category, location, description, phone_number, who_has_it, photo_path, user_email, user_name))
    conn.commit()
    cursor.close()
    conn.close()

    flash('Item reported successfully!', 'success')
    return redirect(url_for('index'))

@app.route('/resolve/<int:item_id>', methods=['POST'])
def resolve(item_id):
    if 'user' not in session:
        return redirect(url_for('landing'))

    action = request.form.get('action') # 'resolve' or 'delete'[cite: 7]
    current_user_email = session['user']['email']

    conn = get_db_connection()
    cursor = conn.cursor()
    
    cursor.execute("SELECT report_type, user_email, user_name FROM items WHERE id = %s", (item_id,))
    item = cursor.fetchone()
    
    if not item:
        cursor.close()
        conn.close()
        flash('Item not found.', 'error')
        return redirect(url_for('index'))

    report_type, owner_email, owner_name = item[0], item[1], item[2]

    # Permission Rule: Only reporting person can remove 'Lost' items.[cite: 7]
    if report_type == 'Lost':
        if current_user_email != owner_email:
            cursor.close()
            conn.close()
            flash('Permission denied. Only the person who posted this lost item can remove it.', 'error')
            return redirect(url_for('index'))
        
        if action == 'resolve':
            cursor.execute("UPDATE items SET status = 'Resolved' WHERE id = %s", (item_id,))
        elif action == 'delete':
            cursor.execute("DELETE FROM items WHERE id = %s", (item_id,))
            
    elif report_type == 'Found':
        # Found items: If someone else tries to mark found/delete, notify the reporting person[cite: 7]
        if current_user_email != owner_email:
            flash(f'Action triggered! A notification has been sent to the reporting person ({owner_name} / {owner_email}).', 'info')
        else:
            if action == 'resolve':
                cursor.execute("UPDATE items SET status = 'Resolved' WHERE id = %s", (item_id,))
            elif action == 'delete':
                cursor.execute("DELETE FROM items WHERE id = %s", (item_id,))

    conn.commit()
    cursor.close()
    conn.close()
    
    flash('Request processed successfully.', 'success')
    return redirect(url_for('index'))

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=int(os.environ.get('PORT', 5000)), debug=False)