import json
import os
import re
import sys
import requests
from datetime import datetime
from zoneinfo import ZoneInfo
from github import Github

# ---------- Config ----------
BOT_TOKEN = os.environ['BOT_TOKEN']
GH_TOKEN = os.environ['GH_TOKEN']
REPO_NAME = os.environ['REPO']
MESSAGE_ID_INPUT = os.environ.get('MESSAGE_ID', '').strip()

BASE_DIR = 'tg/files'
MAX_FILE_SIZE = 20 * 1024 * 1024    # 20 MB
MAX_TOTAL_SIZE = 400 * 1024 * 1024  # 400 MB
PENDING_FILE = 'pending_messages.json'
PROCESSED_FILE = 'processed_message_ids.json'
TEHRAN_TZ = ZoneInfo("Asia/Tehran")

g = Github(GH_TOKEN)
repo = g.get_repo(REPO_NAME)

# ---------- Load pending messages ----------
if not os.path.exists(PENDING_FILE):
    print("No pending messages file. Exiting.")
    sys.exit(0)

with open(PENDING_FILE) as f:
    pending = json.load(f)

if not pending:
    print("No pending messages.")
    sys.exit(0)

print(f"Loaded {len(pending)} pending messages")

# ---------- Filter by message_id if provided ----------
if MESSAGE_ID_INPUT:
    target_id = int(MESSAGE_ID_INPUT)
    filtered = [m for m in pending if m['message_id'] == target_id]
    if not filtered:
        print(f"Message {target_id} not found in pending list.")
        sys.exit(1)
    pending = filtered
    print(f"Processing single message {target_id}")
else:
    print(f"Processing all {len(pending)} pending messages")

# ---------- Load processed IDs ----------
if os.path.exists(PROCESSED_FILE):
    with open(PROCESSED_FILE) as f:
        processed_ids = json.load(f)
else:
    processed_ids = []
print(f"Already processed {len(processed_ids)} messages")

# ---------- Process each message ----------
processed_count = 0
for msg in pending:
    message_id = msg['message_id']

    if message_id in processed_ids:
        print(f"Message {message_id} already processed, skipping")
        continue

    try:
        # Iran time (UTC+3:30)
        msg_date = datetime.fromtimestamp(msg['date'], tz=TEHRAN_TZ)
        issue_title = msg_date.strftime('%d/%m/%Y')

        # Find or create the daily issue
        issue = None
        for i in repo.get_issues(state='open'):
            if i.title == issue_title:
                issue = i
                break
        if issue is None:
            issue = repo.create_issue(title=issue_title, body="Daily digest – separate comments for each message.")
            print(f"Created issue #{issue.number}: {issue_title}")

        # Check if a comment with this message_id already exists
        comment_marker = f'<!-- msg_{message_id} -->'
        already_commented = False
        comments = issue.get_comments()
        for comment in comments:
            if comment.body and comment_marker in comment.body:
                already_commented = True
                print(f"Message {message_id} already commented on issue #{issue.number}")
                break
        
        if already_commented:
            processed_ids.append(message_id)
            processed_count += 1
            continue

        # Build comment body
        time_str = msg_date.strftime('%H:%M')
        sender = msg.get('from', {})
        sender_name = sender.get('first_name') or sender.get('username') or 'Unknown'
        text = msg.get('text') or msg.get('caption') or ''

        comment_body = f"**{sender_name}** at {time_str}:\n"
        if text:
            comment_body += f"{text}\n"

        # Handle attached file
        file_type = None
        file_info = None
        if 'photo' in msg:
            file_type = 'photo'
            file_info = msg['photo'][-1]
        elif 'document' in msg:
            file_type = 'document'
            file_info = msg['document']
        elif 'video' in msg:
            file_type = 'video'
            file_info = msg['video']
        elif 'audio' in msg:
            file_type = 'audio'
            file_info = msg['audio']
        elif 'voice' in msg:
            file_type = 'voice'
            file_info = msg['voice']
        elif 'video_note' in msg:
            file_type = 'video_note'
            file_info = msg['video_note']
        elif 'sticker' in msg:
            file_type = 'sticker'
            file_info = msg['sticker']

        if file_info and 'file_id' in file_info:
            file_id = file_info['file_id']
            file_unique_id = file_info.get('file_unique_id', file_id)

            tg_resp = requests.get(
                f'https://api.telegram.org/bot{BOT_TOKEN}/getFile',
                params={'file_id': file_id}
            )
            if tg_resp.status_code != 200:
                print(f"Failed to get file info for message {message_id}: {tg_resp.text}")
                comment_body += f"\n⚠️ Failed to retrieve file information\n"
            else:
                file_data = tg_resp.json()['result']
                file_path = file_data['file_path']
                file_size = file_data.get('file_size', 0)
                ext = file_path.split('.')[-1] if '.' in file_path else ''

                if file_size <= MAX_FILE_SIZE:
                    download_url = f'https://api.telegram.org/file/bot{BOT_TOKEN}/{file_path}'
                    safe_name = f"{message_id}_{file_unique_id}.{ext}" if ext else f"{message_id}_{file_unique_id}"
                    local_dir = os.path.join(BASE_DIR, file_type)
                    os.makedirs(local_dir, exist_ok=True)
                    local_path = os.path.join(local_dir, safe_name)

                    with requests.get(download_url, stream=True, timeout=30) as r:
                        r.raise_for_status()
                        with open(local_path, 'wb') as f:
                            for chunk in r.iter_content(chunk_size=8192):
                                f.write(chunk)

                    raw_url = f"https://github.com/{REPO_NAME}/raw/main/{local_path}"
                    comment_body += f"\n[📎 Download {file_type}]({raw_url})"
                    print(f"Downloaded {file_type} for message {message_id}")
                else:
                    direct_link = f'https://api.telegram.org/file/bot{BOT_TOKEN}/{file_path}'
                    comment_body += f"\n[📁 Large {file_type} ({file_size//1024//1024} MB) - Telegram Link]({direct_link})"

        # Append hidden marker to prevent double posting
        comment_body += f"\n\n{comment_marker}"

        # Post the comment
        issue.create_comment(comment_body)
        print(f"Posted comment for message {message_id} on issue #{issue.number}")
        processed_count += 1

        # Mark as processed
        processed_ids.append(message_id)

    except Exception as e:
        print(f"Error processing message {message_id}: {e}")
        import traceback
        traceback.print_exc()
        continue

if processed_count == 0:
    print("No new messages to process")
else:
    print(f"Processed {processed_count} messages")

# ---------- Save updated processed IDs (keep last 10000 to avoid bloat) ----------
if len(processed_ids) > 10000:
    processed_ids = processed_ids[-10000:]
with open(PROCESSED_FILE, 'w') as f:
    json.dump(processed_ids, f)

# ---------- Remove processed messages from pending file ----------
# Re-read the current pending file to get latest state
with open(PENDING_FILE) as f:
    current_pending = json.load(f)

if MESSAGE_ID_INPUT:
    remaining = [m for m in current_pending if m['message_id'] != int(MESSAGE_ID_INPUT)]
else:
    remaining = [m for m in current_pending if m['message_id'] not in processed_ids[-processed_count:]]

with open(PENDING_FILE, 'w') as f:
    json.dump(remaining, f)
print(f"Remaining pending messages: {len(remaining)}")

# ---------- Enforce total file size limit ----------
def get_total_size(directory):
    total = 0
    if not os.path.exists(directory):
        return total
    for dirpath, _, filenames in os.walk(directory):
        for fname in filenames:
            fp = os.path.join(dirpath, fname)
            if os.path.exists(fp):
                total += os.path.getsize(fp)
    return total

def extract_message_id(filename):
    match = re.match(r'^(\d+)_.*', filename)
    return int(match.group(1)) if match else 0

if os.path.exists(BASE_DIR):
    total_size = get_total_size(BASE_DIR)
    print(f"Current files size: {total_size / 1024 / 1024:.2f} MB")
    if total_size > MAX_TOTAL_SIZE:
        files_list = []
        for dirpath, _, filenames in os.walk(BASE_DIR):
            for fname in filenames:
                full = os.path.join(dirpath, fname)
                mid = extract_message_id(fname)
                files_list.append((mid, full))
        files_list.sort(key=lambda x: x[0])   # oldest first

        deleted_count = 0
        for _, full_path in files_list:
            if get_total_size(BASE_DIR) <= MAX_TOTAL_SIZE * 0.9:  # go to 90% to give some buffer
                break
            if os.path.exists(full_path):
                os.remove(full_path)
                deleted_count += 1
                print(f"Deleted old file: {full_path}")
        if deleted_count > 0:
            print(f"Deleted {deleted_count} old files to stay under size limit")

# ---------- Commit and push ----------
import subprocess
try:
    subprocess.run(['git', 'config', 'user.name', 'github-actions'], check=True)
    subprocess.run(['git', 'config', 'user.email', 'actions@github.com'], check=True)
    
    # Only add files that exist
    if os.path.exists(BASE_DIR):
        subprocess.run(['git', 'add', BASE_DIR], check=True)
        subprocess.run(['git', 'add', '-u', BASE_DIR], check=True)
    
    if os.path.exists(PROCESSED_FILE):
        subprocess.run(['git', 'add', PROCESSED_FILE], check=True)
    
    if os.path.exists(PENDING_FILE):
        subprocess.run(['git', 'add', PENDING_FILE], check=True)
    
    # Check if there are changes to commit
    result = subprocess.run(['git', 'status', '--porcelain'], capture_output=True, text=True)
    if result.stdout.strip():
        subprocess.run(['git', 'commit', '-m', f'Processed {processed_count} messages from pending list'], check=True)
        subprocess.run(['git', 'push'], check=True)
        print("Changes committed and pushed")
    else:
        print("No changes to commit")

except subprocess.CalledProcessError as e:
    print(f"Git operation failed: {e}")
    sys.exit(1)

print("✅ Processing complete!")
