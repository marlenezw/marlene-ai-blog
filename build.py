#!/usr/bin/env python3
"""
Build script for marlene.ai
Generates static HTML files for GitHub Pages deployment
"""

import json
import os
import shutil
from datetime import datetime

import markdown as md
from jinja2 import Environment, FileSystemLoader
from markupsafe import Markup

# Paths
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
OUTPUT_DIR = os.path.join(BASE_DIR, '_site')
TEMPLATES_DIR = os.path.join(BASE_DIR, 'templates')
STATIC_DIR = os.path.join(BASE_DIR, 'static')
DATA_DIR = os.path.join(BASE_DIR, 'data')

# Load data
def load_json(filename):
    filepath = os.path.join(DATA_DIR, filename)
    try:
        with open(filepath, 'r') as f:
            return json.load(f)
    except:
        return [] if 'posts' in filename or 'tags' in filename else {}

def format_date(date_str):
    try:
        dt = datetime.strptime(date_str, '%Y-%m-%d')
        return dt.strftime('%B %d, %Y')
    except:
        return date_str

def build():
    print("🔨 Building marlene.ai static site...")
    
    # Load data
    posts = load_json('posts.json')
    tags = load_json('tags.json')
    settings = load_json('settings.json')
    
    # Filter published posts and sort by date
    published_posts = [p for p in posts if p.get('published', True)]
    published_posts.sort(key=lambda x: x.get('date', ''), reverse=True)
    
    # Add display dates
    for post in published_posts:
        post['display_date'] = format_date(post.get('date', ''))
    
    # Get highlights (posts tagged with 'tiny-experiments')
    highlights = [p for p in published_posts if 'tiny-experiments' in p.get('tags', [])]
    
    # Clean output directory
    if os.path.exists(OUTPUT_DIR):
        shutil.rmtree(OUTPUT_DIR)
    os.makedirs(OUTPUT_DIR)
    
    # Setup Jinja2
    env = Environment(loader=FileSystemLoader(TEMPLATES_DIR))
    
    # Markdown filter
    def markdown_filter(text):
        if text:
            return Markup(md.markdown(text, extensions=['extra', 'nl2br']))
        return ''
    env.filters['markdown'] = markdown_filter
    
    # Custom url_for function for static builds
    def static_url_for(endpoint, **kwargs):
        if endpoint == 'home':
            return '/index.html'
        elif endpoint == 'about':
            return '/about.html'
        elif endpoint == 'post':
            return f"/post/{kwargs.get('slug', '')}.html"
        elif endpoint == 'tag':
            return f"/tag/{kwargs.get('tag_name', '')}.html"
        elif endpoint == 'static':
            return f"/static/{kwargs.get('filename', '')}"
        return '/'
    
    env.globals['url_for'] = static_url_for
    
    # Build index page
    print("  📄 Building index.html")
    template = env.get_template('index.html')
    html = template.render(
        posts=published_posts,
        highlights=highlights,
        tags=tags,
        settings=settings
    )
    with open(os.path.join(OUTPUT_DIR, 'index.html'), 'w') as f:
        f.write(html)
    
    # Build about page
    print("  📄 Building about.html")
    template = env.get_template('about.html')
    html = template.render(
        highlights=highlights,
        settings=settings
    )
    with open(os.path.join(OUTPUT_DIR, 'about.html'), 'w') as f:
        f.write(html)
    
    # Build post pages
    os.makedirs(os.path.join(OUTPUT_DIR, 'post'), exist_ok=True)
    template = env.get_template('post.html')
    for post in published_posts:
        print(f"  📄 Building post/{post['slug']}.html")
        html = template.render(
            post=post,
            highlights=highlights,
            settings=settings
        )
        with open(os.path.join(OUTPUT_DIR, 'post', f"{post['slug']}.html"), 'w') as f:
            f.write(html)
    
    # Build tag pages
    os.makedirs(os.path.join(OUTPUT_DIR, 'tag'), exist_ok=True)
    template = env.get_template('tag.html')
    all_tags = set()
    for post in published_posts:
        all_tags.update(post.get('tags', []))
    
    for tag_name in all_tags:
        print(f"  📄 Building tag/{tag_name}.html")
        tagged_posts = [p for p in published_posts if tag_name in p.get('tags', [])]
        html = template.render(
            tag=tag_name,
            posts=tagged_posts,
            highlights=highlights,
            tags=tags,
            settings=settings
        )
        with open(os.path.join(OUTPUT_DIR, 'tag', f"{tag_name}.html"), 'w') as f:
            f.write(html)
    
    # Copy static files
    print("  📁 Copying static files")
    shutil.copytree(STATIC_DIR, os.path.join(OUTPUT_DIR, 'static'))
    
    # Create CNAME file for custom domain
    domain = settings.get('domain', '')
    if domain:
        print(f"  🌐 Creating CNAME for {domain}")
        with open(os.path.join(OUTPUT_DIR, 'CNAME'), 'w') as f:
            f.write(domain)
    
    # Create .nojekyll file (tells GitHub Pages not to process with Jekyll)
    with open(os.path.join(OUTPUT_DIR, '.nojekyll'), 'w') as f:
        f.write('')
    
    print(f"\n✅ Build complete! Output in: {OUTPUT_DIR}")
    print(f"   {len(published_posts)} posts, {len(all_tags)} tags")
    print("\n📤 To deploy:")
    print("   1. Push the _site folder to your GitHub repo")
    print("   2. Enable GitHub Pages (Settings > Pages > Source: Deploy from branch)")
    print("   3. Set source to 'main' branch and '/_site' folder")

if __name__ == '__main__':
    build()
