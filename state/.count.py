import re
text = open('/home/runner/work/ainews_bot/ainews_bot/state/.post_draft_check.txt', encoding='utf-8').read()
stripped = re.sub(r'<[^>]+>', '', text)
print(len(stripped))
