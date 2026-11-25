import re
text = open('state/post_draft_check.txt').read()
visible = re.sub(r'<[^>]+>', '', text)
print('visible', len(visible.strip()))
print('raw', len(text.strip()))
