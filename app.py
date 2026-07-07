import hashlib
import json
import os
import re
from datetime import datetime
from functools import wraps

import markdown
from flask import (
    Flask,
    Response,
    flash,
    jsonify,
    redirect,
    render_template,
    request,
    session,
    url_for,
)
from markupsafe import Markup
from werkzeug.security import check_password_hash, generate_password_hash

try:
    import psycopg2
    from psycopg2.extras import Json as PgJson
except ImportError:  # local dev without psycopg2 installed
    psycopg2 = None
    PgJson = None

app = Flask(__name__)
app.secret_key = os.environ.get('SECRET_KEY', 'change-this-to-a-random-secret-key-in-production')
app.config['SESSION_COOKIE_SAMESITE'] = 'Lax'
app.config['SESSION_COOKIE_HTTPONLY'] = True

# Markdown filter for Jinja2
@app.template_filter('markdown')
def markdown_filter(text):
    if text:
        return Markup(markdown.markdown(text, extensions=['extra', 'nl2br']))
    return ''

# Data file paths
DATA_DIR = os.path.join(os.path.dirname(__file__), 'data')
POSTS_FILE = os.path.join(DATA_DIR, 'posts.json')
TAGS_FILE = os.path.join(DATA_DIR, 'tags.json')
SETTINGS_FILE = os.path.join(DATA_DIR, 'settings.json')
COMMENTS_FILE = os.path.join(DATA_DIR, 'comments.json')
SUBSCRIBERS_FILE = os.path.join(DATA_DIR, 'subscribers.json')
MESSAGES_FILE = os.path.join(DATA_DIR, 'messages.json')
TALKS_FILE = os.path.join(DATA_DIR, 'talks.json')

# Admin password hash (use generate_password_hash('your-password') to create a new one)
# Current password: marlene2026!
ADMIN_PASSWORD_HASH = 'scrypt:32768:8:1$hWwBzggvjk2oX3ZA$c088ea24b9f172c15a8b5e9a09a1d672d46c72d06f19ef1d9b25a35fc54afb4d27b9fcce05730ae14a2e5ac9dbee5650aaa113fcc45f4b5ccba1430bf6e69eb6'

# Email hashing salt for subscriber privacy
EMAIL_SALT = os.environ.get('EMAIL_SALT', 'marlene-blog-2026')

# ============== Data Functions ==============

# Postgres-backed persistence in production (DATABASE_URL set by Heroku).
# Falls back to filesystem JSON for local development.
DATABASE_URL = os.environ.get('DATABASE_URL')
if DATABASE_URL and DATABASE_URL.startswith('postgres://'):
    # psycopg2 requires the postgresql:// scheme
    DATABASE_URL = DATABASE_URL.replace('postgres://', 'postgresql://', 1)

USE_DB = bool(DATABASE_URL and psycopg2)

# Files that hold list-shaped data (defaults to [] when missing).
_LIST_KEYS = {'posts.json', 'tags.json', 'comments.json',
              'subscribers.json', 'messages.json', 'talks.json'}

def _db_conn():
    return psycopg2.connect(DATABASE_URL, sslmode='require')

def _init_db():
    """Create kv_store table and seed from JSON files on first run."""
    if not USE_DB:
        return
    with _db_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                CREATE TABLE IF NOT EXISTS kv_store (
                    key TEXT PRIMARY KEY,
                    value JSONB NOT NULL,
                    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                )
            """)
            for fname in ('posts.json', 'tags.json', 'settings.json',
                          'comments.json', 'subscribers.json', 'messages.json',
                          'talks.json'):
                path = os.path.join(DATA_DIR, fname)
                if not os.path.exists(path):
                    continue
                try:
                    with open(path, 'r') as f:
                        seed = json.load(f)
                except (FileNotFoundError, json.JSONDecodeError):
                    continue
                # Only seed if the key doesn't already exist (idempotent across deploys).
                cur.execute(
                    "INSERT INTO kv_store (key, value) VALUES (%s, %s) "
                    "ON CONFLICT (key) DO NOTHING",
                    (fname, PgJson(seed))
                )

def _default_for(key):
    return [] if key in _LIST_KEYS else {}

def load_json(filepath):
    key = os.path.basename(filepath)
    if USE_DB:
        try:
            with _db_conn() as conn:
                with conn.cursor() as cur:
                    cur.execute("SELECT value FROM kv_store WHERE key = %s", (key,))
                    row = cur.fetchone()
                    if row is not None:
                        return row[0]
            return _default_for(key)
        except Exception as e:
            app.logger.exception("load_json DB error for %s: %s", key, e)
            return _default_for(key)
    # Filesystem fallback (local dev)
    try:
        with open(filepath, 'r') as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return _default_for(key)

def save_json(filepath, data):
    key = os.path.basename(filepath)
    if USE_DB:
        with _db_conn() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    INSERT INTO kv_store (key, value, updated_at)
                    VALUES (%s, %s, NOW())
                    ON CONFLICT (key) DO UPDATE
                        SET value = EXCLUDED.value, updated_at = NOW()
                """, (key, PgJson(data)))
        return
    # Filesystem fallback (local dev)
    os.makedirs(os.path.dirname(filepath), exist_ok=True)
    with open(filepath, 'w') as f:
        json.dump(data, f, indent=4)

# Initialize DB schema + seed on import (runs once per worker startup)
try:
    _init_db()
except Exception as e:
    # Don't crash the app if DB is briefly unreachable at boot; routes will retry.
    app.logger.exception("DB init failed at startup: %s", e)

def get_posts():
    return load_json(POSTS_FILE)

def get_published_posts():
    return [p for p in get_posts() if p.get('published', True)]

def get_tags():
    return load_json(TAGS_FILE)

def get_settings():
    return load_json(SETTINGS_FILE)

def get_highlights():
    posts = get_published_posts()
    highlights = [p for p in posts if 'tiny-experiments' in p.get('tags', [])]
    for h in highlights:
        h['display_date'] = format_date(h.get('date', ''))
    return highlights

def get_comments():
    return load_json(COMMENTS_FILE)

def get_comments_for_post(slug):
    comments = get_comments()
    return [c for c in comments if c.get('post_slug') == slug and c.get('visible', True)]

def get_all_comments_for_post(slug):
    """Get all comments including hidden ones (for admin)"""
    comments = get_comments()
    return [c for c in comments if c.get('post_slug') == slug]

def get_subscribers():
    return load_json(SUBSCRIBERS_FILE)

def get_messages():
    return load_json(MESSAGES_FILE)

def get_talks():
    return load_json(TALKS_FILE)

def hash_email(email):
    """Create a one-way hash of email for duplicate checking"""
    return hashlib.sha256((email.lower() + EMAIL_SALT).encode()).hexdigest()

def mask_email(email):
    """Mask email for display: m***e@gmail.com"""
    if '@' not in email:
        return email
    local, domain = email.split('@')
    if len(local) <= 2:
        masked_local = local[0] + '***'
    else:
        masked_local = local[0] + '***' + local[-1]
    return f"{masked_local}@{domain}"

def slugify(text):
    text = text.lower().strip()
    text = re.sub(r'[^\w\s-]', '', text)
    text = re.sub(r'[-\s]+', '-', text)
    return text

def format_date(date_str):
    try:
        dt = datetime.strptime(date_str, '%Y-%m-%d')
        return dt.strftime('%B %d, %Y')
    except:
        return date_str

# ============== Auth Decorator ==============

def admin_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not session.get('admin_logged_in'):
            return redirect(url_for('admin_login'))
        return f(*args, **kwargs)
    return decorated_function

# ============== Public Routes ==============

@app.route('/')
def home():
    posts = get_published_posts()
    # Exclude tiny-experiments from home page
    posts = [p for p in posts if 'tiny-experiments' not in p.get('tags', [])]
    posts.sort(key=lambda x: x.get('date', ''), reverse=True)
    for post in posts:
        post['display_date'] = format_date(post.get('date', ''))
    return render_template('index.html', 
                         posts=posts, 
                         highlights=get_highlights(),
                         tags=get_tags(),
                         settings=get_settings())

@app.route('/experiments')
def experiments():
    posts = get_published_posts()
    # Only show tiny-experiments posts
    posts = [p for p in posts if 'tiny-experiments' in p.get('tags', [])]
    posts.sort(key=lambda x: x.get('date', ''), reverse=True)
    for post in posts:
        post['display_date'] = format_date(post.get('date', ''))
    return render_template('experiments.html', 
                         posts=posts, 
                         highlights=get_highlights(),
                         tags=get_tags(),
                         settings=get_settings())

@app.route('/talks')
def talks():
    talks = get_talks()
    talks.sort(key=lambda t: t.get('date', ''), reverse=True)
    for t in talks:
        t['display_date'] = format_date(t.get('date', ''))
    return render_template('talks.html', talks=talks, tags=get_tags(), settings=get_settings())

@app.route('/post/<slug>')
def post(slug):
    posts = get_published_posts()
    post = next((p for p in posts if p.get('slug') == slug), None)
    if post:
        post['display_date'] = format_date(post.get('date', ''))
        comments = get_comments_for_post(slug)
        for c in comments:
            c['display_date'] = format_date(c.get('date', ''))
        return render_template('post.html', 
                             post=post, 
                             comments=comments,
                             highlights=get_highlights(),
                             settings=get_settings())
    return "Post not found", 404

@app.route('/post/<slug>/comment', methods=['POST'])
def add_comment(slug):
    comments = get_comments()
    new_id = max([c.get('id', 0) for c in comments], default=0) + 1
    
    new_comment = {
        'id': new_id,
        'post_slug': slug,
        'name': request.form.get('name', 'Anonymous'),
        'comment': request.form.get('comment', ''),
        'date': datetime.now().strftime('%Y-%m-%d'),
        'visible': True
    }
    
    comments.append(new_comment)
    save_json(COMMENTS_FILE, comments)
    flash('Comment added!', 'success')
    return redirect(url_for('post', slug=slug))

@app.route('/subscribe', methods=['POST'])
def subscribe():
    subscribers = get_subscribers()
    email = request.form.get('email', '').strip().lower()
    
    if email and '@' in email:
        email_hash = hash_email(email)
        
        # Check if already subscribed using hash
        if not any(s.get('email_hash') == email_hash for s in subscribers):
            subscribers.append({
                'email': email,  # Store real email (only you can see this in admin)
                'email_hash': email_hash,  # For duplicate checking
                'date': datetime.now().strftime('%Y-%m-%d')
            })
            save_json(SUBSCRIBERS_FILE, subscribers)
            flash('Thanks for subscribing!', 'success')
        else:
            flash('You\'re already subscribed!', 'info')
    else:
        flash('Please enter a valid email', 'error')
    
    # Redirect back to the referring page
    return redirect(request.referrer or url_for('home'))

@app.route('/message', methods=['GET', 'POST'])
def send_message():
    if request.method == 'POST':
        messages = get_messages()
        new_id = max([m.get('id', 0) for m in messages], default=0) + 1
        
        new_message = {
            'id': new_id,
            'name': request.form.get('name', 'Anonymous'),
            'email': request.form.get('email', ''),
            'message': request.form.get('message', ''),
            'date': datetime.now().strftime('%Y-%m-%d'),
            'read': False
        }
        
        messages.append(new_message)
        save_json(MESSAGES_FILE, messages)
        flash('Message sent! Thanks for reaching out 💜', 'success')
        return redirect(url_for('home'))
    
    return render_template('message.html', 
                         highlights=get_highlights(),
                         settings=get_settings())

@app.route('/tag/<tag_name>')
def tag(tag_name):
    posts = get_published_posts()
    tagged_posts = [p for p in posts if tag_name in p.get('tags', [])]
    for post in tagged_posts:
        post['display_date'] = format_date(post.get('date', ''))
    return render_template('tag.html', 
                         tag=tag_name, 
                         posts=tagged_posts, 
                         highlights=get_highlights(),
                         tags=get_tags(),
                         settings=get_settings())

@app.route('/about')
def about():
    return render_template('about.html', 
                         highlights=get_highlights(),
                         settings=get_settings())

@app.route('/feed.xml')
def rss_feed():
    posts = get_published_posts()
    # Exclude tiny-experiments from RSS
    posts = [p for p in posts if 'tiny-experiments' not in p.get('tags', [])]
    posts.sort(key=lambda x: x.get('date', ''), reverse=True)
    
    for post in posts:
        post['display_date'] = format_date(post.get('date', ''))
        # Convert date to RFC 822 format for RSS
        try:
            dt = datetime.strptime(post.get('date', ''), '%Y-%m-%d')
            post['rss_date'] = dt.strftime('%a, %d %b %Y 00:00:00 +0000')
        except:
            post['rss_date'] = ''
    
    rss_xml = render_template('feed.xml', posts=posts)
    return Response(rss_xml, mimetype='application/rss+xml')

# ============== Admin Routes ==============

@app.route('/admin/login', methods=['GET', 'POST'])
def admin_login():
    if request.method == 'POST':
        password = request.form.get('password', '')
        if check_password_hash(ADMIN_PASSWORD_HASH, password):
            session['admin_logged_in'] = True
            return redirect(url_for('admin_dashboard'))
        flash('Invalid password', 'error')
    return render_template('admin/login.html')

@app.route('/admin/logout')
def admin_logout():
    session.pop('admin_logged_in', None)
    flash('Logged out successfully', 'success')
    return redirect(url_for('home'))

@app.route('/admin')
@admin_required
def admin_dashboard():
    posts = get_posts()
    posts.sort(key=lambda x: x.get('date', ''), reverse=True)
    for post in posts:
        post['display_date'] = format_date(post.get('date', ''))
    return render_template('admin/dashboard.html', 
                         posts=posts,
                         tags=get_tags(),
                         settings=get_settings())

@app.route('/admin/posts/new', methods=['GET', 'POST'])
@admin_required
def admin_new_post():
    if request.method == 'POST':
        posts = get_posts()
        new_id = max([p.get('id', 0) for p in posts], default=0) + 1
        
        new_post = {
            'id': new_id,
            'title': request.form.get('title', ''),
            'slug': request.form.get('slug') or slugify(request.form.get('title', '')),
            'date': request.form.get('date', datetime.now().strftime('%Y-%m-%d')),
            'content': request.form.get('content', ''),
            'preview': request.form.get('preview', ''),
            'tags': [t.strip() for t in request.form.get('tags', '').split(',') if t.strip()],
            'highlight': request.form.get('highlight') == 'on',
            'published': request.form.get('published') == 'on',
            'placeholder': request.form.get('placeholder') == 'on',
            'seo': {
                'meta_description': request.form.get('meta_description', ''),
                'keywords': [k.strip() for k in request.form.get('keywords', '').split(',') if k.strip()]
            }
        }
        
        posts.append(new_post)
        save_json(POSTS_FILE, posts)
        flash('Post created successfully!', 'success')
        return redirect(url_for('admin_dashboard'))
    
    return render_template('admin/edit_post.html', 
                         post=None, 
                         tags=get_tags(),
                         is_new=True)

@app.route('/admin/posts/<int:post_id>/edit', methods=['GET', 'POST'])
@admin_required
def admin_edit_post(post_id):
    posts = get_posts()
    post = next((p for p in posts if p.get('id') == post_id), None)
    
    if not post:
        flash('Post not found', 'error')
        return redirect(url_for('admin_dashboard'))
    
    if request.method == 'POST':
        post['title'] = request.form.get('title', '')
        post['slug'] = request.form.get('slug') or slugify(request.form.get('title', ''))
        post['date'] = request.form.get('date', '')
        post['content'] = request.form.get('content', '')
        post['preview'] = request.form.get('preview', '')
        post['tags'] = [t.strip() for t in request.form.get('tags', '').split(',') if t.strip()]
        post['highlight'] = request.form.get('highlight') == 'on'
        post['published'] = request.form.get('published') == 'on'
        post['placeholder'] = request.form.get('placeholder') == 'on'
        post['seo'] = {
            'meta_description': request.form.get('meta_description', ''),
            'keywords': [k.strip() for k in request.form.get('keywords', '').split(',') if k.strip()]
        }
        
        save_json(POSTS_FILE, posts)
        flash('Post updated successfully!', 'success')
        return redirect(url_for('admin_dashboard'))
    
    return render_template('admin/edit_post.html', 
                         post=post, 
                         tags=get_tags(),
                         is_new=False)

@app.route('/admin/posts/<int:post_id>/delete', methods=['POST'])
@admin_required
def admin_delete_post(post_id):
    posts = get_posts()
    posts = [p for p in posts if p.get('id') != post_id]
    save_json(POSTS_FILE, posts)
    flash('Post deleted successfully!', 'success')
    return redirect(url_for('admin_dashboard'))

@app.route('/admin/tags', methods=['GET', 'POST'])
@admin_required
def admin_tags():
    if request.method == 'POST':
        tags = get_tags()
        new_tag = {
            'name': slugify(request.form.get('name', '')),
            'description': request.form.get('description', '')
        }
        if new_tag['name'] and not any(t['name'] == new_tag['name'] for t in tags):
            tags.append(new_tag)
            save_json(TAGS_FILE, tags)
            flash('Tag created successfully!', 'success')
        else:
            flash('Tag already exists or invalid name', 'error')
        return redirect(url_for('admin_tags'))
    
    return render_template('admin/tags.html', tags=get_tags())

@app.route('/admin/tags/<tag_name>/delete', methods=['POST'])
@admin_required
def admin_delete_tag(tag_name):
    tags = get_tags()
    tags = [t for t in tags if t.get('name') != tag_name]
    save_json(TAGS_FILE, tags)
    flash('Tag deleted successfully!', 'success')
    return redirect(url_for('admin_tags'))

@app.route('/admin/settings', methods=['GET', 'POST'])
@admin_required
def admin_settings():
    settings = get_settings()
    
    if request.method == 'POST':
        settings['site_name'] = request.form.get('site_name', '')
        settings['site_description'] = request.form.get('site_description', '')
        settings['author'] = request.form.get('author', '')
        settings['domain'] = request.form.get('domain', '')
        settings['social'] = {
            'twitter': request.form.get('twitter', ''),
            'github': request.form.get('github', ''),
            'email': request.form.get('email', '')
        }
        settings['seo'] = {
            'default_keywords': [k.strip() for k in request.form.get('default_keywords', '').split(',') if k.strip()],
            'google_analytics': request.form.get('google_analytics', '')
        }
        
        save_json(SETTINGS_FILE, settings)
        flash('Settings saved successfully!', 'success')
        return redirect(url_for('admin_settings'))
    
    return render_template('admin/settings.html', settings=settings)

@app.route('/admin/comments')
@admin_required
def admin_comments():
    comments = get_comments()
    comments.sort(key=lambda x: x.get('date', ''), reverse=True)
    for c in comments:
        c['display_date'] = format_date(c.get('date', ''))
    return render_template('admin/comments.html', comments=comments)

@app.route('/admin/comments/<int:comment_id>/toggle', methods=['POST'])
@admin_required
def admin_toggle_comment(comment_id):
    comments = get_comments()
    for c in comments:
        if c.get('id') == comment_id:
            c['visible'] = not c.get('visible', True)
            break
    save_json(COMMENTS_FILE, comments)
    flash('Comment visibility updated!', 'success')
    return redirect(url_for('admin_comments'))

@app.route('/admin/comments/<int:comment_id>/delete', methods=['POST'])
@admin_required
def admin_delete_comment(comment_id):
    comments = get_comments()
    comments = [c for c in comments if c.get('id') != comment_id]
    save_json(COMMENTS_FILE, comments)
    flash('Comment deleted!', 'success')
    return redirect(url_for('admin_comments'))

@app.route('/admin/subscribers')
@admin_required
def admin_subscribers():
    subscribers = get_subscribers()
    subscribers.sort(key=lambda x: x.get('date', ''), reverse=True)
    for s in subscribers:
        s['display_date'] = format_date(s.get('date', ''))
    return render_template('admin/subscribers.html', subscribers=subscribers)

@app.route('/admin/messages')
@admin_required
def admin_messages():
    messages = get_messages()
    messages.sort(key=lambda x: x.get('date', ''), reverse=True)
    unread_count = sum(1 for m in messages if not m.get('read', False))
    for m in messages:
        m['display_date'] = format_date(m.get('date', ''))
    return render_template('admin/messages.html', messages=messages, unread_count=unread_count)

@app.route('/admin/messages/<int:message_id>/read', methods=['POST'])
@admin_required
def admin_mark_message_read(message_id):
    messages = get_messages()
    for m in messages:
        if m.get('id') == message_id:
            m['read'] = True
            break
    save_json(MESSAGES_FILE, messages)
    return redirect(url_for('admin_messages'))

@app.route('/admin/messages/<int:message_id>/delete', methods=['POST'])
@admin_required
def admin_delete_message(message_id):
    messages = get_messages()
    messages = [m for m in messages if m.get('id') != message_id]
    save_json(MESSAGES_FILE, messages)
    flash('Message deleted!', 'success')
    return redirect(url_for('admin_messages'))

if __name__ == '__main__':
    app.run(host='127.0.0.1', port=8080, debug=True)
